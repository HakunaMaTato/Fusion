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
from jinja2 import pass_context
from jinja2.runtime import Context
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, load_scoring_config
from app.pipeline.scoring import Reason
from app.pipeline.smart_money import SmartMoneyMetrics
from app.storage import repo
from app.storage.db import get_session_factory
from app.storage.tables import ClusterRow, SnapshotRow, TokenRow
from app.web import format as fmt
from app.web.charts import ScorePoint, score_history_svg
from app.web.links import NANSEN_APP_URL, token_explorer_url, wallet_explorer_url

logger = logging.getLogger(__name__)

VERDICTS = ("GREEN", "WATCH", "AVOID")
HEARTBEAT_WATCH_AGE = timedelta(minutes=3)
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
TABLE_STALE_AFTER = timedelta(minutes=30)  # §5.4: "Updated" turns --watch after this
_CHAIN = re.compile(r"^[a-z0-9_-]{1,32}$")
_ADDRESS = re.compile(r"^[A-Za-z0-9]{1,128}$")
_SORT_DIRS = ("asc", "desc")
_SEARCH_MAX_LEN = 128

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    money=fmt.money,
    percent=fmt.percent,
    usd=fmt.usd,
    pct=fmt.pct,
    qty=fmt.qty,
    ratio=fmt.ratio,
    age=fmt.age,
    ago=fmt.age_since,
    short=fmt.short_address,
    cluster_reason=fmt.cluster_reason,
    component=fmt.component_label,
    component_tooltip=fmt.component_tooltip,
    chain_class=fmt.chain_class,
    chain_label=fmt.chain_label,
    avatar_hue=fmt.avatar_hue,
)


@pass_context
def _list_url(context: Context, **overrides: object) -> str:
    """A "/" or "/partials/tokens"-relative query string for the list page's filters and sort.

    Starts from the CURRENT filter state in the template context (set by _table_context below)
    and applies `overrides`; a `None` override removes that key. Keeps every filter/sort/search
    link in index.html and _tokens_table.html from having to hand-build a query string that
    remembers every other active filter (§5.3: "All filter and sort state lives in URL query
    parameters").
    """
    values: dict[str, object] = {
        "verdict": context.get("verdict"),
        "chain": context.get("chain"),
        "q": context.get("search"),
        "sort": context.get("sort"),
        "dir": context.get("sort_dir") if context.get("sort") else None,
        "aged": "1" if context.get("aged") else None,
    }
    values.update(overrides)
    query = {str(k): v for k, v in values.items() if v}
    return f"?{urlencode(query)}" if query else ""


templates.env.globals.update(wallet_url=wallet_explorer_url, list_url=_list_url)

router = APIRouter()
SessionFactory = Callable[[], Session]


@dataclass
class TokenRowView:
    chain: str
    address: str
    symbol: str
    age_hours: float | None
    market_cap_usd: float | None
    vol_liq_ratio: float | None
    verdict: str
    score: float
    score_variant: str
    bundle_supply_pct: float
    bundle_severity: str
    sm_wallets: int
    updated_at: datetime
    stale: bool


def _vol_liq_ratio(volume_usd: float | None, liquidity_usd: float | None) -> float | None:
    if volume_usd is None or not liquidity_usd:
        return None
    return volume_usd / liquidity_usd


def _row_view(token: TokenRow, snapshot: SnapshotRow, now: datetime) -> TokenRowView:
    age = (now - token.deployed_at).total_seconds() / 3600 if token.deployed_at else None
    return TokenRowView(
        chain=token.chain,
        address=token.token_address,
        symbol=token.symbol,
        age_hours=age,
        market_cap_usd=snapshot.market_cap_usd,
        vol_liq_ratio=_vol_liq_ratio(snapshot.volume_usd, snapshot.liquidity_usd),
        verdict=snapshot.verdict,
        score=snapshot.score,
        score_variant=fmt.verdict_meter_variant(snapshot.verdict),
        bundle_supply_pct=snapshot.bundle_supply_pct,
        bundle_severity=fmt.bundle_severity(snapshot.bundle_supply_pct),
        sm_wallets=int(snapshot.smart_money.get("wallet_count", 0)),
        updated_at=snapshot.created_at,
        stale=now - snapshot.created_at > TABLE_STALE_AFTER,
    )


@dataclass
class HeaderStatus:
    """Feeds the header's live status indicator (§5.1) on every page, not just /status."""

    heartbeat_class: str  # "ok" | "watch" | "avoid", per HEARTBEAT_WATCH_AGE / HEARTBEAT_MAX_AGE
    last_scan: datetime | None


