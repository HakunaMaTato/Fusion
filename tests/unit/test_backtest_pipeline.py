from datetime import timedelta
from pathlib import Path

import pytest

from app.config import load_scoring_config
from app.nansen.exceptions import BudgetExceeded
from app.nansen.models import TGMDexTrade
from app.pipeline.bundle import BundleAnalysis
from app.pipeline.scoring import ScoreInputs, score_token
from app.pipeline.smart_money import SmartMoneyMetrics
from backtest.analyze import (
    UNAVAILABLE,
    bundle_exit_fraction,
    holders_from_trades,
    net_positions,
    to_tgm_trade,
)
from backtest.fetch import (
    SKIP_NEVER_REACHED,
    SKIP_TRADES_TRUNCATED,
    fetch_token_data,
)
from backtest.pipeline import process
from backtest.results import ResultRow, SkipRow
from tests.backtest_world import (
    BUNDLE,
    CANDIDATE,
    DECISION_AT,
    LAUNCH,
    TODAY,
    ScriptedBacktest,
    iso,
    trade,
)
from tests.factories import tgm_trade

CFG = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml")
BCFG = CFG.backtest


# --- balances rebuilt from trades ---


def t(wallet: str, action: str, tokens: float, second: int = 0) -> TGMDexTrade:
    stamp = iso(LAUNCH + timedelta(seconds=second))
    return tgm_trade(wallet, stamp, tokens=tokens, action=action)


def test_net_positions_are_buys_minus_sells_never_negative() -> None:
    trades = [t("a", "BUY", 100), t("a", "SELL", 30, 1), t("b", "SELL", 50, 2), t("c", "BUY", 5)]

    assert net_positions(trades) == {"a": 70.0, "b": 0.0, "c": 5.0}


def test_holders_are_a_zero_to_one_fraction_of_supply_and_missing_wallets_hold_nothing() -> None:
    trades = [t("a", "BUY", 250), t("a", "BUY", 250, 1)]

    holders = {h.address: h for h in holders_from_trades(trades, frozenset({"a", "ghost"}), 1000.0)}

    assert holders["a"].token_amount == 500 and holders["a"].ownership_percentage == 0.5
    assert holders["ghost"].token_amount == 0 and holders["ghost"].ownership_percentage == 0


def test_bundle_exit_fraction() -> None:
    later = [t("a", "SELL", 60), t("b", "SELL", 20), t("x", "SELL", 999), t("a", "BUY", 500)]

    assert bundle_exit_fraction(frozenset({"a", "b"}), {"a": 100.0, "b": 100.0}, later) == 0.4
    assert bundle_exit_fraction(frozenset({"a"}), {"a": 10.0}, later) == 1.0  # capped at 100%
    assert bundle_exit_fraction(frozenset({"a"}), {}, later) is None
    assert bundle_exit_fraction(frozenset(), {"a": 5.0}, later) is None


def test_the_historical_trade_becomes_a_pipeline_trade() -> None:
    from app.nansen.models import TGMHistoricalDexTrade

    raw = TGMHistoricalDexTrade.model_validate(trade("w", LAUNCH))

    converted = to_tgm_trade(raw, "TOK")

    assert converted.token_address == "TOK" and converted.trader_address == "w"
    assert converted.action == "BUY" and converted.token_amount == 1000.0


# --- reduced-input scoring ---


def _inputs(wallets: int) -> ScoreInputs:
    sm = SmartMoneyMetrics(
        wallet_count=wallets,
        signal=wallets >= 2,
        label_counts={},
        weighted_score=float(wallets),
        net_flow_usd=100.0,
        net_selling=False,
        bought_tokens=1.0,
        sold_tokens=0.0,
        holding_ratio=1.0 if wallets else None,
        wallets_still_holding=wallets,
        first_entry_at=None,
        minutes_after_launch=None,
        market_cap_at_entry=None,
    )
    bundle = BundleAnalysis(
        clusters=[], bundled_wallets=frozenset(), supply_pct=0.0, sold_pct=0.0, status="none"
    )
    return ScoreInputs(
        smart_money=sm,
        bundle=bundle,
        smart_wallets_in_bundle=frozenset(),
        volume_usd=None,
        liquidity_usd=None,
        token_age_hours=None,
    )


