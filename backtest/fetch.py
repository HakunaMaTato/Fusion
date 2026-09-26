"""Fetch the point-in-time data for one candidate. Everything used for the verdict ends at the
decision time; only `fetch_later_trades` looks past it, and only for the outcome."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.config import BacktestConfig, ScoringConfig
from app.nansen.client import NansenClient
from app.nansen.endpoints import (
    tgm_historical_dex_trades,
    tgm_historical_token_ohlcv,
    tgm_token_information,
)
from app.nansen.models import (
    DateRange,
    PaginationRequest,
    SortOrder,
    TGMDexTrade,
    TGMHistoricalDexTradesFilters,
    TGMHistoricalDexTradesRequest,
    TGMHistoricalTokenOhlcvRequest,
    TGMTokenInformationRequest,
)
from app.pipeline.bundle import Funder, early_buys, lookup_funder, wallets_to_look_up
from app.pipeline.timeutil import iso_z
from backtest.analyze import PointInTimeData, to_tgm_trade
from backtest.discover import HistCandidate
from backtest.pointintime import assert_range_ends_by, keep_until
from backtest.timeline import Candle, Decision, candle_step, find_decision, parse_candles

TRADES_PER_PAGE = 1000

SKIP_NO_CANDLES = "no_candles"
SKIP_CANDLES_TRUNCATED = "candles_truncated"
SKIP_NEVER_REACHED = "never_reached_market_cap"
SKIP_NO_SUPPLY = "no_supply"
SKIP_NO_TRADES = "no_trades"
SKIP_TRADES_TRUNCATED = "trades_truncated"


class Skipped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class TokenData:
    candidate: HistCandidate
    decision: Decision
    candles: list[Candle]
    data_end: datetime
    point_in_time: PointInTimeData
    lookahead_rows_dropped: int


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _range(start: datetime, end: datetime) -> DateRange:
    return DateRange.model_validate({"from": iso_z(start), "to": iso_z(end)})


async def pull_trades(
    client: NansenClient,
    candidate: HistCandidate,
    start: datetime,
    end: datetime,
    *,
    max_pages: int,
    filters: TGMHistoricalDexTradesFilters | None = None,
) -> tuple[list[TGMDexTrade], bool]:
    """Trades in [start, end], oldest first, and whether `max_pages` truncated them."""
    trades: list[TGMDexTrade] = []
    for page in range(1, max_pages + 1):
        response = await tgm_historical_dex_trades(
            client,
            TGMHistoricalDexTradesRequest(
                chain=candidate.chain,
                token_address=candidate.token_address,
                date_range=_range(start, end),
                pagination=PaginationRequest(page=page, per_page=TRADES_PER_PAGE),
                filters=filters,
                order_by=[SortOrder(field="block_timestamp", direction="ASC")],
            ),
        )
        trades.extend(to_tgm_trade(t, candidate.token_address) for t in response.data)
        if response.pagination.is_last_page:
            return trades, False
    return trades, True


async def fetch_token_data(
    client: NansenClient,
    bcfg: BacktestConfig,
    cfg: ScoringConfig,
    candidate: HistCandidate,
    today: date,
) -> TokenData:
    step = candle_step(bcfg.candle_timeframe)
    as_of = min(candidate.day + timedelta(days=5), today)
    ohlcv = await tgm_historical_token_ohlcv(
        client,
        TGMHistoricalTokenOhlcvRequest(
            chain=candidate.chain,
            token_address=candidate.token_address,
            date_from=iso_z(_midnight(candidate.day - timedelta(days=1))),
            as_of_date=as_of.isoformat(),
            timeframe=bcfg.candle_timeframe,  # type: ignore[arg-type]
        ),
    )
    if not ohlcv.data:
        raise Skipped(SKIP_NO_CANDLES)
    if ohlcv.truncated:
        raise Skipped(SKIP_CANDLES_TRUNCATED)
    candles = parse_candles(ohlcv.data, step)
    decision = find_decision(
        candles,
        min_market_cap=cfg.discovery.min_market_cap_usd,
        pump_window=timedelta(hours=cfg.discovery.pump_window_hours),
    )
    if decision is None:
        raise Skipped(SKIP_NEVER_REACHED)
    decision_at = decision.decision_at

    info = await tgm_token_information(
        client,
        TGMTokenInformationRequest(
            chain=candidate.chain, token_address=candidate.token_address, timeframe="1d"
        ),
    )
    details = info.data.token_details
    supply = (details.total_supply or details.circulating_supply) if details else None
    if not supply:
        raise Skipped(SKIP_NO_SUPPLY)

    assert_range_ends_by(decision_at, decision_at)
    raw, truncated = await pull_trades(
        client, candidate, decision.launch_at, decision_at, max_pages=bcfg.max_trade_pages
    )
    if truncated:
        raise Skipped(SKIP_TRADES_TRUNCATED)
    trades, dropped = keep_until(decision_at, raw, lambda t: t.block_timestamp)
    if not trades:
        raise Skipped(SKIP_NO_TRADES)

    tier_sets: dict[str, set[str]] = defaultdict(set)
    for tier in cfg.smart_money.label_weights:
        tier_trades, _ = await pull_trades(
            client,
            candidate,
            decision.launch_at,
            decision_at,
            max_pages=bcfg.tier_pages,
            filters=TGMHistoricalDexTradesFilters(include_labels=[tier]),
        )
        kept, extra = keep_until(decision_at, tier_trades, lambda t: t.block_timestamp)
        dropped += extra
        for trade in kept:
            tier_sets[trade.trader_address].add(tier)
    tiers = {wallet: frozenset(found) for wallet, found in tier_sets.items()}

    funders: dict[str, Funder | None] = {}
    buys = early_buys(trades, decision.launch_at, cfg.bundle)
    for wallet in wallets_to_look_up(buys, cfg.bundle):
        funders[wallet] = await lookup_funder(
            client,
            candidate.chain,
            wallet,
            cfg.bundle,
            cache_ttl_seconds=cfg.budget.related_wallets_cache_hours * 3600,
        )

    return TokenData(
        candidate=candidate,
        decision=decision,
        candles=candles,
        data_end=_midnight(as_of),
        point_in_time=PointInTimeData(
            launch_at=decision.launch_at,
            decision_at=decision_at,
            trades=trades,
            tiers=tiers,
            funders=funders,
            total_supply=float(supply),
        ),
        lookahead_rows_dropped=dropped,
    )


async def fetch_later_trades(
    client: NansenClient,
    bcfg: BacktestConfig,
    candidate: HistCandidate,
    decision_at: datetime,
    hours: int,
) -> list[TGMDexTrade] | None:
    """Trades AFTER the decision, for the bundle-exit outcome only. None if they were truncated."""
    trades, truncated = await pull_trades(
        client,
        candidate,
        decision_at,
        decision_at + timedelta(hours=hours),
        max_pages=bcfg.exit_trade_pages,
    )
    return None if truncated else trades
