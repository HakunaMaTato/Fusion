from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.storage import repo
from app.storage.db import get_session_factory, init_db, make_engine, make_session_factory


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'health.db'}")
    init_db(engine)
    sessions = make_session_factory(engine)
    app.dependency_overrides[get_session_factory] = lambda: sessions
    yield sessions
    app.dependency_overrides.clear()


def beat(factory: Callable[[], Session], age: timedelta) -> None:
    with factory() as session:
        repo.beat(session, datetime.now(UTC) - age)


def test_healthz_is_ok_with_a_fresh_heartbeat(factory: Callable[[], Session]) -> None:
    beat(factory, timedelta(seconds=10))

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_unhealthy_when_the_heartbeat_is_stale(factory: Callable[[], Session]) -> None:
    beat(factory, timedelta(minutes=5, seconds=5))

    response = TestClient(app).get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "reason": "worker heartbeat is stale"}


def test_healthz_is_ok_just_inside_five_minutes(factory: Callable[[], Session]) -> None:
    beat(factory, timedelta(minutes=4, seconds=50))

    assert TestClient(app).get("/healthz").status_code == 200


def test_healthz_is_unhealthy_without_any_heartbeat(factory: Callable[[], Session]) -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 503
    assert response.json()["reason"] == "no worker heartbeat"


def test_healthz_is_unhealthy_when_the_database_is_unavailable() -> None:
    def broken() -> Session:
        raise RuntimeError("no database")

    app.dependency_overrides[get_session_factory] = lambda: broken
    try:
        response = TestClient(app).get("/healthz")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["reason"] == "database unavailable"


def test_unknown_route_returns_404() -> None:
    assert TestClient(app).get("/does-not-exist").status_code == 404
