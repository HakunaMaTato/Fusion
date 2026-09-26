from datetime import UTC, datetime, timedelta

import pytest

from app.nansen.models import MarketCapData, OhlcvCandle
from backtest.pointintime import LookAheadError, assert_range_ends_by, keep_until
from backtest.timeline import (
    Candle,
    Decision,
    candle_step,
    compute_outcomes,
    find_decision,
    parse_candles,
)

LAUNCH = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
WINDOW = timedelta(hours=4)


def candle(
    i: int,
    close: float | None = 1.0,
    mcap: float | None = 500_000.0,
    low: float | None = None,
    volume: float | None = 1000.0,
) -> Candle:
    start = LAUNCH + i * STEP
    return Candle(
        start=start,
        end=start + STEP,
        low=low if low is not None else close,
        close=close,
        volume_usd=volume,
        market_cap_close=mcap,
    )


# --- candles ---


def test_parse_candles_sorts_and_sets_the_end() -> None:
    raw = [
        OhlcvCandle(
            interval_start="2026-09-10T02:05:00Z",
            market_cap=MarketCapData(close=2.0),
            close=1.1,
            low=1.0,
            volume_usd=5.0,
        ),
        OhlcvCandle(interval_start="2026-09-10T02:00:00Z", market_cap=MarketCapData(close=1.0)),
    ]

    parsed = parse_candles(raw, STEP)

    assert [c.start for c in parsed] == [LAUNCH, LAUNCH + STEP]
    assert parsed[1].end == LAUNCH + 2 * STEP
    assert parsed[1].market_cap_close == 2.0 and parsed[0].close is None


def test_candle_steps() -> None:
    assert candle_step("5m") == timedelta(minutes=5)
    assert candle_step("1h") == timedelta(hours=1)


# --- decision time ---


def test_the_decision_is_the_close_of_the_first_candle_at_the_threshold() -> None:
    candles = [
        candle(0),
        candle(1, mcap=900_000),
        candle(2, close=1.2, mcap=1_000_000),
        candle(3, mcap=2e6),
    ]

    decision = find_decision(candles, min_market_cap=1_000_000, pump_window=WINDOW)

    assert decision == Decision(LAUNCH, LAUNCH + 3 * STEP, 1.2, 1_000_000)
    assert decision is not None and decision.decision_at == candles[2].end


def test_launch_is_the_first_candle_with_a_price_and_volume() -> None:
    candles = [
        candle(0, close=None, mcap=None),
        candle(1, volume=0.0),
        candle(2, volume=None),
        candle(3, mcap=1_500_000),
    ]

    decision = find_decision(candles, min_market_cap=1_000_000, pump_window=WINDOW)

    assert decision is not None and decision.launch_at == LAUNCH + 3 * STEP


def test_a_token_that_never_reaches_the_threshold_has_no_decision() -> None:
    assert (
        find_decision(
            [candle(i, mcap=900_000) for i in range(10)], min_market_cap=1e6, pump_window=WINDOW
        )
        is None
    )
    assert find_decision([], min_market_cap=1e6, pump_window=WINDOW) is None


def test_reaching_the_threshold_after_the_pump_window_is_too_late() -> None:
    late = int(WINDOW / STEP)  # this candle closes after launch + 4h
    candles = (
        [candle(0)] + [candle(i, mcap=500_000) for i in range(1, late)] + [candle(late, mcap=2e6)]
    )

    assert find_decision(candles, min_market_cap=1e6, pump_window=WINDOW) is None


def test_the_pump_window_boundary_is_inclusive() -> None:
    last_inside = int(WINDOW / STEP) - 1  # closes exactly at launch + 4h
    candles = [candle(0)] + [candle(last_inside, mcap=2e6)]

    decision = find_decision(candles, min_market_cap=1e6, pump_window=WINDOW)

    assert decision is not None and decision.decision_at == LAUNCH + WINDOW


def test_missing_prices_or_market_caps_are_skipped() -> None:
    candles = [
        candle(0),
        candle(1, close=None, mcap=5e6),
        candle(2, mcap=None),
        candle(3, mcap=1.5e6),
    ]

    decision = find_decision(candles, min_market_cap=1e6, pump_window=WINDOW)

    assert decision is not None and decision.decision_at == candles[3].end


