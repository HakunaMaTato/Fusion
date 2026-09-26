from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.pipeline.analyze import TokenAnalysis
from app.pipeline.discovery import Candidate
from app.pipeline.monitor import SnapshotState
from app.storage.tables import (
    AlertSentRow,
    ClusterRow,
    ClusterWalletRow,
    CreditUsageRow,
    HeartbeatRow,
    SnapshotRow,
    TokenRow,
)

HEARTBEAT_ID = 1
VERDICT_ORDER = {"GREEN": 0, "WATCH": 1, "AVOID": 2}


def find_token(session: Session, chain: str, token_address: str) -> TokenRow | None:
    return session.scalar(
        select(TokenRow).where(TokenRow.chain == chain, TokenRow.token_address == token_address)
    )


def upsert_token(session: Session, candidate: Candidate, now: datetime) -> TokenRow:
    token = find_token(session, candidate.chain, candidate.token_address)
    if token is None:
        token = TokenRow(
            chain=candidate.chain,
            token_address=candidate.token_address,
            symbol=candidate.token_symbol,
            deployed_at=candidate.token_deployment_date,
            first_seen_at=now,
            feeds={name: seen.isoformat() for name, seen in candidate.feeds.items()},
        )
        session.add(token)
    else:
        token.symbol = candidate.token_symbol
        if token.deployed_at is None:
            token.deployed_at = candidate.token_deployment_date
        token.feeds = {**token.feeds, **{n: s.isoformat() for n, s in candidate.feeds.items()}}
    session.commit()
    return token


def latest_snapshot(session: Session, token_id: int) -> SnapshotRow | None:
    return session.scalar(
        select(SnapshotRow)
        .where(SnapshotRow.token_id == token_id)
        .order_by(SnapshotRow.created_at.desc(), SnapshotRow.id.desc())
        .limit(1)
    )


def snapshot_state(row: SnapshotRow) -> SnapshotState:
    return SnapshotState(
        verdict=row.verdict, bundle_status=row.bundle_status, sm_net_selling=row.sm_net_selling
    )


def save_snapshot(session: Session, token: TokenRow, analysis: TokenAnalysis) -> SnapshotRow:
    snapshot = SnapshotRow(
        token_id=token.id,
        created_at=analysis.analyzed_at,
        score=analysis.score.score,
        verdict=analysis.score.verdict,
        vetoes=analysis.score.vetoes,
        reasons=analysis.score.reasons,
        components=analysis.score.components,
        smart_money=analysis.smart_money.model_dump(mode="json"),
        bundle_status=analysis.bundle.status,
        bundle_supply_pct=analysis.bundle.supply_pct,
        sm_net_selling=analysis.smart_money.net_selling,
        market_cap_usd=analysis.candidate.market_cap_usd,
        token_age_hours=analysis.token_age_hours,
        volume_usd=analysis.candidate.volume_usd,
        liquidity_usd=analysis.candidate.liquidity_usd,
    )
    for cluster in analysis.bundle.clusters:
        cluster_row = ClusterRow(
            reasons=sorted(cluster.reasons),
            funders=sorted(cluster.funders),
            similar_size=cluster.similar_size,
        )
        cluster_row.wallets = [ClusterWalletRow(wallet=w) for w in sorted(cluster.wallets)]
        snapshot.clusters.append(cluster_row)
    session.add(snapshot)
    session.commit()
    return snapshot


def watchlist(
    session: Session, now: datetime, max_age_hours: float
) -> list[tuple[TokenRow, SnapshotRow]]:
    """Tokens younger than max_age_hours whose latest verdict is not AVOID."""
    oldest = now - timedelta(hours=max_age_hours)
    tokens = session.scalars(select(TokenRow).where(TokenRow.deployed_at >= oldest)).all()
    items: list[tuple[TokenRow, SnapshotRow]] = []
    for token in tokens:
        snapshot = latest_snapshot(session, token.id)
        if snapshot is not None and snapshot.verdict != "AVOID":
            items.append((token, snapshot))
    return items


