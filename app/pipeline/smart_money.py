import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.config import SmartMoneyConfig
from app.nansen.client import NansenClient
from app.nansen.endpoints import tgm_dex_trades, tgm_token_information
from app.nansen.exceptions import NansenAPIError
from app.nansen.models import (
    DateRange,
    PaginationRequest,
    SortOrder,
    TGMDexTrade,
    TGMDexTradesFilters,
    TGMDexTradesRequest,
    TGMTokenInformationRequest,
)
from app.pipeline.timeutil import iso_z, parse_timestamp

logger = logging.getLogger(__name__)

OTHER_LABEL = "other"
MAX_WALLET_ROWS = 50
TRADES_PER_PAGE = 1000
MAX_TRADE_PAGES = 5


class SmartWalletSummary(BaseModel):
    address: str
    tier: str
    bought_usd: float
    sold_usd: float
    still_holding: bool


class SmartMoneyMetrics(BaseModel):
    wallet_count: int
    signal: bool
    label_counts: dict[str, int]
    weighted_score: float
    net_flow_usd: float
    net_selling: bool
    bought_tokens: float
    sold_tokens: float
    holding_ratio: float | None
    wallets_still_holding: int
    first_entry_at: datetime | None
    minutes_after_launch: float | None
    market_cap_at_entry: float | None
    smart_wallets: frozenset[str] = frozenset()
    total_holders: int | None = None
    wallets: list[SmartWalletSummary] = []


def match_label(label: str | None, cfg: SmartMoneyConfig) -> str | None:
    """The configured label key contained in `label`, longest key first; None if no match."""
    if not label:
        return None
    lowered = label.lower()
    for key in sorted(cfg.label_weights, key=len, reverse=True):
        if key.lower() in lowered:
            return key
    return None


def wallet_label_and_weight(
    labels: list[str | None], cfg: SmartMoneyConfig, tiers: frozenset[str] = frozenset()
) -> tuple[str, float]:
    """A wallet's best label: the highest-weighted tier it belongs to or a trade label matches."""
    matched = {key for key in (match_label(label, cfg) for label in labels) if key is not None}
    matched |= {tier for tier in tiers if tier in cfg.label_weights}
    if not matched:
        return OTHER_LABEL, cfg.default_label_weight
    best = max(matched, key=lambda key: cfg.label_weights[key])
    return best, cfg.label_weights[best]


def compute_smart_money_metrics(
    trades: list[TGMDexTrade],
    cfg: SmartMoneyConfig,
    *,
    deployed_at: datetime | None,
    now: datetime,
    circulating_supply: float | None,
    total_holders: int | None = None,
    tiers_by_wallet: Mapping[str, frozenset[str]] | None = None,
) -> SmartMoneyMetrics:
    """Metrics from the token's smart-money trades (both sides), oldest to newest."""
    dated = sorted(((parse_timestamp(t.block_timestamp), t) for t in trades), key=lambda p: p[0])

    labels_by_wallet: dict[str, list[str | None]] = defaultdict(list)
    net_tokens: dict[str, float] = defaultdict(float)
    bought_usd: dict[str, float] = defaultdict(float)
    sold_usd: dict[str, float] = defaultdict(float)
    buyers: set[str] = set()
    bought = sold = 0.0
    net_flow = 0.0
    flow_start = now - timedelta(hours=cfg.flow_window_hours)

    for moment, trade in dated:
        labels_by_wallet[trade.trader_address].append(trade.trader_address_label)
        signed = 1 if trade.action == "BUY" else -1
        if trade.action == "BUY":
            buyers.add(trade.trader_address)
            bought += trade.token_amount
        else:
            sold += trade.token_amount
        net_tokens[trade.trader_address] += signed * trade.token_amount
        if trade.action == "BUY":
            bought_usd[trade.trader_address] += trade.estimated_value_usd
        else:
            sold_usd[trade.trader_address] += trade.estimated_value_usd
        if flow_start <= moment <= now:
            net_flow += signed * trade.estimated_value_usd

    label_counts: dict[str, int] = defaultdict(int)
    weighted_score = 0.0
    summaries: list[SmartWalletSummary] = []
    for wallet in buyers:
        tiers = (tiers_by_wallet or {}).get(wallet, frozenset())
        label, weight = wallet_label_and_weight(labels_by_wallet[wallet], cfg, tiers)
        label_counts[label] += 1
        weighted_score += weight
        summaries.append(
            SmartWalletSummary(
                address=wallet,
                tier=label,
                bought_usd=bought_usd[wallet],
                sold_usd=sold_usd[wallet],
                still_holding=net_tokens[wallet] > 0,
            )
        )
    summaries.sort(key=lambda w: (-w.bought_usd, w.address))

    first_entry_at: datetime | None = None
    minutes_after_launch: float | None = None
    market_cap_at_entry: float | None = None
    first_buy = next(((m, t) for m, t in dated if t.action == "BUY"), None)
    if first_buy is not None:
        first_entry_at, entry = first_buy
        if deployed_at is not None:
            minutes_after_launch = max(0.0, (first_entry_at - deployed_at).total_seconds() / 60)
        if circulating_supply is not None and entry.token_amount > 0:
            market_cap_at_entry = (
                entry.estimated_value_usd / entry.token_amount * circulating_supply
            )

    return SmartMoneyMetrics(
        wallet_count=len(buyers),
        signal=len(buyers) >= cfg.min_wallets_for_signal,
        label_counts=dict(label_counts),
        weighted_score=weighted_score,
        net_flow_usd=net_flow,
        net_selling=net_flow < 0,
        bought_tokens=bought,
        sold_tokens=sold,
        holding_ratio=max(bought - sold, 0.0) / bought if bought > 0 else None,
        wallets_still_holding=sum(1 for wallet in buyers if net_tokens[wallet] > 0),
        first_entry_at=first_entry_at,
        minutes_after_launch=minutes_after_launch,
        market_cap_at_entry=market_cap_at_entry,
        smart_wallets=frozenset(buyers),
        total_holders=total_holders,
        wallets=summaries[:MAX_WALLET_ROWS],
    )


