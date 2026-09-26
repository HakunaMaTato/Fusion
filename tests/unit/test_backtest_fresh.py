from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings, load_scoring_config
from backtest import run as run_module
from backtest.client import BacktestClient
from backtest.discover import HistCandidate
from backtest.report import (
    batch_coverage_lines,
    build_report,
    fisher_two_sided,
    fresh2_lines,
    fresh_lines,
)
from backtest.results import ResultRow, load_results
from tests.unit.test_backtest_lifecycle import build, lifecycle, with_lifecycle
from tests.unit.test_backtest_tools import row

CFG = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml")


# --- Fisher exact test ---


def test_fisher_known_values() -> None:
    assert fisher_two_sided(3, 1, 1, 3) == pytest.approx(0.4857, abs=1e-3)
    assert fisher_two_sided(1, 9, 11, 3) == pytest.approx(0.00276, abs=1e-4)


def test_fisher_is_symmetric_and_one_when_there_is_no_difference() -> None:
    assert fisher_two_sided(5, 5, 5, 5) == pytest.approx(1.0)
    assert fisher_two_sided(8, 2, 2, 8) == pytest.approx(fisher_two_sided(2, 8, 8, 2))
    assert fisher_two_sided(10, 0, 0, 10) < 1e-4


# --- the fresh-token section ---


def fresh(bad: bool, sm_wallets: int, verdict: str = "WATCH", status: str = "none") -> ResultRow:
    lc = lifecycle(-97, -97, 100.0, 0.0) if bad else lifecycle(20, -10, 100.0, 50.0)
    return with_lifecycle(lc, verdict).model_copy(
        update={"sm_wallets": sm_wallets, "bundle_status": status, "batch": "fresh"}
    )


def pilot_rows() -> list[ResultRow]:
    return build([0.0, 0.01, 0.02, 0.03], [0.1, 0.2, 0.4, 0.5])  # threshold 6.5%


def test_no_fresh_tokens_no_section() -> None:
    assert fresh_lines(pilot_rows()) == []


def test_a_real_effect_in_the_fresh_batch_is_reported_as_supported() -> None:
    rows = pilot_rows()
    rows += [fresh(bad=True, sm_wallets=0) for _ in range(15)]
    rows += [fresh(bad=False, sm_wallets=3) for _ in range(15)]

    text = "\n".join(fresh_lines(rows))

    assert "Pre-registered checks on fresh tokens" in text
    assert "Fresh tokens analysed: 30; with a complete 72-hour window: 30" in text
    assert "no smart wallets: 15/15 (100%)" in text and "3+ smart wallets: 0/15 (0%)" in text
    h1 = next(line for line in text.splitlines() if line.startswith("| H1"))
    assert h1.endswith("| supported |")


def test_the_wrong_direction_is_not_reported_as_supported() -> None:
    rows = pilot_rows()
    rows += [fresh(bad=False, sm_wallets=0) for _ in range(15)]
    rows += [fresh(bad=True, sm_wallets=3) for _ in range(15)]

    h1 = next(x for x in "\n".join(fresh_lines(rows)).splitlines() if x.startswith("| H1"))

    assert h1.endswith("| opposite direction |")


def test_a_small_difference_does_not_pass_the_corrected_bar() -> None:
    rows = pilot_rows()
    rows += [fresh(bad=i < 4, sm_wallets=0) for i in range(6)]  # 4/6 bad
    rows += [fresh(bad=i < 2, sm_wallets=3) for i in range(6)]  # 2/6 bad

    h1 = next(x for x in "\n".join(fresh_lines(rows)).splitlines() if x.startswith("| H1"))

    assert h1.endswith("| not supported |")


def test_the_threshold_comes_from_the_pilot_tokens_only() -> None:
    rows = pilot_rows() + [fresh(bad=True, sm_wallets=0) for _ in range(10)]
    rows += [fresh(bad=False, sm_wallets=3) for _ in range(10)]

    text = "\n".join(fresh_lines(rows))

    assert "(6.5%)" in text  # the fresh tokens, however many, do not move it


def test_a_group_with_no_fresh_tokens_is_not_testable() -> None:
    text = "\n".join(fresh_lines(pilot_rows() + [fresh(bad=True, sm_wallets=1)]))

    h1 = next(x for x in text.splitlines() if x.startswith("| H1"))
    assert "not testable" in h1


