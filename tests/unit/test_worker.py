import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import MonitorConfig, ScoringConfig, Settings, load_scoring_config
from app.nansen.exceptions import NansenAPIError
from app.pipeline.discovery import Candidate
from app.storage import repo
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.tables import AlertSentRow, SnapshotRow, TokenRow
from app.worker import Worker
from tests.scripted import START, FakeClock, RecordingNotifier, ScriptedNansen, WorldToken

REAL_CFG = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml")
GOOD = "GOODtoken"
BAD = "BADtoken"


@dataclass
class Env:
    worker: Worker
    client: ScriptedNansen
    clock: FakeClock
    notifier: RecordingNotifier
    factory: sessionmaker[Session]
    engine: Engine
    settings: Settings
    cfg: ScoringConfig


def make_env(
    tmp_path: Path,
    tokens: list[WorldToken] | None = None,
    *,
    daily_budget: int = 3000,
    cfg: ScoringConfig | None = None,
    failures: dict[str, Any] | None = None,
    notifier: RecordingNotifier | None = None,
    clock: FakeClock | None = None,
    engine: Engine | None = None,
) -> Env:
    clock = clock or FakeClock()
    settings = Settings(
        _env_file=None, nansen_mode="replay", daily_credit_budget=daily_budget, chains=["solana"]
    )
    cfg = cfg or REAL_CFG
    if engine is None:
        engine = make_engine(f"sqlite:///{tmp_path / 'worker.db'}")
        init_db(engine)
    factory = make_session_factory(engine)
    client = ScriptedNansen(settings, clock, tokens or [WorldToken(GOOD)], failures)
    notifier = notifier or RecordingNotifier()
    worker = Worker(
        settings=settings,
        cfg=cfg,
        client=client,
        notifier=notifier,
        session_factory=factory,
        clock=clock.now,
        sleep=clock.sleep,
    )
    return Env(worker, client, clock, notifier, factory, engine, settings, cfg)


async def simulate(env: Env, seconds: float, tick: float = 5.0) -> None:
    env.worker.startup()
    end = env.clock.now() + timedelta(seconds=seconds)
    while env.clock.now() < end:
        await env.worker.tick()
        await env.clock.sleep(tick)


def snapshots(env: Env, address: str = GOOD) -> list[SnapshotRow]:
    with env.factory() as session:
        token = repo.find_token(session, "solana", address)
        if token is None:
            return []
        return list(
            session.scalars(
                select(SnapshotRow).where(SnapshotRow.token_id == token.id).order_by(SnapshotRow.id)
            )
        )


def alert_types(env: Env) -> list[str]:
    with env.factory() as session:
        return [row.event_type for row in session.scalars(select(AlertSentRow))]


# --- the happy path ---


@pytest.mark.asyncio
async def test_first_tick_analyses_stores_and_alerts_once(tmp_path: Path) -> None:
    env = make_env(tmp_path)

    await env.worker.tick()

    (snapshot,) = snapshots(env)
    assert snapshot.verdict == "GREEN"
    assert alert_types(env) == ["new_green"]
    assert len(env.notifier.messages) == 1
    assert "GREEN" in env.notifier.messages[0]
    with env.factory() as session:
        assert repo.last_beat(session) == START
        assert repo.last_discovery_at(session) == START
        assert repo.get_credits_used(session, START.date()) == env.client.credits_used_today


@pytest.mark.asyncio
async def test_a_known_token_is_not_analysed_again_before_it_is_due(tmp_path: Path) -> None:
    env = make_env(tmp_path)

    await env.worker.tick()
    await env.clock.sleep(130)  # a second feed poll finds the same token
    await env.worker.tick()

    assert len(snapshots(env)) == 1
    assert len(env.notifier.messages) == 1


@pytest.mark.asyncio
async def test_ten_simulated_minutes_run_cleanly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(tmp_path)
    stop = asyncio.Event()
    real_sleep = env.clock.sleep

    async def sleep_then_stop(seconds: float) -> None:
        await real_sleep(seconds)
        if env.clock.now() >= START + timedelta(minutes=10):
            stop.set()

    env.worker._sleep = sleep_then_stop  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        await env.worker.run(stop)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert len(snapshots(env)) == 1  # re-evaluation cadence (900s) is longer than ten minutes
    assert len(env.client.calls_to("/api/v1/token-screener")) >= 5  # feed A every 120s
    with env.factory() as session:
        beat = repo.last_beat(session)
    assert beat is not None and env.clock.now() - beat <= timedelta(seconds=5)
    assert env.client.credits_used_today < env.settings.daily_credit_budget / 24