# --- outcomes ---


DECISION = Decision(LAUNCH, LAUNCH + 6 * STEP, 2.0, 2e6)
DATA_END = LAUNCH + timedelta(days=6)


def outcomes(candles: list[Candle], data_end: datetime = DATA_END, hours: list[int] | None = None):  # type: ignore[no-untyped-def]
    return compute_outcomes(
        candles,
        DECISION,
        data_end=data_end,
        horizons_hours=hours or [6, 24, 72],
        dump_threshold_pct=50,
    )


def after(hours: float, close: float, low: float | None = None) -> Candle:
    start = DECISION.decision_at + timedelta(hours=hours)
    return Candle(start, start + STEP, low if low is not None else close, close, 100.0, None)


def test_drawdown_and_change_per_horizon() -> None:
    candles = [after(1, 1.6, low=1.2), after(5, 1.0, low=0.9), after(10, 3.0, low=2.5)]

    six, day, three_days = outcomes(candles)

    assert six.complete and six.max_drawdown_pct == pytest.approx(-55.0)  # low 0.9 vs 2.0
    assert six.price_change_pct == pytest.approx(-50.0)  # last close inside 6h is 1.0
    assert six.dumped is True
    assert day.max_drawdown_pct == pytest.approx(-55.0)
    assert day.price_change_pct == pytest.approx(50.0)  # last close by 24h is 3.0
    assert three_days.complete


def test_a_fall_of_exactly_the_threshold_counts_as_a_dump() -> None:
    (outcome,) = outcomes([after(1, 1.0, low=1.0)], hours=[6])

    assert outcome.max_drawdown_pct == pytest.approx(-50.0)
    assert outcome.dumped is True


def test_a_smaller_fall_is_not_a_dump() -> None:
    (outcome,) = outcomes([after(1, 1.1, low=1.05)], hours=[6])

    assert outcome.dumped is False


def test_a_token_that_only_rises_has_zero_drawdown() -> None:
    (outcome,) = outcomes([after(1, 2.4, low=2.2), after(2, 3.0)], hours=[6])

    assert outcome.max_drawdown_pct == 0.0 and outcome.price_change_pct == pytest.approx(50.0)


def test_a_token_that_stops_trading_keeps_its_last_price() -> None:
    (outcome,) = outcomes([], hours=[24])

    assert outcome.complete and outcome.max_drawdown_pct == 0.0 and outcome.price_change_pct == 0.0


def test_a_horizon_beyond_the_data_is_incomplete() -> None:
    data_end = DECISION.decision_at + timedelta(hours=30)

    six, day, three_days = outcomes([after(1, 1.0)], data_end=data_end)

    assert six.complete and day.complete
    assert not three_days.complete
    assert three_days.dumped is None and three_days.max_drawdown_pct is None


def test_candles_after_the_horizon_are_ignored_for_that_horizon() -> None:
    candles = [after(1, 2.0), after(8, 0.2, low=0.1)]  # the crash is after +6h

    six, day, _ = outcomes(candles)

    assert six.dumped is False
    assert day.dumped is True


def test_candles_before_the_decision_are_never_part_of_an_outcome() -> None:
    before = Candle(DECISION.decision_at - STEP, DECISION.decision_at, 0.1, 0.1, 100.0, None)

    (outcome,) = outcomes([before], hours=[6])

    assert outcome.dumped is False


# --- point-in-time guard ---


def test_keep_until_drops_and_counts_later_rows() -> None:
    at = LAUNCH + timedelta(minutes=30)
    rows = ["2026-09-10T02:29:59Z", "2026-09-10T02:30:00Z", "2026-09-10T02:30:01Z"]

    kept, dropped = keep_until(at, rows, lambda r: r)

    assert kept == rows[:2] and dropped == 1


def test_a_request_range_past_the_decision_is_rejected() -> None:
    at = LAUNCH + timedelta(minutes=30)

    assert_range_ends_by(at, at)
    assert_range_ends_by(at, at - STEP)
    with pytest.raises(LookAheadError):
        assert_range_ends_by(at, at + timedelta(seconds=1))