def _header_status(session: Session, now: datetime) -> HeaderStatus:
    beat = repo.last_beat(session)
    if beat is None or now - beat > HEARTBEAT_MAX_AGE:
        heartbeat_class = "avoid"
    elif now - beat > HEARTBEAT_WATCH_AGE:
        heartbeat_class = "watch"
    else:
        heartbeat_class = "ok"
    return HeaderStatus(heartbeat_class=heartbeat_class, last_scan=repo.last_discovery_at(session))


def render(
    request: Request, name: str, context: dict[str, object] | None = None, status: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context or {}, status_code=status)


def error_page(request: Request, status: int, message: str) -> HTMLResponse:
    return render(request, "error.html", {"status": status, "message": message}, status)


def _filters(
    verdict: str | None,
    chain: str | None,
    search: str | None,
    sort: str | None,
    sort_dir: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str]:
    search = search.strip()[:_SEARCH_MAX_LEN] if search else None
    return (
        verdict if verdict in VERDICTS else None,
        chain if chain is not None and _CHAIN.match(chain) else None,
        search or None,
        sort if sort in repo.SORT_KEYS else None,
        sort_dir if sort_dir in _SORT_DIRS else "desc",
    )


def _table_context(
    open_session: SessionFactory,
    verdict: str | None,
    chain: str | None,
    search: str | None,
    sort: str | None,
    sort_dir: str | None,
    aged: bool,
) -> dict[str, object]:
    verdict, chain, search, sort, sort_dir = _filters(verdict, chain, search, sort, sort_dir)
    query = {
        k: v
        for k, v in (
            ("verdict", verdict),
            ("chain", chain),
            ("q", search),
            ("sort", sort),
            ("dir", sort_dir if sort else None),
            ("aged", "1" if aged else None),
        )
        if v
    }
    now = datetime.now(UTC)
    max_age_hours = load_scoring_config().discovery.max_age_hours
    min_deployed_at = None if aged else now - timedelta(hours=max_age_hours)
    context: dict[str, object] = {
        "rows": [],
        "chains": [],
        "counts": {v: 0 for v in VERDICTS},
        "summary_segments": [],
        "total_listed": 0,
        "verdict": verdict,
        "chain": chain,
        "search": search,
        "sort": sort,
        "sort_dir": sort_dir,
        "aged": aged,
        "verdicts": VERDICTS,
        "unavailable": False,
        "partial_url": "/partials/tokens" + (f"?{urlencode(query)}" if query else ""),
        "now": now,
        "credits_used": 0,
        "daily_credit_budget": 0,
        "heartbeat_class": "avoid",
        "last_scan": None,
    }
    try:
        with open_session() as session:
            rows = repo.list_tokens(
                session,
                verdict=verdict,
                chain=chain,
                search=search,
                min_deployed_at=min_deployed_at,
                sort=sort,
                sort_dir=sort_dir,
                now=now,
            )
            context["rows"] = [_row_view(token, snapshot, now) for token, snapshot in rows]
            context["chains"] = repo.list_chains(session)
            counts = repo.verdict_counts(session, chain=chain, min_deployed_at=min_deployed_at)
            context["counts"] = counts
            total = sum(counts.values())
            context["summary_segments"] = [
                (v, counts[v], fmt.width_class(100 * counts[v] / total))
                for v in VERDICTS
                if counts[v]
            ]
            context["total_listed"] = total
            context["credits_used"] = repo.get_credits_used(session, now.date())
            status = _header_status(session, now)
            context["heartbeat_class"] = status.heartbeat_class
            context["last_scan"] = status.last_scan
    except SQLAlchemyError:
        logger.exception("dashboard could not read the token list")
        context["unavailable"] = True
    return context


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    verdict: str | None = None,
    chain: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the query param name the URL/spec uses
    aged: bool = False,
    open_session: SessionFactory = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    context = _table_context(open_session, verdict, chain, q, sort, dir, aged)
    context["daily_credit_budget"] = settings.daily_credit_budget
    return render(request, "index.html", context)


@router.get("/partials/tokens", response_class=HTMLResponse)
def tokens_partial(
    request: Request,
    verdict: str | None = None,
    chain: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002
    aged: bool = False,
    open_session: SessionFactory = Depends(get_session_factory),
) -> HTMLResponse:
    return render(
        request,
        "_tokens_table.html",
        _table_context(open_session, verdict, chain, q, sort, dir, aged),
    )


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


