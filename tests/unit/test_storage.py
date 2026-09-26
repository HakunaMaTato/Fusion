from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.nansen.client import NansenClient
from app.pipeline.analyze import MissingDeploymentDate, TokenAnalysis, analyze_token
from app.pipeline.bundle import BundleAnalysis, Cluster
from app.pipeline.discovery import Candidate
from app.pipeline.scoring import ScoreResult
from app.pipeline.smart_money import SmartMoneyMetrics
from app.storage import repo
from app.storage.db import init_db, make_engine, make_session_factory
from tests.factories import StubClient

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine: Engine) -> Session:
    with make_session_factory(engine)() as session:
        yield session  # type: ignore[misc]


def candidate(
    address: str = "tok", deployed: datetime | None = NOW - timedelta(hours=1)
) -> Candidate:
    return Candidate(
        chain="solana",
        token_address=address,
        token_symbol="TOK",
        token_age_hours=1.0,
        market_cap_usd=2e6,
        feeds={"screener": NOW},
        token_deployment_date=deployed,
        volume_usd=5e5,
        liquidity_usd=1e5,
    )


def analysis(
    cand: Candidate, verdict: str = "GREEN", at: datetime = NOW, with_cluster: bool = False
) -> TokenAnalysis:
    clusters = (
        [
            Cluster(
                wallets=frozenset({"w1", "w2", "w3"}),
                reasons=frozenset({"same_second"}),
                funders=frozenset({"F"}),
                similar_size=True,
            )
        ]
        if with_cluster
        else []
    )
    return TokenAnalysis(
        candidate=cand,
        bundle=BundleAnalysis(
            clusters=clusters,
            bundled_wallets=frozenset({"w1", "w2", "w3"}) if with_cluster else frozenset(),
            supply_pct=12.5,
            sold_pct=0.0,
            status="holding" if with_cluster else "none",
        ),
        smart_money=SmartMoneyMetrics(
            wallet_count=2,
            signal=True,
            label_counts={"Fund": 1},
            weighted_score=2.5,
            net_flow_usd=100.0,
            net_selling=False,
            bought_tokens=10.0,
            sold_tokens=0.0,
            holding_ratio=1.0,
            wallets_still_holding=2,
            first_entry_at=at,
            minutes_after_launch=2.0,
            market_cap_at_entry=1e6,
            smart_wallets=frozenset({"sm0", "sm1"}),
            total_holders=10,
        ),
        smart_wallets_in_bundle=frozenset(),
        score=ScoreResult(
            score=80.0,
            verdict=verdict,
            vetoes=[],
            reasons=["r1"],
            components={"a": 1.0},  # type: ignore[arg-type]
        ),
        token_age_hours=1.0,
        analyzed_at=at,
    )


def test_database_uses_wal_mode(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_timestamps_round_trip_as_utc_aware(session: Session) -> None:
    token = repo.upsert_token(session, candidate(), NOW)
    session.expire_all()

    assert repo.find_token(session, "solana", "tok").first_seen_at == NOW  # type: ignore[union-attr]
    assert token.deployed_at is not None and token.deployed_at.tzinfo is not None


def test_naive_datetimes_are_rejected(session: Session) -> None:
    naive = datetime(2026, 9, 25, 12, 0)

    with pytest.raises(Exception, match="naive datetime"):
        repo.beat(session, naive)


def test_upsert_token_is_idempotent_and_keeps_the_first_deployment_date(session: Session) -> None:
    first = repo.upsert_token(session, candidate(), NOW)
    second = repo.upsert_token(session, candidate(deployed=None), NOW + timedelta(minutes=5))

    assert first.id == second.id
    assert second.deployed_at == NOW - timedelta(hours=1)
    assert second.first_seen_at == NOW


def test_snapshot_round_trip_with_clusters(session: Session) -> None:
    token = repo.upsert_token(session, candidate(), NOW)

    saved = repo.save_snapshot(session, token, analysis(candidate(), with_cluster=True))
    latest = repo.latest_snapshot(session, token.id)

    assert latest is not None and latest.id == saved.id
    assert latest.verdict == "GREEN"
    assert latest.bundle_supply_pct == 12.5
    assert latest.smart_money["wallet_count"] == 2
    assert latest.volume_usd == 5e5
    (cluster,) = latest.clusters
    assert cluster.similar_size is True
    assert [w.wallet for w in cluster.wallets] == ["w1", "w2", "w3"]
    assert repo.snapshot_state(latest).verdict == "GREEN"


def test_latest_snapshot_is_the_newest(session: Session) -> None:
    token = repo.upsert_token(session, candidate(), NOW)
    repo.save_snapshot(session, token, analysis(candidate(), "GREEN", NOW))
    repo.save_snapshot(session, token, analysis(candidate(), "WATCH", NOW + timedelta(minutes=5)))

    latest = repo.latest_snapshot(session, token.id)

    assert latest is not None and latest.verdict == "WATCH"


def test_watchlist_excludes_avoid_old_and_unanalysed_tokens(session: Session) -> None:
    for address, verdict, deployed in [
        ("watch", "WATCH", NOW - timedelta(hours=2)),
        ("avoid", "AVOID", NOW - timedelta(hours=2)),
        ("old", "GREEN", NOW - timedelta(hours=25)),
    ]:
        cand = candidate(address, deployed)
        token = repo.upsert_token(session, cand, NOW)
        repo.save_snapshot(session, token, analysis(cand, verdict))
    repo.upsert_token(session, candidate("fresh"), NOW)  # never analysed

    items = repo.watchlist(session, NOW, max_age_hours=24)

    assert [token.token_address for token, _ in items] == ["watch"]


def test_credit_usage_is_upserted_per_day(session: Session) -> None:
    day = NOW.date()

    assert repo.get_credits_used(session, day) == 0
    repo.set_credits_used(session, day, 40)
    repo.set_credits_used(session, day, 55)

    assert repo.get_credits_used(session, day) == 55
    assert repo.get_credits_used(session, day + timedelta(days=1)) == 0


def test_alerts_are_recorded_per_token_and_event(session: Session) -> None:
    token = repo.upsert_token(session, candidate(), NOW)

    assert repo.last_alert_at(session, token.id, "new_green") is None
    repo.record_alert(session, token.id, "new_green", NOW, "m1")
    repo.record_alert(session, token.id, "new_green", NOW + timedelta(hours=2), "m2")
    repo.record_alert(session, token.id, "verdict_downgrade", NOW + timedelta(hours=3), "m3")

    assert repo.last_alert_at(session, token.id, "new_green") == NOW + timedelta(hours=2)
    assert repo.last_alert_at(session, token.id, "sm_selling") is None


def test_heartbeat_is_a_single_updated_row(session: Session) -> None:
    assert repo.last_beat(session) is None

    repo.beat(session, NOW)
    repo.beat(session, NOW + timedelta(seconds=5))

    assert repo.last_beat(session) == NOW + timedelta(seconds=5)


@pytest.mark.asyncio
async def test_analyze_token_requires_a_deployment_date() -> None:
    client: NansenClient = StubClient({})
    try:
        with pytest.raises(MissingDeploymentDate):
            from app.config import load_scoring_config

            await analyze_token(client, load_scoring_config(), candidate(deployed=None), NOW)
    finally:
        await client.aclose()
