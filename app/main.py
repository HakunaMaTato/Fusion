from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.storage import repo
from app.storage.db import get_session_factory

HEARTBEAT_MAX_AGE = timedelta(minutes=5)

app = FastAPI(title="LP Radar")


def _unhealthy(reason: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "unhealthy", "reason": reason})


@app.get("/healthz", response_model=None)
def healthz(
    open_session: Callable[[], Session] = Depends(get_session_factory),
) -> JSONResponse | dict[str, str]:
    try:
        with open_session() as session:
            beat = repo.last_beat(session)
    except Exception:
        return _unhealthy("database unavailable")
    if beat is None:
        return _unhealthy("no worker heartbeat")
    if datetime.now(UTC) - beat > HEARTBEAT_MAX_AGE:
        return _unhealthy("worker heartbeat is stale")
    return {"status": "ok"}