def get_credits_used(session: Session, day: date) -> int:
    row = session.get(CreditUsageRow, day)
    return row.credits_used if row is not None else 0


def set_credits_used(session: Session, day: date, credits_used: int) -> None:
    row = session.get(CreditUsageRow, day)
    if row is None:
        session.add(CreditUsageRow(day=day, credits_used=credits_used))
    else:
        row.credits_used = credits_used
    session.commit()


def last_alert_at(session: Session, token_id: int, event_type: str) -> datetime | None:
    return session.scalar(
        select(AlertSentRow.sent_at)
        .where(AlertSentRow.token_id == token_id, AlertSentRow.event_type == event_type)
        .order_by(AlertSentRow.sent_at.desc())
        .limit(1)
    )


def record_alert(
    session: Session, token_id: int, event_type: str, sent_at: datetime, message: str
) -> None:
    session.add(
        AlertSentRow(
            token_id=token_id, event_type=event_type, sent_at=sent_at, message=message[:4096]
        )
    )
    session.commit()


def beat(session: Session, now: datetime) -> None:
    row = session.get(HeartbeatRow, HEARTBEAT_ID)
    if row is None:
        session.add(HeartbeatRow(id=HEARTBEAT_ID, beat_at=now))
    else:
        row.beat_at = now
    session.commit()


def last_beat(session: Session) -> datetime | None:
    row = session.get(HeartbeatRow, HEARTBEAT_ID)
    return row.beat_at if row is not None else None


def record_discovery(session: Session, now: datetime) -> None:
    row = session.get(HeartbeatRow, HEARTBEAT_ID)
    if row is None:
        session.add(HeartbeatRow(id=HEARTBEAT_ID, beat_at=now, last_discovery_at=now))
    else:
        row.last_discovery_at = now
    session.commit()


def last_discovery_at(session: Session) -> datetime | None:
    row = session.get(HeartbeatRow, HEARTBEAT_ID)
    return row.last_discovery_at if row is not None else None


def last_analysis_at(session: Session) -> datetime | None:
    return session.scalar(select(func.max(SnapshotRow.created_at)))


def list_tokens(
    session: Session,
    *,
    verdict: str | None = None,
    chain: str | None = None,
    updated_since: datetime | None = None,
    limit: int = 200,
) -> list[tuple[TokenRow, SnapshotRow]]:
    """Each token with its latest snapshot: GREEN first, then WATCH, then AVOID, by score.

    `updated_since` hides tokens whose latest snapshot is older, so stale verdicts drop off.
    """
    latest = select(func.max(SnapshotRow.id)).group_by(SnapshotRow.token_id).scalar_subquery()
    stmt = (
        select(TokenRow, SnapshotRow)
        .join(SnapshotRow, SnapshotRow.token_id == TokenRow.id)
        .where(SnapshotRow.id.in_(latest))
    )
    if verdict is not None:
        stmt = stmt.where(SnapshotRow.verdict == verdict)
    if chain is not None:
        stmt = stmt.where(TokenRow.chain == chain)
    if updated_since is not None:
        stmt = stmt.where(SnapshotRow.created_at >= updated_since)
    rows = [(token, snapshot) for token, snapshot in session.execute(stmt)]
    rows.sort(key=lambda pair: (VERDICT_ORDER.get(pair[1].verdict, 3), -pair[1].score))
    return rows[:limit]


def list_chains(session: Session) -> list[str]:
    return list(session.scalars(select(TokenRow.chain).distinct().order_by(TokenRow.chain)))


def snapshot_history(session: Session, token_id: int, limit: int = 200) -> list[SnapshotRow]:
    """The newest `limit` snapshots, oldest first."""
    newest = session.scalars(
        select(SnapshotRow)
        .where(SnapshotRow.token_id == token_id)
        .order_by(SnapshotRow.created_at.desc(), SnapshotRow.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(newest))
