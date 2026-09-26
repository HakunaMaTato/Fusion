import logging
from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel, Field

from app.config import DiscoveryConfig
from app.nansen.client import NansenClient
from app.nansen.endpoints import smart_money_dex_trades, token_screener
from app.nansen.models import (
    NumericRangeFilter,
    PaginationRequest,
    SmartMoneyDexTrade,
    SmartMoneyDexTradesFilters,
    SmartMoneyDexTradesRequest,
    SortOrder,
    TokenScreenerFilters,
    TokenScreenerRequest,
    TokenScreenerToken,
)
from app.pipeline.timeutil import parse_optional_timestamp

logger = logging.getLogger(__name__)

FEED_SCREENER = "screener"
FEED_SMART_MONEY = "smart_money"

SCREENER_PER_PAGE = 100
TRADES_PER_PAGE = 1000
CONFIRM_BATCH_SIZE = 50

TokenKey = tuple[str, str]


class Candidate(BaseModel):
    chain: str
    token_address: str
    token_symbol: str
    token_age_hours: float
    market_cap_usd: float
    feeds: dict[str, datetime] = Field(default_factory=dict)
    smart_wallet_count: int | None = None
    token_deployment_date: datetime | None = None
    volume_usd: float | None = None
    liquidity_usd: float | None = None

    @property
    def key(self) -> TokenKey:
        return (self.chain, self.token_address)


def filter_screener_tokens(
    tokens: list[TokenScreenerToken], cfg: DiscoveryConfig, now: datetime
) -> list[Candidate]:
    """Feed A local re-filter: the API filters on whole days, so age is re-checked in hours."""
    candidates: list[Candidate] = []
    for token in tokens:
        if token.token_age_hours is None or token.market_cap_usd is None:
            logger.warning(
                "excluding token with missing age or market cap",
                extra={"chain": token.chain, "token_address": token.token_address},
            )
            continue
        if token.token_age_hours > cfg.pump_window_hours:
            continue
        if token.market_cap_usd < cfg.min_market_cap_usd:
            continue
        candidates.append(
            Candidate(
                chain=token.chain,
                token_address=token.token_address,
                token_symbol=token.token_symbol,
                token_age_hours=token.token_age_hours,
                market_cap_usd=token.market_cap_usd,
                feeds={FEED_SCREENER: now},
                token_deployment_date=parse_optional_timestamp(token.token_deployment_date),
                volume_usd=token.volume,
                liquidity_usd=token.liquidity,
            )
        )
    return candidates


def smart_wallets_by_token(
    trades: list[SmartMoneyDexTrade], min_wallets: int
) -> dict[TokenKey, set[str]]:
    """Feed B: distinct smart wallets that bought a brand-new (age 0 days) token."""
    wallets: dict[TokenKey, set[str]] = defaultdict(set)
    for trade in trades:
        if trade.token_bought_age_days != 0:
            continue
        wallets[(trade.chain, trade.token_bought_address)].add(trade.trader_address)
    return {key: found for key, found in wallets.items() if len(found) >= min_wallets}


def confirm_smart_money_tokens(
    smart_wallets: dict[TokenKey, set[str]],
    screener_tokens: list[TokenScreenerToken],
    cfg: DiscoveryConfig,
    now: datetime,
) -> list[Candidate]:
    """Keep Feed B tokens whose age (<= max_age_hours) and market cap are confirmed."""
    by_key = {(t.chain, t.token_address): t for t in screener_tokens}
    candidates: list[Candidate] = []
    for key, wallets in smart_wallets.items():
        token = by_key.get(key)
        if token is None:
            logger.warning(
                "excluding smart money token not confirmed by screener",
                extra={"chain": key[0], "token_address": key[1]},
            )
            continue
        if token.token_age_hours is None or token.market_cap_usd is None:
            logger.warning(
                "excluding smart money token with missing age or market cap",
                extra={"chain": key[0], "token_address": key[1]},
            )
            continue
        if token.token_age_hours > cfg.max_age_hours:
            continue
        candidates.append(
            Candidate(
                chain=token.chain,
                token_address=token.token_address,
                token_symbol=token.token_symbol,
                token_age_hours=token.token_age_hours,
                market_cap_usd=token.market_cap_usd,
                feeds={FEED_SMART_MONEY: now},
                smart_wallet_count=len(wallets),
                token_deployment_date=parse_optional_timestamp(token.token_deployment_date),
                volume_usd=token.volume,
                liquidity_usd=token.liquidity,
            )
        )
    return candidates


