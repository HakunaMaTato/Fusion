"""Find backtest candidates: young, liquid tokens per day and chain.

The historical screener is filtered on age and volume but deliberately NOT on market cap: a
market-cap filter measured at the end of the day would drop tokens that pumped and then collapsed,
which would make dumps look rarer than they are.
"""

import hashlib
from datetime import date, timedelta

from pydantic import BaseModel

from app.config import BacktestConfig
from app.nansen.client import NansenClient
from app.nansen.endpoints import token_screener_historical
from app.nansen.models import (
    IntegerRangeFilter,
    NumericRangeFilter,
    PaginationRequest,
    SortOrder,
    TokenScreenerHistoricalFilters,
    TokenScreenerHistoricalRequest,
)

BACKTEST_CHAINS = ("solana", "base", "bnb", "ethereum")  # what the historical endpoints cover
SCREENER_CREDITS = 5


class HistCandidate(BaseModel):
    chain: str
    token_address: str
    symbol: str
    day: date
    volume_usd: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.token_address)


def window_days(cfg: BacktestConfig, today: date) -> list[date]:
    """The `days` days ending `lag_days` before today, oldest first (recent tokens lack +72h)."""
    last = today - timedelta(days=cfg.lag_days)
    return [last - timedelta(days=offset) for offset in range(cfg.days - 1, -1, -1)]


def screener_calls(cfg: BacktestConfig, chains: list[str]) -> int:
    return cfg.days * len(chains)


async def discover_day_chain(
    client: NansenClient, cfg: BacktestConfig, chain: str, day: date
) -> list[HistCandidate]:
    response = await token_screener_historical(
        client,
        TokenScreenerHistoricalRequest(
            to_date=day.isoformat(),
            timeframe_days=1,
            chains=[chain],
            filters=TokenScreenerHistoricalFilters(
                volume_usd=NumericRangeFilter(min=cfg.min_volume_usd),
                token_age_days=IntegerRangeFilter(max=1),
            ),
            pagination=PaginationRequest(page=1, per_page=cfg.max_candidates_per_day_chain),
            order_by=[SortOrder(field="volume", direction="DESC")],
        ),
    )
    return [
        HistCandidate(
            chain=token.chain,
            token_address=token.token_address,
            symbol=token.token_symbol,
            day=day,
            volume_usd=token.volume,
        )
        for token in response.data
    ]


async def discover(
    client: NansenClient, cfg: BacktestConfig, chains: list[str], today: date
) -> list[HistCandidate]:
    """All candidates; a token seen on two days keeps its earliest day."""
    found: dict[tuple[str, str], HistCandidate] = {}
    for day in window_days(cfg, today):
        for chain in chains:
            for candidate in await discover_day_chain(client, cfg, chain, day):
                found.setdefault(candidate.key, candidate)
    return list(found.values())


def sample_order(candidates: list[HistCandidate]) -> list[HistCandidate]:
    """A fixed pseudo-random order, so a pilot of the first N is an unbiased sample."""

    def key(c: HistCandidate) -> str:
        return hashlib.sha256(f"{c.chain}:{c.token_address}".encode()).hexdigest()

    return sorted(candidates, key=key)