def test_the_report_includes_the_fresh_section_when_there_are_fresh_tokens() -> None:
    rows = pilot_rows() + [fresh(bad=True, sm_wallets=0) for _ in range(5)]

    text = build_report(
        rows, [], threshold=50, horizons=[6, 24, 72],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC), reduced_inputs="x",
    )  # fmt: skip

    assert "Pre-registered checks on fresh tokens" in text


# --- window shift, merge and batch labels ---


def cand(name: str, day: date = date(2026, 9, 1)) -> HistCandidate:
    return HistCandidate(chain="solana", token_address=name, symbol=name, day=day)


@pytest.mark.asyncio
async def test_discovery_can_shift_the_window_and_keeps_earlier_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "candidates.json"
    old = cand("old", date(2026, 9, 12))
    path.write_text("[" + old.model_dump_json() + "]", encoding="utf-8")
    seen: list[date] = []

    async def fake_discover(client, bcfg, chains, today):  # type: ignore[no-untyped-def]
        seen.append(today)
        return [cand("new"), cand("old", date(2026, 8, 30))]  # "old" also found again

    monkeypatch.setattr(run_module, "CANDIDATES_PATH", path)
    monkeypatch.setattr(run_module, "discover", fake_discover)
    monkeypatch.setattr(
        run_module,
        "BacktestClient",
        lambda s, max_credits: BacktestClient(s, max_credits=max_credits, cache_dir=tmp_path),
    )

    settings = Settings(_env_file=None, nansen_api_key="k")  # type: ignore[call-arg]
    await run_module.run_discover(
        settings, CFG.backtest, ["solana"], 300, date(2026, 9, 26), shift_days=14
    )

    assert seen == [date(2026, 9, 12)]
    stored = {c.token_address: c for c in run_module.load_candidates()}
    assert set(stored) == {"old", "new"}
    assert stored["old"].day == date(2026, 9, 12)  # the stored entry wins


@pytest.mark.asyncio
async def test_analyze_labels_results_with_the_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_process(client, bcfg, cfg, candidate, today):  # type: ignore[no-untyped-def]
        return row("GREEN", False).model_copy(update={"token_address": candidate.token_address})

    results = tmp_path / "results.jsonl"
    monkeypatch.setattr(run_module, "load_candidates", lambda: [cand("a"), cand("b")])
    monkeypatch.setattr(run_module, "load_results", lambda: load_results(results))
    monkeypatch.setattr(run_module, "load_skips", lambda: [])
    monkeypatch.setattr(run_module, "process", fake_process)
    monkeypatch.setattr(run_module, "RESULTS_PATH", results)
    monkeypatch.setattr(
        run_module,
        "BacktestClient",
        lambda s, max_credits: BacktestClient(s, max_credits=max_credits, cache_dir=tmp_path),
    )

    settings = Settings(_env_file=None, nansen_api_key="k")  # type: ignore[call-arg]
    await run_module.run_analyze(
        settings, CFG.backtest, CFG, 5, 100, date(2026, 9, 26), batch="fresh"
    )

    assert {r.batch for r in load_results(results)} == {"fresh"}


def test_rows_stored_before_batches_existed_count_as_pilot() -> None:
    old_json = row("GREEN", False).model_dump_json(exclude={"batch"})

    assert ResultRow.model_validate_json(old_json).batch == "pilot"


@pytest.mark.asyncio
async def test_analyze_can_be_limited_to_candidates_before_a_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    async def fake_process(client, bcfg, cfg, candidate, today):  # type: ignore[no-untyped-def]
        seen.append(candidate.token_address)
        return row("GREEN", False).model_copy(update={"token_address": candidate.token_address})

    candidates = [cand("early", date(2026, 9, 9)), cand("late", date(2026, 9, 10))]
    monkeypatch.setattr(run_module, "load_candidates", lambda: candidates)
    monkeypatch.setattr(run_module, "load_results", lambda: [])
    monkeypatch.setattr(run_module, "load_skips", lambda: [])
    monkeypatch.setattr(run_module, "process", fake_process)
    monkeypatch.setattr(run_module, "RESULTS_PATH", tmp_path / "r.jsonl")
    monkeypatch.setattr(
        run_module,
        "BacktestClient",
        lambda s, max_credits: BacktestClient(s, max_credits=max_credits, cache_dir=tmp_path),
    )
    settings = Settings(_env_file=None, nansen_api_key="k")  # type: ignore[call-arg]

    await run_module.run_analyze(
        settings, CFG.backtest, CFG, 5, 100, date(2026, 9, 26), "fresh", date(2026, 9, 10)
    )

    assert seen == ["early"]


# --- the second fresh batch: six checks ---


