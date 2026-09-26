from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.config import load_scoring_config
from backtest import run as run_module
from backtest.client import BacktestClient
from backtest.lifecycle import (
    COLLAPSED,
    DEAD,
    HELD,
    RECOVERED,
    best_threshold,
    calibrate,
    classify,
    compute_lifecycle,
    is_clean,
    volume_share,
)
from backtest.report import build_report
from backtest.results import LifecycleRow, ResultRow, append_jsonl, load_results
from backtest.timeline import Candle, Decision
from tests.backtest_world import CANDIDATE, TODAY, ScriptedBacktest
from tests.unit.test_backtest_tools import row

CFG = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml")
T0 = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
DECISION = Decision(T0 - timedelta(minutes=30), T0, 2.0, 2e6)
STEP = timedelta(minutes=5)


def candle(hours: float, close: float, low: float | None = None, volume: float = 100.0) -> Candle:
    start = T0 + timedelta(hours=hours)
    return Candle(start, start + STEP, low if low is not None else close, close, volume, None)


def lifecycle(change: float, trough: float, first: float, late: float) -> LifecycleRow:
    return LifecycleRow(
        complete=True,
        trough_pct=trough,
        trough_hours=1.0,
        change_pct=change,
        volume_first_usd=first,
        volume_late_usd=late,
    )


def with_lifecycle(lc: LifecycleRow | None, verdict: str = "GREEN") -> ResultRow:
    return row(verdict, None).model_copy(update={"lifecycle": lc})


# --- the +72h picture ---


def test_lifecycle_measures_trough_end_and_the_two_volume_windows() -> None:
    candles = [
        candle(1, 1.6, low=0.9, volume=300),  # first-day volume, the trough
        candle(30, 1.0, volume=999),  # neither window
        candle(50, 1.0, volume=40),  # late window
        candle(71, 3.0, volume=10),  # late window, last close
    ]

    lc = compute_lifecycle(candles, DECISION, data_end=T0 + timedelta(days=5))

    assert lc.complete and lc.trough_pct == pytest.approx(-55.0) and lc.trough_hours == 1.0
    assert lc.change_pct == pytest.approx(50.0)
    assert lc.volume_first_usd == 300 and lc.volume_late_usd == 50
    assert volume_share(lc) == pytest.approx(50 / 300)


def test_lifecycle_is_incomplete_without_72_hours_of_data() -> None:
    lc = compute_lifecycle([candle(1, 1.0)], DECISION, data_end=T0 + timedelta(hours=71))

    assert not lc.complete and lc.change_pct is None and volume_share(lc) is None


def test_a_token_that_stops_trading_has_no_volume_and_no_share_when_it_never_had_any() -> None:
    lc = compute_lifecycle([], DECISION, data_end=T0 + timedelta(days=5))

    assert lc.complete and lc.volume_first_usd == 0 and volume_share(lc) is None


def test_candles_before_the_decision_are_ignored() -> None:
    before = Candle(T0 - STEP, T0, 0.1, 0.1, 999.0, None)

    lc = compute_lifecycle([before], DECISION, data_end=T0 + timedelta(days=5))

    assert lc.trough_pct == 0.0 and lc.volume_first_usd == 0


# --- the threshold comes from the data ---


def test_the_threshold_sits_between_the_groups() -> None:
    assert best_threshold([0.0, 0.01, 0.02], [0.10, 0.20, 0.40]) == pytest.approx(0.06)


def test_overlapping_groups_take_the_best_available_split() -> None:
    threshold = best_threshold([0.0, 0.0, 0.05, 0.30], [0.02, 0.10, 0.20, 0.40])

    assert 0.02 < threshold < 0.30


def build(rugged: list[float], survivors: list[float]) -> list[ResultRow]:
    rugs = [with_lifecycle(lifecycle(-97, -97, 100.0, 100.0 * s)) for s in rugged]
    lives = [with_lifecycle(lifecycle(10, -20, 100.0, 100.0 * s)) for s in survivors]
    return rugs + lives


def test_calibration_labels_by_price_and_ignores_the_middle() -> None:
    rows = build([0.0, 0.01, 0.02], [0.1, 0.2, 0.4])
    rows.append(with_lifecycle(lifecycle(-60, -70, 100.0, 1.0)))  # neither rugged nor survivor

    cal = calibrate(rows)

    assert len(cal.rugged) == 3 and len(cal.survivors) == 3
    assert cal.threshold == pytest.approx(0.06) and cal.accuracy == 1.0
    assert cal.loo_accuracy is None and cal.loo_n == 0  # leaving one out would leave < 3


def test_leave_one_out_needs_more_than_the_minimum_group() -> None:
    cal = calibrate(build([0.0, 0.01, 0.02, 0.03], [0.1, 0.2, 0.4, 0.5]))

    assert cal.loo_accuracy == 0.875 and cal.loo_n == 8  # the edge survivor is misjudged


def test_no_threshold_when_a_group_is_too_small() -> None:
    cal = calibrate(build([0.0, 0.01], [0.1, 0.2, 0.4]))

    assert cal.threshold is None and cal.loo_accuracy is None