async def _fetch_trades(
    client: NansenClient,
    *,
    chain: str,
    token_address: str,
    start: datetime,
    now: datetime,
    filters: TGMDexTradesFilters | None = None,
    max_pages: int = MAX_TRADE_PAGES,
) -> tuple[list[TGMDexTrade], bool]:
    """All pages of smart-money trades (ascending) and whether the page cap truncated them."""
    trades: list[TGMDexTrade] = []
    for page in range(1, max_pages + 1):
        request = TGMDexTradesRequest(
            chain=chain,
            token_address=token_address,
            only_smart_money=True,
            date=DateRange.model_validate({"from": iso_z(start), "to": iso_z(now)}),
            pagination=PaginationRequest(page=page, per_page=TRADES_PER_PAGE),
            filters=filters,
            order_by=[SortOrder(field="block_timestamp", direction="ASC")],
        )
        response = await tgm_dex_trades(client, request)
        trades.extend(response.data)
        if response.pagination.is_last_page:
            return trades, False
    return trades, True


async def _fetch_tiers(
    client: NansenClient,
    cfg: SmartMoneyConfig,
    *,
    chain: str,
    token_address: str,
    start: datetime,
    now: datetime,
) -> dict[str, frozenset[str]]:
    """Which tiers each smart buyer belongs to: one filtered call per tier (1 credit each).

    Tier membership is only knowable from which filtered call returned the wallet, because the
    rows' trader_address_label does not name the tier.
    """
    tiers: dict[str, set[str]] = defaultdict(set)
    for tier in cfg.label_weights:
        try:
            trades, truncated = await _fetch_trades(
                client,
                chain=chain,
                token_address=token_address,
                start=start,
                now=now,
                filters=TGMDexTradesFilters(include_smart_money_labels=[tier], action="BUY"),
                max_pages=1,
            )
        except NansenAPIError as exc:
            logger.warning(
                "tier lookup failed, tier ignored",
                extra={
                    "chain": chain,
                    "token_address": token_address,
                    "tier": tier,
                    "status": exc.status,
                },
            )
            continue
        if truncated:
            logger.warning(
                "tier lookup truncated to the first page",
                extra={"chain": chain, "token_address": token_address, "tier": tier},
            )
        for trade in trades:
            tiers[trade.trader_address].add(tier)
    return {wallet: frozenset(found) for wallet, found in tiers.items()}


async def analyze_smart_money(
    client: NansenClient,
    cfg: SmartMoneyConfig,
    *,
    chain: str,
    token_address: str,
    deployed_at: datetime | None,
    now: datetime,
) -> SmartMoneyMetrics:
    start = deployed_at or now - timedelta(hours=cfg.flow_window_hours)
    trades, truncated = await _fetch_trades(
        client, chain=chain, token_address=token_address, start=start, now=now
    )
    if truncated:
        # Ascending order means the missing tail is the newest trades, which biases toward holding.
        logger.warning(
            "smart money trades truncated at the page cap",
            extra={"chain": chain, "token_address": token_address},
        )

    tiers: dict[str, frozenset[str]] = {}
    if any(trade.action == "BUY" for trade in trades):
        tiers = await _fetch_tiers(
            client, cfg, chain=chain, token_address=token_address, start=start, now=now
        )
    circulating_supply, total_holders = await _token_stats(client, chain, token_address)

    return compute_smart_money_metrics(
        trades,
        cfg,
        deployed_at=deployed_at,
        now=now,
        circulating_supply=circulating_supply,
        total_holders=total_holders,
        tiers_by_wallet=tiers,
    )


async def _token_stats(
    client: NansenClient, chain: str, token_address: str
) -> tuple[float | None, int | None]:
    """Circulating supply and holder count from one token-information call (1 credit)."""
    try:
        info = await tgm_token_information(
            client,
            TGMTokenInformationRequest(chain=chain, token_address=token_address, timeframe="1d"),
        )
    except NansenAPIError as exc:
        logger.warning(
            "token information lookup failed, supply and holder count unknown",
            extra={"chain": chain, "token_address": token_address, "status": exc.status},
        )
        return None, None
    details = info.data.token_details
    metrics = info.data.spot_metrics
    return (
        details.circulating_supply if details is not None else None,
        metrics.total_holders if metrics is not None else None,
    )