def test_unavailable_components_are_rescaled_not_scored_as_zero() -> None:
    full = score_token(_inputs(5), CFG.scoring)
    reduced = score_token(_inputs(5), CFG.scoring, unavailable=UNAVAILABLE)

    assert full.score == pytest.approx(70.0)  # 30 of 100 weight is missing
    assert reduced.score == pytest.approx(100.0)
    assert reduced.verdict == "GREEN" and full.verdict == "GREEN"
    assert not any("unavailable" in reason for reason in reduced.reasons)
    assert any("unavailable" in reason for reason in full.reasons)


def test_rescaling_does_not_change_a_vetoed_verdict() -> None:
    inputs = _inputs(5)
    inputs.smart_money.net_selling = True

    result = score_token(inputs, CFG.scoring, unavailable=UNAVAILABLE)

    assert result.verdict == "AVOID" and result.vetoes == ["sm_net_selling"]


# --- one token end to end ---


@pytest.mark.asyncio
async def test_a_bundled_token_is_avoided_and_its_dump_is_measured() -> None:
    client = ScriptedBacktest()
    try:
        row = await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert isinstance(row, ResultRow)
    assert row.verdict == "AVOID" and "bundle_supply" in row.vetoes
    assert row.bundle_wallets == 12 and row.bundle_supply_pct == pytest.approx(48.0)
    assert row.bundle_status == "holding"
    assert row.sm_wallets == 2
    assert row.decision_at == DECISION_AT and row.launch_at == LAUNCH
    assert row.decision_market_cap == 1_200_000.0

    six, day, three_days = row.outcomes
    assert (six.hours, day.hours, three_days.hours) == (6, 24, 72)
    assert all(o.complete for o in row.outcomes)
    assert six.dumped is True and six.max_drawdown_pct == pytest.approx(-75.0)  # low 0.3 vs 1.2
    assert six.price_change_pct == pytest.approx(-58.33, abs=0.01)  # close 0.5 vs 1.2
    assert row.bundle_exited_24h is True and row.bundle_exit_fraction == 1.0


