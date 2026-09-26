"""The production analysis (bundle, smart money, cross-check, score) run on point-in-time data."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from app.config import ScoringConfig
from app.nansen.models import TGMDexTrade, TGMHistoricalDexTrade, TGMHolder
from app.pipeline.bundle import (
    BundleAnalysis,
    Funder,
    early_buys,
    find_clusters,
    measure_bundle,
)
from app.pipeline.crosscheck import sm_in_bundle
from app.pipeline.scoring import ScoreInputs, ScoreResult, score_token
from app.pipeline.smart_money import SmartMoneyMetrics, compute_smart_money_metrics

# Holder count and liquidity do not exist point-in-time, so those components are left out.
UNAVAILABLE = frozenset({"volume_liquidity", "holder_growth"})


def to_tgm_trade(trade: TGMHistoricalDexTrade, token_address: str) -> TGMDexTrade:
    """The historical response has no token addresses; the pipeline types expect them."""
    return TGMDexTrade(
        block_timestamp=trade.block_timestamp,
        transaction_hash=trade.transaction_hash,
        trader_address=trade.trader_address,
        trader_address_label=trade.trader_address_label,
        action=trade.action,
        token_address=token_address,
        token_name=trade.token_name,
        token_amount=trade.token_amount,
        traded_token_address="",
        traded_token_name=trade.traded_token_name,
        traded_token_amount=trade.traded_token_amount,
        estimated_swap_price_usd=trade.estimated_swap_price_usd,
        estimated_value_usd=trade.estimated_value_usd,
    )


def net_positions(trades: list[TGMDexTrade]) -> dict[str, float]:
    """Tokens each wallet holds according to the trades: buys minus sells, never below zero.

    A wallet with no trades in the window counts as holding nothing.
    """
    net: dict[str, float] = defaultdict(float)
    for trade in trades:
        net[trade.trader_address] += (
            trade.token_amount if trade.action == "BUY" else -trade.token_amount
        )
    return {wallet: max(amount, 0.0) for wallet, amount in net.items()}


def holders_from_trades(
    trades: list[TGMDexTrade], wallets: frozenset[str], total_supply: float
) -> list[TGMHolder]:
    """Balances at the last trade, in the shape tgm/holders returns (a 0-1 ownership fraction)."""
    positions = net_positions(trades)
    return [
        TGMHolder(
            address=wallet,
            token_amount=positions.get(wallet, 0.0),
            ownership_percentage=positions.get(wallet, 0.0) / total_supply,
        )
        for wallet in wallets
    ]


class Analysis(BaseModel):
    score: ScoreResult
    bundle: BundleAnalysis
    smart_money: SmartMoneyMetrics
    smart_wallets_in_bundle: frozenset[str]


@dataclass
class PointInTimeData:
    launch_at: datetime
    decision_at: datetime
    trades: list[TGMDexTrade]
    tiers: dict[str, frozenset[str]]
    funders: dict[str, Funder | None]
    total_supply: float


def analyze_at(data: PointInTimeData, cfg: ScoringConfig) -> Analysis:
    buys = early_buys(data.trades, data.launch_at, cfg.bundle)
    clusters = find_clusters(buys, data.funders, None, cfg.bundle)
    bundled = frozenset(w for cluster in clusters for w in cluster.wallets)
    holders = holders_from_trades(data.trades, bundled, data.total_supply)
    bundle = measure_bundle(clusters, buys, holders, cfg.bundle)

    smart_wallets = frozenset(data.tiers)
    smart_trades = [t for t in data.trades if t.trader_address in smart_wallets]
    smart = compute_smart_money_metrics(
        smart_trades,
        cfg.smart_money,
        deployed_at=data.launch_at,
        now=data.decision_at,
        circulating_supply=data.total_supply,
        total_holders=None,
        tiers_by_wallet=data.tiers,
    )
    overlap = sm_in_bundle(smart.smart_wallets, bundle)
    score = score_token(
        ScoreInputs(
            smart_money=smart,
            bundle=bundle,
            smart_wallets_in_bundle=overlap,
            volume_usd=None,
            liquidity_usd=None,
            token_age_hours=None,
        ),
        cfg.scoring,
        unavailable=UNAVAILABLE,
    )
    return Analysis(score=score, bundle=bundle, smart_money=smart, smart_wallets_in_bundle=overlap)


def bundle_exit_fraction(
    bundled: frozenset[str],
    holdings_at_decision: dict[str, float],
    later_trades: list[TGMDexTrade],
) -> float | None:
    """Share of the bundle's holdings at the decision that it sold afterwards (outcome only)."""
    held = sum(holdings_at_decision.get(w, 0.0) for w in bundled)
    if held <= 0:
        return None
    sold = sum(
        t.token_amount for t in later_trades if t.action == "SELL" and t.trader_address in bundled
    )
    return min(sold / held, 1.0)