def merge_candidates(*feeds: list[Candidate]) -> list[Candidate]:
    merged: dict[TokenKey, Candidate] = {}
    for feed in feeds:
        for candidate in feed:
            existing = merged.get(candidate.key)
            if existing is None:
                merged[candidate.key] = candidate.model_copy(deep=True)
                continue
            for name, seen_at in candidate.feeds.items():
                previous = existing.feeds.get(name)
                if previous is None or seen_at < previous:
                    existing.feeds[name] = seen_at
            if existing.token_deployment_date is None:
                existing.token_deployment_date = candidate.token_deployment_date
            if candidate.smart_wallet_count is not None:
                existing.smart_wallet_count = candidate.smart_wallet_count
    return list(merged.values())


async def fetch_feed_a(
    client: NansenClient, cfg: DiscoveryConfig, chains: list[str], now: datetime
) -> list[Candidate]:
    request = TokenScreenerRequest(
        chains=chains,
        timeframe=cfg.screener_timeframe,
        pagination=PaginationRequest(page=1, per_page=SCREENER_PER_PAGE),
        filters=TokenScreenerFilters(
            token_age_days=NumericRangeFilter(max=cfg.pump_window_hours / 24),
            market_cap_usd=NumericRangeFilter(min=cfg.min_market_cap_usd),
        ),
        order_by=[SortOrder(field="market_cap_usd", direction="DESC")],
    )
    response = await token_screener(client, request)
    if not response.pagination.is_last_page:
        logger.warning("screener feed truncated to the first page")
    return filter_screener_tokens(response.data, cfg, now)


async def fetch_feed_b(
    client: NansenClient,
    cfg: DiscoveryConfig,
    chains: list[str],
    min_wallets: int,
    now: datetime,
) -> list[Candidate]:
    trades_request = SmartMoneyDexTradesRequest(
        chains=chains,
        filters=SmartMoneyDexTradesFilters(token_bought_age_days=NumericRangeFilter(max=0)),
        pagination=PaginationRequest(page=1, per_page=TRADES_PER_PAGE),
        order_by=[SortOrder(field="block_timestamp", direction="DESC")],
    )
    trades = await smart_money_dex_trades(client, trades_request)
    if not trades.pagination.is_last_page:
        logger.warning("smart money feed truncated to the first page")

    smart_wallets = smart_wallets_by_token(trades.data, min_wallets)
    if not smart_wallets:
        return []

    # One request per chain and at most CONFIRM_BATCH_SIZE addresses: Nansen's edge (Cloudflare)
    # answered a single request holding 100+ addresses from several chains with a 403 block page,
    # while the same addresses sent one chain at a time were fine (seen live, 2026-09-26).
    by_chain: dict[str, set[str]] = defaultdict(set)
    for chain, address in smart_wallets:
        by_chain[chain].add(address)
    screener_tokens: list[TokenScreenerToken] = []
    for chain in sorted(by_chain):
        addresses = sorted(by_chain[chain])
        for start in range(0, len(addresses), CONFIRM_BATCH_SIZE):
            batch = addresses[start : start + CONFIRM_BATCH_SIZE]
            confirm_request = TokenScreenerRequest(
                chains=[chain],
                timeframe=cfg.screener_timeframe,
                pagination=PaginationRequest(page=1, per_page=TRADES_PER_PAGE),
                filters=TokenScreenerFilters(token_address=batch),
            )
            screener_tokens.extend((await token_screener(client, confirm_request)).data)
    return confirm_smart_money_tokens(smart_wallets, screener_tokens, cfg, now)