@pytest.mark.asyncio
async def test_rows_after_the_decision_never_reach_the_verdict() -> None:
    with_future, without = (
        ScriptedBacktest(plant_future_rows=True),
        ScriptedBacktest(plant_future_rows=False),
    )
    try:
        planted = await process(with_future, BCFG, CFG, CANDIDATE, TODAY)
        clean = await process(without, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await with_future.aclose()
        await without.aclose()

    assert isinstance(planted, ResultRow) and isinstance(clean, ResultRow)
    assert planted.lookahead_rows_dropped == 1 and clean.lookahead_rows_dropped == 0
    # the planted sell of the bundle's tokens would have cut its supply share from 48% to 44%
    assert planted.bundle_supply_pct == clean.bundle_supply_pct == pytest.approx(48.0)
    assert planted.score == clean.score


@pytest.mark.asyncio
async def test_no_analysis_request_reaches_past_the_decision() -> None:
    client = ScriptedBacktest()
    try:
        data = await fetch_token_data(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    trade_requests = [b for e, b in client.calls if e.endswith("historical-dex-trades")]
    assert len(trade_requests) == 1 + len(CFG.smart_money.label_weights)  # all trades + tiers
    for body in trade_requests:
        assert body["date_range"]["from"] == iso(data.decision.launch_at)
        assert body["date_range"]["to"] == iso(data.decision.decision_at)
    assert max(body["date_range"]["to"] for body in trade_requests) == iso(DECISION_AT)


@pytest.mark.asyncio
async def test_only_the_outcome_looks_past_the_decision() -> None:
    client = ScriptedBacktest()
    try:
        await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    later = [
        b
        for e, b in client.calls
        if e.endswith("historical-dex-trades") and b["date_range"]["from"] == iso(DECISION_AT)
    ]
    assert len(later) == 1
    assert later[0]["date_range"]["to"] == iso(DECISION_AT + timedelta(hours=24))


@pytest.mark.asyncio
async def test_the_ohlcv_request_covers_the_launch_day_and_the_horizon() -> None:
    client = ScriptedBacktest()
    try:
        await fetch_token_data(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    body = next(b for e, b in client.calls if e.endswith("historical-token-ohlcv"))
    assert body["date_from"] == "2026-09-09T00:00:00Z"
    assert body["as_of_date"] == "2026-09-15"
    assert body["timeframe"] == "5m"


@pytest.mark.asyncio
async def test_the_data_window_is_capped_at_today_and_later_horizons_are_incomplete() -> None:
    client = ScriptedBacktest()
    try:
        row = await process(client, BCFG, CFG, CANDIDATE, CANDIDATE.day + timedelta(days=1))
    finally:
        await client.aclose()

    assert isinstance(row, ResultRow)
    assert [o.complete for o in row.outcomes] == [True, False, False]
    assert row.outcomes[1].dumped is None
    assert row.bundle_exited_24h is None  # the +24h window is beyond the data


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"never_pumps": True}, SKIP_NEVER_REACHED),
        ({"trades_never_end": True}, SKIP_TRADES_TRUNCATED),
    ],
)
@pytest.mark.asyncio
async def test_untestable_tokens_are_skipped_with_a_reason(
    options: dict[str, bool], reason: str
) -> None:
    client = ScriptedBacktest(**options)
    try:
        row = await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert isinstance(row, SkipRow) and row.reason == reason


@pytest.mark.asyncio
async def test_a_skipped_token_spends_only_what_it_used() -> None:
    client = ScriptedBacktest(never_pumps=True)
    try:
        await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert [e for e, _ in client.calls] == ["/api/v1beta1/tgm/historical-token-ohlcv"]
    assert client.run_credits == 5


@pytest.mark.asyncio
async def test_credits_spent_are_recorded_per_token() -> None:
    client = ScriptedBacktest()
    try:
        row = await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert isinstance(row, ResultRow) and row.credits_spent == client.run_credits > 0


@pytest.mark.asyncio
async def test_the_bundle_funders_are_looked_up_for_the_earliest_buyers_only() -> None:
    client = ScriptedBacktest()
    try:
        await fetch_token_data(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    lookups = [b["wallet_address"] for e, b in client.calls if e.endswith("related-wallets")]
    assert sorted(lookups) == sorted(BUNDLE + ["o0", "o1", "o2"])
    assert len(lookups) <= CFG.bundle.max_funder_lookups


@pytest.mark.asyncio
async def test_budget_exhaustion_propagates_to_the_runner() -> None:
    class Capped(ScriptedBacktest):
        async def post(self, endpoint, body, *, cache_ttl_seconds=None):  # type: ignore[no-untyped-def]
            if len(self.calls) >= 3:
                raise BudgetExceeded(self.run_credits, self.max_credits)
            return await super().post(endpoint, body)

    client = Capped()
    try:
        with pytest.raises(BudgetExceeded):
            await process(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_supply_is_implied_by_the_decision_candle_not_fetched() -> None:
    client = ScriptedBacktest()
    try:
        data = await fetch_token_data(client, BCFG, CFG, CANDIDATE, TODAY)
    finally:
        await client.aclose()

    assert data.point_in_time.total_supply == pytest.approx(1_000_000_000.0)  # 1.2M / 1.2e-3
    assert not any(e.endswith("token-information") for e, _ in client.calls)
