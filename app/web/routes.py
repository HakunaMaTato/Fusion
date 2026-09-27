import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, load_scoring_config
from app.pipeline.smart_money import SmartMoneyMetrics
from app.storage import repo
from app.storage.db import get_session_factory
from app.storage.tables import ClusterRow, SnapshotRow, TokenRow
from app.web import format as fmt
from app.web.charts import ScorePoint, score_history_svg
from app.web.links import NANSEN_APP_URL, token_explorer_url, wallet_explorer_url

logger = logging.getLogger(__name__)

VERDICTS = ("GREEN", "WATCH", "AVOID")
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
_CHAIN = re.compile(r"^[a-z0-9_-]{1,32}$")
_ADDRESS = re.compile(r"^[A-Za-z0-9]{1,128}$")

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    money=fmt.money,
    percent=fmt.percent,
    usd=fmt.usd,
    pct=fmt.pct,
    age=fmt.age,
    ago=fmt.age_since,
    short=fmt.short_address,
    cluster_reason=fmt.cluster_reason,
    component=fmt.component_label,
    chain_class=fmt.chain_class,
    chain_label=fmt.chain_label,
    avatar_hue=fmt.avatar_hue,
)
templates.env.globals.update(wallet_url=wallet_explorer_url)

router = APIRouter()
SessionFactory = Callable[[], Session]


@dataclass
class TokenRowView:
    chain: str
    address: str
    symbol: str
    age_hours: float | None
    market_cap_usd: float | None
    verdict: str
    score: float
    bundle_supply_pct: float
    sm_wallets: int
    updated_at: datetime


def _row_view(token: TokenRow, snapshot: SnapshotRow, now: datetime) -> TokenRowView:
    age = (now - token.deployed_at).total_seconds() / 3600 if token.deployed_at else None
    return TokenRowView(
        chain=token.chain,
        address=token.token_address,
        symbol=token.symbol,
        age_hours=age,
        market_cap_usd=snapshot.market_cap_usd,
        verdict=snapshot.verdict,
        score=snapshot.score,
        bundle_supply_pct=snapshot.bundle_supply_pct,
        sm_wallets=int(snapshot.smart_money.get("wallet_count", 0)),
        updated_at=snapshot.created_at,
    )


def render(
    request: Request, name: str, context: dict[str, object] | None = None, status: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context or {}, status_code=status)


def error_page(request: Request, status: int, message: str) -> HTMLResponse:
    return render(request, "error.html", {"status": status, "message": message}, status)


def _filters(verdict: str | None, chain: str | None) -> tuple[str | None, str | None]:
    return (
        verdict if verdict in VERDICTS else None,
        chain if chain is not None and _CHAIN.match(chain) else None,
    )


def _table_context(
    open_session: SessionFactory, verdict: str | None, chain: str | None
) -> dict[str, object]:
    verdict, chain = _filters(verdict, chain)
    query = {k: v for k, v in (("verdict", verdict), ("chain", chain)) if v}
    now = datetime.now(UTC)
    context: dict[str, object] = {
        "rows": [],
        "chains": [],
        "verdict": verdict,
        "chain": chain,
        "verdicts": VERDICTS,
        "unavailable": False,
        "partial_url": "/partials/tokens" + (f"?{urlencode(query)}" if query else ""),
        "now": now,
    }
    try:
        with open_session() as session:
            rows = repo.list_tokens(
                session,
                verdict=verdict,
                chain=chain,
                updated_since=now - timedelta(hours=load_scoring_config().discovery.max_age_hours),
            )
            context["rows"] = [_row_view(token, snapshot, now) for token, snapshot in rows]
            context["chains"] = repo.list_chains(session)
    except SQLAlchemyError:
        logger.exception("dashboard could not read the token list")
        context["unavailable"] = True
    return context


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    verdict: str | None = None,
    chain: str | None = None,
    open_session: SessionFactory = Depends(get_session_factory),
) -> HTMLResponse:
    return render(request, "index.html", _table_context(open_session, verdict, chain))


@router.get("/partials/tokens", response_class=HTMLResponse)
def tokens_partial(
    request: Request,
    verdict: str | None = None,
    chain: str | None = None,
    open_session: SessionFactory = Depends(get_session_factory),
) -> HTMLResponse:
    return render(request, "_tokens_table.html", _table_context(open_session, verdict, chain))


@dataclass
class ClusterView:
    reasons: list[str]
    funders: list[str]
    similar_size: bool
    wallets: list[str]


def _cluster_view(cluster: ClusterRow) -> ClusterView:
    return ClusterView(
        reasons=list(cluster.reasons),
        funders=list(cluster.funders),
        similar_size=cluster.similar_size,
        wallets=sorted(w.wallet for w in cluster.wallets),
    )


@router.get("/token/{chain}/{address}", response_class=HTMLResponse)
def token_page(
    request: Request,
    chain: str,
    address: str,
    open_session: SessionFactory = Depends(get_session_factory),
) -> HTMLResponse:
    if not _CHAIN.match(chain) or not _ADDRESS.match(address):
        return error_page(request, 404, "Token not found.")
    scoring = load_scoring_config().scoring
    try:
        with open_session() as session:
            token = repo.find_token(session, chain, address)
            history = repo.snapshot_history(session, token.id) if token is not None else []
            latest = history[-1] if history else None
            clusters = [_cluster_view(c) for c in latest.clusters] if latest else []
    except SQLAlchemyError:
        logger.exception("dashboard could not read a token")
        return error_page(request, 503, "Data is temporarily unavailable.")
    if token is None or latest is None:
        return error_page(request, 404, "Token not found.")

    now = datetime.now(UTC)
    chart = score_history_svg(
        [ScorePoint(s.created_at, s.score, s.verdict) for s in history],
        watch_min=scoring.watch_min,
        green_min=scoring.green_min,
    )
    return render(
        request,
        "token.html",
        {
            "token": token,
            "latest": latest,
            "age_hours": (now - token.deployed_at).total_seconds() / 3600
            if token.deployed_at
            else None,
            "smart": SmartMoneyMetrics.model_validate(latest.smart_money),
            "clusters": clusters,
            "chart": chart,
            "history_count": len(history),
            "explorer_url": token_explorer_url(chain, address),
            "nansen_url": NANSEN_APP_URL,
            "now": now,
        },
    )


@router.get("/status", response_class=HTMLResponse)
def status(
    request: Request,
    open_session: SessionFactory = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    now = datetime.now(UTC)
    context: dict[str, object] = {
        "db_ok": True,
        "credits_used": 0,
        "budget": settings.daily_credit_budget,
        "hourly_share": settings.daily_credit_budget / 24,
        "health": "unknown",
        "health_class": "verdict-unknown",
        "beat": None,
        "last_discovery": None,
        "last_analysis": None,
        "now": now,
        "mode": settings.nansen_mode,
    }
    try:
        with open_session() as session:
            context["credits_used"] = repo.get_credits_used(session, now.date())
            beat = repo.last_beat(session)
            context["beat"] = beat
            context["last_discovery"] = repo.last_discovery_at(session)
            context["last_analysis"] = repo.last_analysis_at(session)
    except SQLAlchemyError:
        logger.exception("dashboard could not read the status")
        context["db_ok"] = False
        beat = None
    if beat is None:
        context["health"] = "no worker heartbeat"
        context["health_class"] = "verdict-avoid"
    elif now - beat > HEARTBEAT_MAX_AGE:
        context["health"] = "worker heartbeat is stale"
        context["health_class"] = "verdict-avoid"
    else:
        context["health"] = "worker is healthy"
        context["health_class"] = "verdict-green"
    return render(request, "status.html", context)
