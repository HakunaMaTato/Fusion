import logging
from collections import defaultdict
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
    TGMDexTradesRequest,
    TGMTokenInformationRequest,
)
from app.pipeline.timeutil import iso_z, parse_timestamp

logger = logging.getLogger(__name__)

OTHER_LABEL = "other"
TRADES_PER_PAGE = 1000
MAX_TRADE_PAGES = 5


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


def match_label(label: str | None, cfg: SmartMoneyConfig) -> str | None:
    """The configured label key contained in `label`, longest key first; None if no match."""
    if not label:
        return None
    lowered = label.lower()
    for key in sorted(cfg.label_weights, key=len, reverse=True):
        if key.lower() in lowered:
            return key
    return None


def wallet_label_and_weight(labels: list[str | None], cfg: SmartMoneyConfig) -> tuple[str, float]:
    """A wallet's best label: the highest-weighted configured match, else `other`."""
    matched = [key for key in (match_label(label, cfg) for label in labels) if key is not None]
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
) -> SmartMoneyMetrics:
    """Metrics from the token's smart-money trades (both sides), oldest to newest."""
    dated = sorted(((parse_timestamp(t.block_timestamp), t) for t in trades), key=lambda p: p[0])

    labels_by_wallet: dict[str, list[str | None]] = defaultdict(list)
    net_tokens: dict[str, float] = defaultdict(float)
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
        if flow_start <= moment <= now:
            net_flow += signed * trade.estimated_value_usd

    label_counts: dict[str, int] = defaultdict(int)
    weighted_score = 0.0
    for wallet in buyers:
        label, weight = wallet_label_and_weight(labels_by_wallet[wallet], cfg)
        label_counts[label] += 1
        weighted_score += weight

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
    )


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
    trades: list[TGMDexTrade] = []
    for page in range(1, MAX_TRADE_PAGES + 1):
        request = TGMDexTradesRequest(
            chain=chain,
            token_address=token_address,
            only_smart_money=True,
            date=DateRange.model_validate({"from": iso_z(start), "to": iso_z(now)}),
            pagination=PaginationRequest(page=page, per_page=TRADES_PER_PAGE),
            order_by=[SortOrder(field="block_timestamp", direction="ASC")],
        )
        response = await tgm_dex_trades(client, request)
        trades.extend(response.data)
        if response.pagination.is_last_page:
            break
    else:
        # Ascending order means the missing tail is the newest trades, which biases toward holding.
        logger.warning(
            "smart money trades truncated at the page cap",
            extra={"chain": chain, "token_address": token_address},
        )

    circulating_supply: float | None = None
    if any(trade.action == "BUY" for trade in trades):
        circulating_supply = await _circulating_supply(client, chain, token_address)

    return compute_smart_money_metrics(
        trades,
        cfg,
        deployed_at=deployed_at,
        now=now,
        circulating_supply=circulating_supply,
    )


async def _circulating_supply(client: NansenClient, chain: str, token_address: str) -> float | None:
    try:
        info = await tgm_token_information(
            client,
            TGMTokenInformationRequest(chain=chain, token_address=token_address, timeframe="1d"),
        )
    except NansenAPIError as exc:
        logger.warning(
            "token information lookup failed, market cap at entry unknown",
            extra={"chain": chain, "token_address": token_address, "status": exc.status},
        )
        return None
    details = info.data.token_details
    return details.circulating_supply if details is not None else None
