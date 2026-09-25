"""Runs the worker for ten real minutes against the scripted Nansen. Slow: `make soak`."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings, load_scoring_config
from app.storage import repo
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.tables import SnapshotRow
from app.worker import Worker
from tests.scripted import FakeClock, RecordingNotifier, ScriptedNansen, WorldToken

SOAK_SECONDS = 600


class RealClock(FakeClock):
    """Wall-clock time with the same interface as FakeClock."""

    def __init__(self) -> None:
        super().__init__(datetime.now(UTC))

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@pytest.mark.soak
@pytest.mark.asyncio
async def test_worker_runs_ten_real_minutes_without_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = RealClock()
    engine = make_engine(f"sqlite:///{tmp_path / 'soak.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    settings = Settings(_env_file=None, nansen_mode="replay", chains=["solana"])
    client = ScriptedNansen(settings, clock, [WorldToken("SOAKtoken")])
    notifier = RecordingNotifier()
    worker = Worker(
        settings=settings,
        cfg=load_scoring_config(),
        client=client,
        notifier=notifier,
        session_factory=factory,
        clock=clock.now,
        sleep=clock.sleep,
    )
    stop = asyncio.Event()
    deadline = clock.now() + timedelta(seconds=SOAK_SECONDS)

    async def stop_at_deadline() -> None:
        while clock.now() < deadline:
            await asyncio.sleep(1)
        stop.set()

    with caplog.at_level(logging.WARNING):
        await asyncio.gather(worker.run(stop), stop_at_deadline())

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    with factory() as session:
        beat = repo.last_beat(session)
        assert len(session.scalars(select(SnapshotRow)).all()) == 1
    assert beat is not None and clock.now() - beat <= timedelta(seconds=10)
    assert len(notifier.messages) == 1
