"""A real, running instance of the app (not FastAPI's TestClient), for Playwright to drive over
HTTP -- needed for the UI_REDESIGN.md §10 screenshot and accessibility pass."""

import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn

from app.config import Settings, get_settings
from app.demo import evm_address, seed_demo, solana_address
from app.main import app
from app.storage.db import get_session_factory, init_db, make_engine, make_session_factory

API_KEY = "nsn_UI_TEST_KEY_NEVER_USED_FOR_A_REAL_CALL"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db_path = tmp_path_factory.mktemp("ui") / "ui.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session, datetime.now(UTC))

    app.dependency_overrides[get_session_factory] = lambda: make_session_factory(engine)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, nansen_api_key=API_KEY, daily_credit_budget=3000, nansen_mode="replay"
    )

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/healthz", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("live_server did not come up in time")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def green_token_path() -> str:
    return f"/token/solana/{solana_address('green')}"


@pytest.fixture(scope="session")
def avoid_token_path() -> str:
    return f"/token/bnb/{evm_address('avoid')}"
