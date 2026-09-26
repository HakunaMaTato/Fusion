from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.config import Settings, load_scoring_config
from app.nansen.client import NANSEN_BASE_URL
from app.nansen.exceptions import BudgetExceeded
from backtest import run as run_module
from backtest.client import BacktestClient
from backtest.discover import (
    HistCandidate,
    discover,
    sample_order,
    screener_calls,
    window_days,
)
from backtest.report import build_report, chart_svg, dump_rate, headline, wilson, write_report
from backtest.results import (
    OutcomeRow,
    ResultRow,
    SkipRow,
    append_jsonl,
    load_results,
    load_skips,
)

CFG = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml")
BCFG = CFG.backtest
ENDPOINT = "/api/v1beta1/tgm/historical-token-ohlcv"
PAGE = {"page": 1, "per_page": 10, "is_last_page": True}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, nansen_api_key="test-key", **overrides)  # type: ignore[arg-type]


# --- cache and cap ---


@pytest.mark.asyncio
async def test_a_repeated_request_is_served_from_disk_and_costs_nothing(tmp_path: Path) -> None:
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(
                200, json={"data": []}, headers={"X-Nansen-Credits-Used": "5"}
            )
        )
        client = BacktestClient(_settings(), max_credits=100, cache_dir=tmp_path)
        try:
            first = await client.post(ENDPOINT, {"a": 1})
            second = await client.post(ENDPOINT, {"a": 1})
        finally:
            await client.aclose()

    assert first == second and route.call_count == 1
    assert (client.live_calls, client.cache_hits, client.run_credits) == (1, 1, 5)


@pytest.mark.asyncio
async def test_a_second_client_reuses_the_cache(tmp_path: Path) -> None:
    with respx.mock:
        respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        one = BacktestClient(_settings(), max_credits=100, cache_dir=tmp_path)
        await one.post(ENDPOINT, {"a": 1})
        await one.aclose()
        two = BacktestClient(_settings(), max_credits=0, cache_dir=tmp_path)
        try:
            await two.post(ENDPOINT, {"a": 1})  # cap 0, but cached: allowed
        finally:
            await two.aclose()

    assert two.live_calls == 0 and two.cache_hits == 1


@pytest.mark.asyncio
async def test_the_cap_stops_a_call_before_it_is_made(tmp_path: Path) -> None:
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(
                200, json={"data": []}, headers={"X-Nansen-Credits-Used": "5"}
            )
        )
        client = BacktestClient(_settings(), max_credits=9, cache_dir=tmp_path)
        try:
            await client.post(ENDPOINT, {"a": 1})
            with pytest.raises(BudgetExceeded):
                await client.post(ENDPOINT, {"a": 2})  # 5 + 5 > 9
        finally:
            await client.aclose()

    assert route.call_count == 1


@pytest.mark.asyncio
async def test_the_client_goes_live_even_if_the_settings_say_replay(tmp_path: Path) -> None:
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        client = BacktestClient(_settings(nansen_mode="replay"), max_credits=50, cache_dir=tmp_path)
        try:
            await client.post(ENDPOINT, {})
        finally:
            await client.aclose()

    assert route.call_count == 1


# --- discovery ---


def test_the_window_ends_lag_days_before_today() -> None:
    days = window_days(BCFG, date(2026, 9, 26))

    assert len(days) == BCFG.days and days[-1] == date(2026, 9, 23) and days == sorted(days)
    assert screener_calls(BCFG, ["solana", "base"]) == 2 * BCFG.days


class FakeScreener(BacktestClient):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(_settings(), max_credits=10**6, cache_dir=tmp_path)
        self.bodies: list[dict[str, object]] = []

    async def post(self, endpoint, body, *, cache_ttl_seconds=None):  # type: ignore[no-untyped-def]
        self.bodies.append(body)
        day = body["to_date"]
        chain = body["chains"][0]
        repeat = {"token_address": "REPEAT", "token_symbol": "R", "chain": chain}
        own = {"token_address": f"T-{day}", "token_symbol": "T", "chain": chain}
        return {"data": [repeat, own], "pagination": PAGE}


@pytest.mark.asyncio
async def test_discovery_dedupes_and_keeps_the_earliest_day(tmp_path: Path) -> None:
    client = FakeScreener(tmp_path)
    try:
        found = await discover(client, BCFG, ["solana"], date(2026, 9, 26))
    finally:
        await client.aclose()

    repeat = next(c for c in found if c.token_address == "REPEAT")
    assert repeat.day == window_days(BCFG, date(2026, 9, 26))[0]
    assert len(found) == 1 + BCFG.days
    body = client.bodies[0]
    filters = body["filters"]
    assert isinstance(filters, dict) and body["timeframe_days"] == 1
    # no market-cap filter: it would hide tokens that pumped and then collapsed
    assert set(filters) == {"volume_usd", "token_age_days"}


def test_the_sample_order_is_stable_and_independent_of_input_order() -> None:
    items = [
        HistCandidate(chain="solana", token_address=f"t{i}", symbol="S", day=date(2026, 9, 1))
        for i in range(20)
    ]

    assert sample_order(items) == sample_order(list(reversed(items)))
    assert sample_order(items) != items


# --- statistics and the report ---


def test_wilson_interval() -> None:
    lo, hi = wilson(5, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.237, 0.763)
    assert wilson(0, 0) == (0.0, 0.0)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.722, abs=0.001)
    assert wilson(0, 10)[0] == 0.0


