import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.alerts.telegram import Notifier, build_notifier, format_alert
from app.config import ScoringConfig, Settings, get_settings, load_scoring_config
from app.logging_config import configure_logging
from app.nansen.client import NansenClient
from app.nansen.exceptions import BudgetExceeded, NansenAPIError, NansenError
from app.pipeline.analyze import MissingDeploymentDate, TokenAnalysis, analyze_token
from app.pipeline.discovery import Candidate, fetch_feed_a, fetch_feed_b, merge_candidates
from app.pipeline.monitor import (
    detect_events,
    is_due_for_reeval,
    outside_cooldown,
)
from app.storage import repo
from app.storage.db import get_engine, init_db, make_session_factory
from app.storage.tables import SnapshotRow, TokenRow

logger = logging.getLogger(__name__)

TICK_SECONDS = 5.0
OUTAGE_BACKOFF_BASE_SECONDS = 30.0
OUTAGE_BACKOFF_MAX_SECONDS = 300.0
MAX_ANALYSIS_ATTEMPTS = 3
FEED_A_CREDITS = 1  # token-screener
FEED_B_CREDITS = 6  # smart-money trades (5) + one screener confirmation (1)

TokenKey = tuple[str, str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Worker:
    def __init__(
        self,
        *,
        settings: Settings,
        cfg: ScoringConfig,
        client: NansenClient,
        notifier: Notifier,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._settings = settings
        self._cfg = cfg
        self._client = client
        self._notifier = notifier
        self._sessions = session_factory
        self._clock = clock
        self._sleep = sleep
        self._tick_seconds = tick_seconds

        self._last_feed_a: datetime | None = None
        self._last_feed_b: datetime | None = None
        self._pending: dict[TokenKey, Candidate] = {}
        self._attempts: dict[TokenKey, int] = {}
        self._outage_failures = 0
        self._outage_until: datetime | None = None
        self._paused_day: date | None = None
        self._hour_start: datetime | None = None
        self._credits_at_hour_start = 0
        self._analyses_this_hour = 0

    # --- lifecycle ---

    async def run(self, stop: asyncio.Event) -> None:
        self.startup()
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("worker tick failed")
            await self._sleep(self._tick_seconds)

    def startup(self) -> None:
        today = self._clock().date()
        with self._sessions() as session:
            self._client.restore_credits_used_today(repo.get_credits_used(session, today))
        allowance = self._settings.daily_credit_budget / 24
        needed = self._cfg.budget.estimated_analysis_credits + FEED_A_CREDITS
        if allowance < needed:
            logger.warning(
                "DAILY_CREDIT_BUDGET is too small for deep analysis: the hourly share is below "
                "the cost of one analysis, so tokens will be discovered but not analysed",
                extra={"hourly_allowance": round(allowance, 1), "analysis_credits": needed},
            )

    async def tick(self) -> None:
        now = self._clock()
        self._beat(now)
        today = now.date()
        if self._paused_day == today:
            return
        self._paused_day = None
        if self._outage_until is not None and now < self._outage_until:
            return
        self._roll_hour(now)
        try:
            await self._discover(now)
            await self._analyze(now)
        except BudgetExceeded as exc:
            self._paused_day = today
            logger.warning(
                "daily credit budget reached, pausing until the next UTC day",
                extra={"used": exc.used, "budget": exc.budget},
            )
        finally:
            with self._sessions() as session:
                repo.set_credits_used(session, today, self._client.credits_used_today)

    # --- discovery ---

    def _due(self, last: datetime | None, now: datetime, seconds: float) -> bool:
        return last is None or now - last >= timedelta(seconds=seconds)

    async def _discover(self, now: datetime) -> None:
        discovery = self._cfg.discovery
        chains = self._settings.chains
        found: list[list[Candidate]] = []
        if self._due(self._last_feed_a, now, discovery.poll_seconds) and self._can_spend(
            FEED_A_CREDITS
        ):
            try:
                found.append(await fetch_feed_a(self._client, discovery, chains, now))
                self._last_feed_a = now
                self._outage_failures = 0
            except BudgetExceeded:
                raise
            except Exception as exc:
                self._note_outage(now, exc, "screener feed")
                return
        if self._due(
            self._last_feed_b, now, discovery.smart_money_poll_seconds
        ) and self._can_spend(FEED_B_CREDITS):
            try:
                found.append(
                    await fetch_feed_b(
                        self._client,
                        discovery,
                        chains,
                        self._cfg.smart_money.min_wallets_for_signal,
                        now,
                    )
                )
                self._last_feed_b = now
                self._outage_failures = 0
            except BudgetExceeded:
                raise
            except Exception as exc:
                self._note_outage(now, exc, "smart money feed")
                return
        with self._sessions() as session:
            for candidate in merge_candidates(*found):
                if candidate.key in self._pending:
                    continue
                token = repo.find_token(session, candidate.chain, candidate.token_address)
                if token is not None and repo.latest_snapshot(session, token.id) is not None:
                    continue
                self._pending[candidate.key] = candidate

    def _note_outage(self, now: datetime, exc: Exception, what: str) -> None:
        self._outage_failures += 1
        delay = min(
            OUTAGE_BACKOFF_BASE_SECONDS * 2 ** (self._outage_failures - 1),
            OUTAGE_BACKOFF_MAX_SECONDS,
        )
        self._outage_until = now + timedelta(seconds=delay)
        logger.warning(
            "nansen unavailable, backing off",
            extra={
                "what": what,
                "error_type": type(exc).__name__,
                "backoff_seconds": delay,
                "failures": self._outage_failures,
            },
        )

    # --- analysis ---

    def _roll_hour(self, now: datetime) -> None:
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour != self._hour_start:
            self._hour_start = hour
            self._credits_at_hour_start = self._client.credits_used_today
            self._analyses_this_hour = 0

    def _can_spend(self, credits: float) -> bool:
        """True while this hour's share of the daily budget (budget / 24) can cover `credits`."""
        hourly_allowance = self._settings.daily_credit_budget / 24
        spent = self._client.credits_used_today - self._credits_at_hour_start
        return hourly_allowance - spent >= credits

    def _can_afford(self, estimated_credits: float) -> bool:
        within_count = self._analyses_this_hour < self._cfg.budget.max_deep_analyses_per_hour
        return within_count and self._can_spend(estimated_credits)

    async def _analyze(self, now: datetime) -> None:
        budget = self._cfg.budget
        for candidate in sorted(self._pending.values(), key=lambda c: -c.market_cap_usd):
            if not self._can_afford(budget.estimated_analysis_credits):
                return
            await self._analyze_one(candidate, now)

        with self._sessions() as session:
            due = [
                (token, snapshot)
                for token, snapshot in repo.watchlist(
                    session, now, self._cfg.discovery.max_age_hours
                )
                if is_due_for_reeval(snapshot.created_at, now, self._cfg.monitor.reeval_seconds)
            ]
        for token, snapshot in sorted(due, key=lambda pair: pair[1].created_at):
            if not self._can_afford(budget.estimated_reeval_credits):
                return
            await self._analyze_one(self._candidate_from_snapshot(token, snapshot, now), now)

    @staticmethod
    def _candidate_from_snapshot(
        token: TokenRow, snapshot: SnapshotRow, now: datetime
    ) -> Candidate:
        age_hours = (now - token.deployed_at).total_seconds() / 3600 if token.deployed_at else 0.0
        return Candidate(
            chain=token.chain,
            token_address=token.token_address,
            token_symbol=token.symbol,
            token_age_hours=age_hours,
            market_cap_usd=snapshot.market_cap_usd or 0.0,
            token_deployment_date=token.deployed_at,
            volume_usd=snapshot.volume_usd,
            liquidity_usd=snapshot.liquidity_usd,
        )

    async def _analyze_one(self, candidate: Candidate, now: datetime) -> None:
        key = candidate.key
        self._analyses_this_hour += 1
        try:
            analysis = await analyze_token(self._client, self._cfg, candidate, now)
        except BudgetExceeded:
            raise
        except MissingDeploymentDate:
            logger.warning("skipping token without deployment date", extra=_token_extra(candidate))
            self._pending.pop(key, None)
            return
        except (NansenError, httpx.HTTPError) as exc:
            self._on_token_failure(candidate, now, exc)
            return
        except Exception:
            logger.exception("token analysis failed", extra=_token_extra(candidate))
            self._register_failure(candidate)
            return

        self._pending.pop(key, None)
        self._attempts.pop(key, None)
        await self._store_and_alert(analysis, now)
        self._beat(self._clock())

    def _on_token_failure(self, candidate: Candidate, now: datetime, exc: Exception) -> None:
        extra: dict[str, object] = {**_token_extra(candidate), "error_type": type(exc).__name__}
        if isinstance(exc, NansenAPIError):
            extra["status"] = exc.status
        logger.warning("token analysis failed", extra=extra)
        server_side = isinstance(exc, httpx.HTTPError) or (
            isinstance(exc, NansenAPIError) and (exc.status >= 500 or exc.status == 429)
        )
        if server_side:
            self._note_outage(now, exc, "token analysis")
        self._register_failure(candidate)

    def _register_failure(self, candidate: Candidate) -> None:
        count = self._attempts.get(candidate.key, 0) + 1
        self._attempts[candidate.key] = count
        if count >= MAX_ANALYSIS_ATTEMPTS:
            logger.error(
                "giving up on token after repeated failures", extra=_token_extra(candidate)
            )
            self._pending.pop(candidate.key, None)
            self._attempts.pop(candidate.key, None)

    # --- storage and alerts ---

    async def _store_and_alert(self, analysis: TokenAnalysis, now: datetime) -> None:
        candidate = analysis.candidate
        with self._sessions() as session:
            token = repo.upsert_token(session, candidate, now)
            previous = repo.latest_snapshot(session, token.id)
            previous_state = repo.snapshot_state(previous) if previous is not None else None
            snapshot = repo.save_snapshot(session, token, analysis)
            events = detect_events(previous_state, repo.snapshot_state(snapshot))
            for event in events:
                last = repo.last_alert_at(session, token.id, event.event_type)
                if not outside_cooldown(last, now, self._cfg.alerts.cooldown_minutes):
                    logger.info(
                        "alert suppressed by cooldown",
                        extra={**_token_extra(candidate), "event": event.event_type},
                    )
                    continue
                text = format_alert(
                    chain=token.chain,
                    symbol=token.symbol,
                    token_address=token.token_address,
                    verdict=snapshot.verdict,
                    detail=event.detail,
                    reasons=analysis.score.reasons,
                    dashboard_base_url=self._settings.dashboard_base_url,
                )
                try:
                    sent = await self._notifier.send(text)
                except Exception:
                    logger.exception("notifier raised", extra=_token_extra(candidate))
                    sent = False
                if sent:
                    repo.record_alert(session, token.id, event.event_type, now, text)

    def _beat(self, now: datetime) -> None:
        with self._sessions() as session:
            repo.beat(session, now)


def _token_extra(candidate: Candidate) -> dict[str, str]:
    return {"chain": candidate.chain, "token_address": candidate.token_address}


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    cfg = load_scoring_config()
    engine = get_engine()
    init_db(engine)
    client = NansenClient(settings)
    notifier = build_notifier(settings)
    worker = Worker(
        settings=settings,
        cfg=cfg,
        client=client,
        notifier=notifier,
        session_factory=make_session_factory(engine),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    logger.info("worker started", extra={"mode": settings.nansen_mode})
    try:
        await worker.run(stop)
    finally:
        await client.aclose()
        logger.info("worker stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