def test_incomplete_tokens_do_not_take_part_in_calibration() -> None:
    rows = build([0.0, 0.01, 0.02], [0.1, 0.2, 0.4])
    rows.append(with_lifecycle(LifecycleRow(
        complete=False, trough_pct=None, trough_hours=None, change_pct=None,
        volume_first_usd=None, volume_late_usd=None,
    )))  # fmt: skip

    assert len(calibrate(rows).rugged) == 3 and len(calibrate(rows).survivors) == 3


def test_leave_one_out_is_honest_about_an_overlap() -> None:
    cal = calibrate(build([0.0, 0.0, 0.05, 0.30], [0.02, 0.10, 0.20, 0.40]))

    assert cal.loo_accuracy is not None and cal.loo_accuracy < 1.0


# --- classes ---


def test_classes() -> None:
    assert classify(lifecycle(-97, -97, 100, 0.5), 0.04) == DEAD  # volume gone
    assert classify(lifecycle(+400, +10, 100, 0.0), 0.04) == DEAD  # price up, nobody trading
    assert classify(lifecycle(-70, -80, 100, 20), 0.04) == COLLAPSED  # still traded, but down
    assert classify(lifecycle(-10, -66, 100, 20), 0.04) == RECOVERED  # dipped, came back
    assert classify(lifecycle(+30, -20, 100, 20), 0.04) == HELD


def test_without_a_threshold_nothing_is_called_dead() -> None:
    assert classify(lifecycle(-97, -97, 100, 0.0), None) == COLLAPSED


def test_an_unfinished_token_has_no_class() -> None:
    assert classify(None, 0.04) is None
    unfinished = LifecycleRow(
        complete=False, trough_pct=None, trough_hours=None, change_pct=None,
        volume_first_usd=None, volume_late_usd=None,
    )  # fmt: skip
    assert classify(unfinished, 0.04) is None


def test_the_clean_flag() -> None:
    base = row("GREEN", None).model_copy(update={"sm_wallets": 3, "bundle_supply_pct": 1.0})

    assert is_clean(base)
    assert not is_clean(base.model_copy(update={"vetoes": ["sm_in_bundle"]}))
    assert not is_clean(base.model_copy(update={"sm_wallets": 1}))
    assert not is_clean(base.model_copy(update={"bundle_supply_pct": 20.0}))
    assert not is_clean(base.model_copy(update={"sm_net_selling": True}))


# --- the report ---


def test_the_report_shows_the_calibration_and_the_classes() -> None:
    rows = build([0.0, 0.01, 0.02], [0.1, 0.2, 0.4])

    text = build_report(
        rows, [], threshold=50, horizons=[6, 24, 72],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC), reduced_inputs="x",
    )  # fmt: skip

    assert "Threshold: a volume share below 6.0% is called dead" in text
    assert "Dead or collapsed by +72h" in text and "### Clean tokens against the rest" in text
    assert "How much the threshold depends on the labels" in text


def test_the_report_says_so_when_there_is_too_little_data() -> None:
    text = build_report(
        [row("GREEN", False)], [], threshold=50, horizons=[6, 24, 72],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC), reduced_inputs="x",
    )  # fmt: skip

    assert "Not enough tokens to set a threshold" in text
    assert "No token has 72 hours of data" in text


# --- process fills it in, rescore is free ---


@pytest.mark.asyncio
async def test_a_processed_token_carries_its_lifecycle() -> None:
    from backtest.pipeline import process

    client = ScriptedBacktest()
    try:
        result = await process(client, CFG.backtest, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert isinstance(result, ResultRow) and result.lifecycle is not None
    assert result.lifecycle.complete and result.lifecycle.trough_pct == pytest.approx(-75.0)


@pytest.mark.asyncio
async def test_rescore_reruns_from_the_cache_without_spending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = row("GREEN", None).model_copy(
        update={"chain": CANDIDATE.chain, "token_address": CANDIDATE.token_address}
    )
    results = tmp_path / "results.jsonl"
    append_jsonl(results, old)
    made: list[BacktestClient] = []

    def factory(settings: object, max_credits: int) -> BacktestClient:
        assert max_credits == 0  # a cache miss must fail, not spend
        client = ScriptedBacktest()
        made.append(client)
        return client

    monkeypatch.setattr(run_module, "BacktestClient", factory)
    monkeypatch.setattr(run_module, "load_candidates", lambda: [CANDIDATE])
    monkeypatch.setattr(run_module, "load_results", lambda: load_results(results))
    monkeypatch.setattr(run_module, "RESULTS_PATH", results)

    await run_module.run_rescore(
        run_module.Settings(_env_file=None),
        CFG.backtest,
        CFG,
        date(2026, 9, 20),  # type: ignore[attr-defined]
    )

    (rescored,) = load_results(results)
    assert rescored.lifecycle is not None and rescored.verdict == "AVOID"
    assert "0 credits" in capsys.readouterr().out
