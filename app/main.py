import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.storage import repo
from app.storage.db import get_session_factory
from app.web.routes import error_page
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)

HEARTBEAT_MAX_AGE = timedelta(minutes=5)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

app = FastAPI(title="LP Radar", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    message = "Page not found." if exc.status_code == 404 else "The request could not be handled."
    return error_page(request, exc.status_code, message)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> HTMLResponse:
    logger.error("unhandled error", exc_info=exc)
    return error_page(request, 500, "Something went wrong. Please try again later.")


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


app.include_router(web_router)