def fresh2(
    bad: bool,
    sm_wallets: int = 1,
    minutes: float = 60.0,
    bundle_pct: float = 1.0,
    status: str = "none",
    verdict: str = "WATCH",
    vetoes: list[str] | None = None,
) -> ResultRow:
    lc = lifecycle(-97, -97, 100.0, 0.0) if bad else lifecycle(20, -10, 100.0, 50.0)
    launch = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
    return with_lifecycle(lc, verdict).model_copy(
        update={
            "sm_wallets": sm_wallets,
            "bundle_status": status,
            "bundle_supply_pct": bundle_pct,
            "vetoes": vetoes or [],
            "batch": "fresh2",
            "launch_at": launch,
            "decision_at": launch + timedelta(minutes=minutes),
        }
    )


def check_line(text: str, name: str) -> str:
    return next(x for x in text.splitlines() if x.startswith(f"| {name}"))


def test_no_second_batch_no_section() -> None:
    assert fresh2_lines(pilot_rows()) == []


def test_all_six_checks_are_printed_whatever_the_outcome() -> None:
    rows = pilot_rows() + [fresh2(bad=i % 2 == 0) for i in range(10)]

    text = "\n".join(fresh2_lines(rows))

    for name in ("H1", "H2", "H3", "H4", "H5", "H6"):
        assert check_line(text, name)
    assert "with 6 checks the bar is p < 0.0083" in text


def test_pump_speed_check_supports_a_real_effect() -> None:
    rows = pilot_rows()
    rows += [fresh2(bad=True, minutes=5) for _ in range(15)]  # fast pumps die
    rows += [fresh2(bad=False, minutes=90) for _ in range(15)]

    line = check_line("\n".join(fresh2_lines(rows)), "H5")

    assert "$1M within 30 min: 15/15" in line and line.endswith("| supported |")


def test_the_pump_speed_boundary_is_inclusive() -> None:
    rows = pilot_rows() + [fresh2(bad=True, minutes=30) for _ in range(4)]
    rows += [fresh2(bad=False, minutes=31) for _ in range(4)]

    line = check_line("\n".join(fresh2_lines(rows)), "H5")

    assert "$1M within 30 min: 4/4" in line and "slower: 0/4" in line


def test_big_bundle_check_uses_the_fifteen_percent_line() -> None:
    rows = pilot_rows()
    rows += [fresh2(bad=True, bundle_pct=15.0) for _ in range(15)]
    rows += [fresh2(bad=False, bundle_pct=14.9) for _ in range(15)]

    line = check_line("\n".join(fresh2_lines(rows)), "H6")

    assert "bundle 15%+ of supply: 15/15" in line and line.endswith("| supported |")


def test_a_check_with_the_wrong_direction_is_flagged() -> None:
    rows = pilot_rows()
    rows += [fresh2(bad=False, minutes=5) for _ in range(15)]
    rows += [fresh2(bad=True, minutes=90) for _ in range(15)]

    assert check_line("\n".join(fresh2_lines(rows)), "H5").endswith("| opposite direction |")


def test_the_batches_do_not_leak_into_each_other() -> None:
    base = pilot_rows()
    first = [fresh(bad=True, sm_wallets=0) for _ in range(10)]
    second = [fresh2(bad=True, sm_wallets=0) for _ in range(10)]

    # rows of the other fresh batch neither change the pilot threshold nor the groups
    only_second = "\n".join(fresh2_lines(base + second))
    both = "\n".join(fresh2_lines(base + first + second))
    only_first = "\n".join(fresh_lines(base + first))
    with_second = "\n".join(fresh_lines(base + first + second))

    assert only_second == both
    assert only_first == with_second
    assert "(6.5%)" in both and "(6.5%)" in with_second


def test_the_report_includes_the_second_section() -> None:
    rows = pilot_rows() + [fresh2(bad=True) for _ in range(5)]

    text = build_report(
        rows, [], threshold=50, horizons=[6, 24, 72],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC), reduced_inputs="x",
    )  # fmt: skip

    assert "Second fresh batch: six pre-registered checks" in text


def test_smart_money_coverage_is_reported_per_batch() -> None:
    rows = pilot_rows() + [fresh2(bad=True, sm_wallets=0) for _ in range(9)]
    rows += [fresh2(bad=False, sm_wallets=3)]

    text = "\n".join(batch_coverage_lines(rows))

    assert "| fresh2 |" in text and "| 1 (10%) | 1 (10%) |" in text
    assert "| pilot |" in text and "| fresh |" not in text  # empty batches are left out
