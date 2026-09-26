"""Decision time and outcomes from OHLCV candles.

The decision is taken when a candle CLOSES with market cap at or above the threshold, so nothing
inside that candle is used before the decision. Outcomes only ever look forward from that moment.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.nansen.models import OhlcvCandle
from app.pipeline.timeutil import parse_timestamp

_STEPS = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def candle_step(timeframe: str) -> timedelta:
    return timedelta(minutes=_STEPS[timeframe])


@dataclass(frozen=True)
class Candle:
    start: datetime
    end: datetime
    low: float | None
    close: float | None
    volume_usd: float | None
    market_cap_close: float | None


def parse_candles(candles: list[OhlcvCandle], step: timedelta) -> list[Candle]:
    parsed = [
        Candle(
            start=(start := parse_timestamp(c.interval_start)),
            end=start + step,
            low=c.low,
            close=c.close,
            volume_usd=c.volume_usd,
            market_cap_close=c.market_cap.close,
        )
        for c in candles
    ]
    return sorted(parsed, key=lambda c: c.start)


@dataclass(frozen=True)
class Decision:
    launch_at: datetime
    decision_at: datetime
    reference_price: float
    market_cap: float


def find_decision(
    candles: list[Candle], *, min_market_cap: float, pump_window: timedelta
) -> Decision | None:
    """First candle close at or above `min_market_cap`, within `pump_window` of the launch.

    The launch is the first candle that has both a price and traded volume.
    """
    launch = next(
        (c.start for c in candles if c.close is not None and (c.volume_usd or 0) > 0), None
    )
    if launch is None:
        return None
    for candle in candles:
        if candle.start < launch or candle.close is None or candle.market_cap_close is None:
            continue
        if candle.market_cap_close >= min_market_cap:
            if candle.end - launch > pump_window or candle.close <= 0:
                return None
            return Decision(launch, candle.end, candle.close, candle.market_cap_close)
    return None


@dataclass(frozen=True)
class HorizonOutcome:
    hours: int
    complete: bool
    max_drawdown_pct: float | None
    price_change_pct: float | None
    dumped: bool | None


def compute_outcomes(
    candles: list[Candle],
    decision: Decision,
    *,
    data_end: datetime,
    horizons_hours: list[int],
    dump_threshold_pct: float,
) -> list[HorizonOutcome]:
    """Worst decline and final change versus the price at the decision, per horizon.

    A horizon is complete only if the requested data reaches it (`data_end`); a token that simply
    stopped trading keeps its last price. Drawdown is measured from the decision price, not from a
    running peak.
    """
    ref = decision.reference_price
    outcomes: list[HorizonOutcome] = []
    for hours in horizons_hours:
        horizon_end = decision.decision_at + timedelta(hours=hours)
        if data_end < horizon_end:
            outcomes.append(HorizonOutcome(hours, False, None, None, None))
            continue
        window = [c for c in candles if c.start >= decision.decision_at and c.end <= horizon_end]
        lows = [c.low for c in window if c.low is not None]
        closes = [c.close for c in window if c.close is not None]
        drawdown = (min(lows) / ref - 1) * 100 if lows else 0.0
        drawdown = min(drawdown, 0.0)
        change = (closes[-1] / ref - 1) * 100 if closes else 0.0
        outcomes.append(
            HorizonOutcome(hours, True, drawdown, change, drawdown <= -dump_threshold_pct)
        )
    return outcomes