# --- re-evaluation and alert transitions ---


@pytest.mark.asyncio
async def test_smart_money_selling_downgrades_and_alerts_once(tmp_path: Path) -> None:
    cfg = REAL_CFG.model_copy(update={"monitor": MonitorConfig(reeval_seconds=300)})
    env = make_env(tmp_path, [WorldToken(GOOD, sells_after_seconds=350)], cfg=cfg)

    await simulate(env, 1300)

    verdicts = [s.verdict for s in snapshots(env)]
    assert verdicts[0] == "GREEN"
    assert verdicts[-1] == "AVOID"
    assert sorted(alert_types(env)) == ["new_green", "sm_selling", "verdict_downgrade"]
    assert len(snapshots(env)) == 3  # AVOID leaves the watchlist, so no further re-evaluation


# --- failure isolation ---


@pytest.mark.asyncio
async def test_a_failing_token_does_not_block_the_others(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def bad_trades(body: dict[str, Any], now: Any) -> Exception | None:
        if body["token_address"] == BAD:
            return NansenAPIError(status=400, message="bad request")
        return None

    env = make_env(
        tmp_path,
        [WorldToken(GOOD, market_cap=3e6), WorldToken(BAD, market_cap=2e6)],
        failures={"/api/v1/tgm/dex-trades": bad_trades},
    )

    with caplog.at_level(logging.WARNING):
        await simulate(env, 60)

    assert len(snapshots(env, GOOD)) == 1
    assert snapshots(env, BAD) == []
    bad_calls = [
        b for b in env.client.calls_to("/api/v1/tgm/dex-trades") if b["token_address"] == BAD
    ]
    assert len(bad_calls) == 3  # three attempts, then given up
    assert "giving up on token" in caplog.text
    assert env.worker._pending == {}


@pytest.mark.asyncio
async def test_unexpected_errors_in_one_token_are_logged_and_survived(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(body: dict[str, Any], now: Any) -> Exception | None:
        return RuntimeError("boom") if body["token_address"] == BAD else None

    env = make_env(
        tmp_path,
        [WorldToken(GOOD, market_cap=3e6), WorldToken(BAD, market_cap=2e6)],
        failures={"/api/v1/tgm/dex-trades": explode},
    )

    with caplog.at_level(logging.ERROR):
        await simulate(env, 30)

    assert len(snapshots(env, GOOD)) == 1
    assert "token analysis failed" in caplog.text


@pytest.mark.asyncio
async def test_a_token_without_a_deployment_date_is_skipped(tmp_path: Path) -> None:
    env = make_env(tmp_path, [WorldToken(GOOD, has_deployment_date=False)])

    await simulate(env, 30)

    assert snapshots(env) == []
    assert env.worker._pending == {}


# --- Nansen outage and budget ---


@pytest.mark.asyncio
async def test_an_outage_backs_off_and_recovers_with_the_heartbeat_alive(
    tmp_path: Path,
) -> None:
    def down(body: dict[str, Any], now: Any) -> Exception | None:
        if now < START + timedelta(seconds=100):
            return NansenAPIError(status=503, message="down")
        return None

    env = make_env(tmp_path, failures={"/api/v1/token-screener": down})

    await simulate(env, 95)
    failed_calls = len(env.client.calls_to("/api/v1/token-screener"))
    with env.factory() as session:
        beat = repo.last_beat(session)
    assert failed_calls <= 3  # 30s then 60s backoff, not one attempt per tick
    assert beat is not None and env.clock.now() - beat <= timedelta(seconds=10)
    assert snapshots(env) == []

    await simulate(env, 200)
    assert len(snapshots(env)) == 1


@pytest.mark.asyncio
async def test_transport_errors_also_trigger_backoff(tmp_path: Path) -> None:
    def broken(body: dict[str, Any], now: Any) -> Exception | None:
        return httpx.ConnectError("no route")

    env = make_env(tmp_path, failures={"/api/v1/token-screener": broken})

    await simulate(env, 60)

    assert len(env.client.calls_to("/api/v1/token-screener")) <= 2


@pytest.mark.asyncio
async def test_budget_exhaustion_pauses_until_the_next_utc_day(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.client.restore_credits_used_today(3000)  # today's whole budget is already spent

    await simulate(env, 60)

    assert len(env.client.calls) == 1  # the first call hit the guard, then the worker paused
    with env.factory() as session:
        assert repo.get_credits_used(session, START.date()) == 3000
        assert repo.last_beat(session) == env.clock.now() - timedelta(seconds=5)

    env.clock.advance(24 * 3600)
    await env.worker.tick()

    assert len(env.client.calls) > 1  # resumed on the new day
    assert env.client.credits_used_today < 3000


@pytest.mark.asyncio
async def test_an_unparseable_feed_response_backs_off_instead_of_hammering_the_api(
    tmp_path: Path,
) -> None:
    def garbage(body: dict[str, Any], now: Any) -> Exception | None:
        return ValueError("response did not match the model")

    env = make_env(tmp_path, failures={"/api/v1/token-screener": garbage})

    await simulate(env, 95)

    assert len(env.client.calls_to("/api/v1/token-screener")) <= 3


@pytest.mark.asyncio
async def test_a_tiny_budget_warns_and_never_spends_on_deep_analysis(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(tmp_path, daily_budget=90)  # 3.75 credits an hour

    with caplog.at_level(logging.WARNING):
        await simulate(env, 120)

    assert "DAILY_CREDIT_BUDGET is too small" in caplog.text
    assert snapshots(env) == []
    assert env.client.credits_used_today <= 4  # never more than the hourly share
    assert env.client.calls_to("/api/v1/smart-money/dex-trades") == []


@pytest.mark.asyncio
async def test_the_hourly_credit_allowance_gates_deep_analysis(tmp_path: Path) -> None:
    env = make_env(tmp_path, daily_budget=720)  # 30 credits an hour, below one analysis

    await simulate(env, 60)

    assert snapshots(env) == []
    assert GOOD in {key[1] for key in env.worker._pending}
    assert env.client.calls_to("/api/v1/tgm/dex-trades") == []


@pytest.mark.asyncio
async def test_max_deep_analyses_per_hour_is_a_ceiling(tmp_path: Path) -> None:
    budget = REAL_CFG.budget.model_copy(update={"max_deep_analyses_per_hour": 1})
    cfg = REAL_CFG.model_copy(update={"budget": budget})
    env = make_env(tmp_path, [WorldToken(GOOD, market_cap=3e6), WorldToken(BAD)], cfg=cfg)

    await simulate(env, 60)

    assert len(snapshots(env, GOOD)) == 1
    assert snapshots(env, BAD) == []


# --- restart, cooldown and notifier failures ---


@pytest.mark.asyncio
async def test_restart_restores_credits_and_does_not_repeat_work(tmp_path: Path) -> None:
    first = make_env(tmp_path)
    await simulate(first, 30)
    spent = first.client.credits_used_today

    second = make_env(tmp_path, clock=first.clock, engine=first.engine)
    await simulate(second, 30)

    assert second.client.credits_used_today >= spent  # restored, then only discovery added
    assert len(snapshots(second)) == 1
    assert len(first.notifier.messages) == 1
    assert second.notifier.messages == []


@pytest.mark.asyncio
async def test_cooldown_suppresses_a_repeated_alert(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    with env.factory() as session:
        candidate = Candidate(
            chain="solana",
            token_address=GOOD,
            token_symbol="TOK",
            token_age_hours=0.5,
            market_cap_usd=2e6,
            token_deployment_date=START - timedelta(minutes=30),
        )
        token = repo.upsert_token(session, candidate, START)
        repo.record_alert(session, token.id, "new_green", START - timedelta(minutes=30), "earlier")

    await env.worker.tick()

    assert env.notifier.messages == []
    assert alert_types(env) == ["new_green"]  # only the pre-existing one
    assert len(snapshots(env)) == 1


@pytest.mark.asyncio
async def test_a_failed_send_is_not_recorded_and_does_not_stop_the_snapshot(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, notifier=RecordingNotifier(outcome=False))

    await env.worker.tick()

    assert len(snapshots(env)) == 1
    assert alert_types(env) == []


@pytest.mark.asyncio
async def test_a_raising_notifier_is_survived(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(tmp_path, notifier=RecordingNotifier(raises=True))

    with caplog.at_level(logging.ERROR):
        await env.worker.tick()

    assert len(snapshots(env)) == 1
    assert "notifier raised" in caplog.text


@pytest.mark.asyncio
async def test_token_rows_carry_the_discovery_metadata(tmp_path: Path) -> None:
    env = make_env(tmp_path, [WorldToken(GOOD, symbol="ABC")])

    await env.worker.tick()

    with env.factory() as session:
        token = session.scalar(select(TokenRow))
    assert token is not None
    assert token.symbol == "ABC"
    assert token.deployed_at == START - timedelta(minutes=30)
    assert set(token.feeds) == {"screener", "smart_money"}