def row(
    verdict: str, dumped: bool | None, complete: bool = True, vetoes: tuple[str, ...] = ()
) -> ResultRow:
    outcomes = [
        OutcomeRow(
            hours=h,
            complete=complete,
            max_drawdown_pct=-60.0 if dumped else -5.0,
            price_change_pct=-40.0 if dumped else 10.0,
            dumped=dumped if complete else None,
        )
        for h in (6, 24, 72)
    ]
    return ResultRow(
        chain="solana",
        token_address=f"t{id(outcomes)}",
        symbol="S",
        day=date(2026, 9, 10),
        launch_at=datetime(2026, 9, 10, tzinfo=UTC),
        decision_at=datetime(2026, 9, 10, 1, tzinfo=UTC),
        decision_market_cap=1e6,
        verdict=verdict,
        score=50.0,
        vetoes=list(vetoes),
        bundle_status="none",
        bundle_supply_pct=0.0,
        bundle_wallets=0,
        sm_wallets=0,
        sm_net_selling=False,
        smart_in_bundle=0,
        outcomes=outcomes,
        bundle_exit_fraction=None,
        bundle_exited_24h=None,
        lookahead_rows_dropped=0,
        credits_spent=100,
    )


ROWS = [
    row("AVOID", True, vetoes=("bundle_supply",)),
    row("AVOID", True, vetoes=("bundle_supply",)),
    row("AVOID", False),
    row("GREEN", False),
    row("GREEN", None, complete=False),
]


def test_dump_rate_counts_only_complete_outcomes() -> None:
    assert dump_rate(ROWS, 24) == (2, 4)
    assert dump_rate([], 24) == (0, 0)


def test_the_headline_compares_avoid_with_green() -> None:
    text = headline(ROWS, 24, 50)

    assert "67% of cases (2/3)" in text and "0% for GREEN (0/1)" in text
    assert "No AVOID" in headline([row("GREEN", False)], 24, 50)


def test_the_report_states_the_method_and_the_numbers() -> None:
    text = build_report(
        ROWS,
        [SkipRow(chain="solana", token_address="x", reason="no_trades")],
        threshold=50,
        horizons=[6, 24, 72],
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
        reduced_inputs="volume and holder growth",
    )

    assert "# Backtest results" in text and "`bundle_supply`" in text
    assert "`no_trades`: 1" in text and "Tokens analysed: 5" in text
    assert "volume and holder growth" in text


def test_the_chart_is_a_self_contained_svg_and_survives_empty_input() -> None:
    assert chart_svg(ROWS, 24).startswith("<svg") and "AVOID (n=3)" in chart_svg(ROWS, 24)
    assert "n=0" in chart_svg([], 24)


def test_write_report_writes_both_files(tmp_path: Path) -> None:
    write_report(
        ROWS,
        [],
        threshold=50,
        horizons=[6, 24, 72],
        now=lambda: datetime(2026, 9, 26, tzinfo=UTC),
        report_path=tmp_path / "b.md",
        chart_path=tmp_path / "b.svg",
    )

    assert (tmp_path / "b.md").read_text(encoding="utf-8").startswith("# Backtest results")
    assert (tmp_path / "b.svg").read_text(encoding="utf-8").startswith("<svg")


def test_results_round_trip_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    append_jsonl(path, ROWS[0])
    append_jsonl(path, ROWS[1])

    assert load_results(path) == ROWS[:2]
    assert load_results(tmp_path / "missing.jsonl") == []
    assert load_skips(tmp_path / "missing.jsonl") == []


# --- CLI ---


def _never(_prompt: str) -> str:
    raise AssertionError("must not ask")


@pytest.fixture
def mode(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Pin the settings the CLI sees, so the developer's real .env is never consulted."""

    def set_mode(value: str) -> None:
        settings = Settings(_env_file=None, nansen_api_key="test-key", nansen_mode=value)  # type: ignore[arg-type]
        monkeypatch.setattr(run_module, "get_settings", lambda: settings)

    return set_mode


def test_estimate_spends_nothing_and_never_asks(
    mode,
    capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
) -> None:
    mode("replay")

    assert run_module.main(["estimate", "--limit", "3"], input_fn=_never) == 0

    out = capsys.readouterr().out
    assert "Discovery" in out and "worst case" in out


def test_estimates_scale_with_config() -> None:
    typical, worst = run_module.estimate_per_token(BCFG, CFG)

    assert 0 < typical < worst
    assert run_module.estimate_discover(BCFG, ["solana", "base"]) == BCFG.days * 2 * 5


def test_paid_commands_refuse_outside_live_mode(
    mode,
    capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
) -> None:
    mode("replay")

    assert run_module.main(["discover", "--max-credits", "1000"], input_fn=_never) == 2
    assert run_module.main(["analyze", "--limit", "1", "--max-credits", "1000"], _never) == 2
    assert "NANSEN_MODE=live" in capsys.readouterr().out


def test_discover_refuses_a_cap_below_its_own_estimate(
    mode,
    capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
) -> None:
    mode("live")

    assert run_module.main(["discover", "--max-credits", "10"], input_fn=_never) == 2
    assert "Refusing" in capsys.readouterr().out


def test_declining_the_confirmation_aborts_before_any_call(
    mode,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    mode("live")

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr(run_module, "run_discover", boom)
    monkeypatch.setattr(run_module, "run_analyze", boom)

    assert run_module.main(["discover", "--max-credits", "5000"], input_fn=lambda _: "n") == 1
    assert run_module.main(["analyze", "--limit", "2", "--max-credits", "900"], lambda _: "") == 1


def test_confirm_accepts_only_yes() -> None:
    assert run_module.confirm("x", lambda _: " Y ") is True
    assert run_module.confirm("x", lambda _: "yes please") is False