@dataclass
class ComponentView:
    """One row of §6.4 "How the score is built": points earned out of the points available."""

    name: str
    label: str
    tooltip: str | None
    value: float  # 0..1, the raw component score
    weight: float  # points available (weights sum to 100)
    points: float  # value * weight, what this component actually contributed
    width_class: str  # points as a share of the 0-100 score bar (fmt.width_class)


def _component_views(
    components: dict[str, float], weights: dict[str, float]
) -> list[ComponentView]:
    return [
        ComponentView(
            name=name,
            label=fmt.component_label(name),
            tooltip=fmt.component_tooltip(name),
            value=value,
            weight=weights.get(name, 0.0),
            points=value * weights.get(name, 0.0),
            width_class=fmt.width_class(value * weights.get(name, 0.0)),
        )
        for name, value in components.items()
    ]


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
    now = datetime.now(UTC)
    header_status = HeaderStatus(heartbeat_class="avoid", last_scan=None)
    try:
        with open_session() as session:
            token = repo.find_token(session, chain, address)
            history = repo.snapshot_history(session, token.id) if token is not None else []
            latest = history[-1] if history else None
            clusters = [_cluster_view(c) for c in latest.clusters] if latest else []
            header_status = _header_status(session, now)
    except SQLAlchemyError:
        logger.exception("dashboard could not read a token")
        return error_page(request, 503, "Data is temporarily unavailable.")
    if token is None or latest is None:
        return error_page(request, 404, "Token not found.")

    chart = score_history_svg(
        [ScorePoint(s.created_at, s.score, s.verdict) for s in history],
        watch_min=scoring.watch_min,
        green_min=scoring.green_min,
    )
    age_hours = (now - token.deployed_at).total_seconds() / 3600 if token.deployed_at else None
    smart = SmartMoneyMetrics.model_validate(latest.smart_money)
    reasons = [Reason.model_validate(r) for r in latest.reasons]
    # §6.6: the headline sentence's "peak" is the bundle as a whole (all clusters combined), not
    # any one cluster, so the max of the already-stored per-snapshot aggregate is enough — no
    # per-cluster history is needed (clusters aren't given a stable identity across snapshots).
    bundle_peak_supply_pct = max(s.bundle_supply_pct for s in history)
    holders_per_hour = (
        smart.total_holders / max(age_hours, 1.0)
        if smart.total_holders is not None and age_hours is not None
        else None
    )
    return render(
        request,
        "token.html",
        {
            "token": token,
            "latest": latest,
            "age_hours": age_hours,
            "smart": smart,
            "clusters": clusters,
            "chart": chart,
            "history_count": len(history),
            "explorer_url": token_explorer_url(chain, address),
            "nansen_url": NANSEN_APP_URL,
            "now": now,
            "heartbeat_class": header_status.heartbeat_class,
            "last_scan": header_status.last_scan,
            "strengths": [r for r in reasons if r.polarity == "positive"],
            "risks": [r for r in reasons if r.polarity == "risk"],
            "components": _component_views(latest.components, scoring.weights.model_dump()),
            "bundle_peak_supply_pct": bundle_peak_supply_pct,
            "holders_per_hour": holders_per_hour,
            "vol_liq_ratio": _vol_liq_ratio(latest.volume_usd, latest.liquidity_usd),
            "bundle_wallet_count": sum(len(c.wallets) for c in clusters),
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
        "heartbeat_class": "avoid",
        "last_scan": None,
    }
    try:
        with open_session() as session:
            context["credits_used"] = repo.get_credits_used(session, now.date())
            beat = repo.last_beat(session)
            context["beat"] = beat
            context["last_discovery"] = repo.last_discovery_at(session)
            context["last_analysis"] = repo.last_analysis_at(session)
            context["last_scan"] = context["last_discovery"]
    except SQLAlchemyError:
        logger.exception("dashboard could not read the status")
        context["db_ok"] = False
        beat = None
    if beat is None:
        context["health"] = "no worker heartbeat"
        context["health_class"] = "verdict-avoid"
        context["heartbeat_class"] = "avoid"
    elif now - beat > HEARTBEAT_MAX_AGE:
        context["health"] = "worker heartbeat is stale"
        context["health_class"] = "verdict-avoid"
        context["heartbeat_class"] = "avoid"
    else:
        context["health"] = "worker is healthy"
        context["health_class"] = "verdict-green"
        context["heartbeat_class"] = "watch" if now - beat > HEARTBEAT_WATCH_AGE else "ok"
    return render(request, "status.html", context)
