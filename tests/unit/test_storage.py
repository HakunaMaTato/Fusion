from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.nansen.client import NansenClient
from app.pipeline.analyze import MissingDeploymentDate, TokenAnalysis, analyze_token
from app.pipeline.bundle import BundleAnalysis, Cluster
from app.pipeline.discovery import Candidate
from app.pipeline.scoring import Reason, ScoreResult
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
            reasons=[Reason(text="r1", polarity="positive")],
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


def _add(session: Session, address: str, verdict: str, score: float, chain: str = "solana") -> None:
    cand = candidate(address)
    cand = cand.model_copy(update={"chain": chain})
    token = repo.upsert_token(session, cand, NOW)
    result = analysis(cand, verdict)
    result.score.score = score
    repo.save_snapshot(session, token, result)


def test_list_tokens_orders_green_first_then_by_score(session: Session) -> None:
    _add(session, "a", "AVOID", 30)
    _add(session, "b", "GREEN", 75)
    _add(session, "c", "WATCH", 45)
    _add(session, "d", "GREEN", 90)
    _add(session, "e", "WATCH", 60)

    rows = repo.list_tokens(session)

    assert [t.token_address for t, _ in rows] == ["d", "b", "e", "c", "a"]


def test_list_tokens_uses_only_the_latest_snapshot_and_filters(session: Session) -> None:
    cand = candidate("x")
    token = repo.upsert_token(session, cand, NOW)
    repo.save_snapshot(session, token, analysis(cand, "GREEN", NOW))
    repo.save_snapshot(session, token, analysis(cand, "AVOID", NOW + timedelta(minutes=5)))
    _add(session, "y", "WATCH", 50, chain="base")

    assert [(t.token_address, s.verdict) for t, s in repo.list_tokens(session)] == [
        ("y", "WATCH"),
        ("x", "AVOID"),
    ]
    assert [t.token_address for t, _ in repo.list_tokens(session, verdict="AVOID")] == ["x"]
    assert repo.list_tokens(session, verdict="GREEN") == []
    assert [t.token_address for t, _ in repo.list_tokens(session, chain="base")] == ["y"]
    assert repo.list_chains(session) == ["base", "solana"]


def test_list_tokens_respects_the_limit(session: Session) -> None:
    for i in range(5):
        _add(session, f"t{i}", "WATCH", 50 + i)

    assert [t.token_address for t, _ in repo.list_tokens(session, limit=2)] == ["t4", "t3"]


def test_tokens_without_a_snapshot_are_not_listed(session: Session) -> None:
    repo.upsert_token(session, candidate("bare"), NOW)

    assert repo.list_tokens(session) == []


def test_snapshot_history_is_the_newest_n_oldest_first(session: Session) -> None:
    cand = candidate()
    token = repo.upsert_token(session, cand, NOW)
    for minutes in range(5):
        repo.save_snapshot(
            session, token, analysis(cand, "WATCH", NOW + timedelta(minutes=minutes))
        )

    history = repo.snapshot_history(session, token.id, limit=3)

    assert [s.created_at for s in history] == [NOW + timedelta(minutes=m) for m in (2, 3, 4)]


def test_last_discovery_and_analysis_times(session: Session) -> None:
    assert repo.last_discovery_at(session) is None
    assert repo.last_analysis_at(session) is None

    repo.record_discovery(session, NOW)  # creates the row before any heartbeat
    repo.beat(session, NOW + timedelta(seconds=5))
    cand = candidate()
    repo.save_snapshot(session, repo.upsert_token(session, cand, NOW), analysis(cand, "GREEN", NOW))

    assert repo.last_discovery_at(session) == NOW
    assert repo.last_beat(session) == NOW + timedelta(seconds=5)
    assert repo.last_analysis_at(session) == NOW


def test_list_tokens_hides_stale_snapshots(session: Session) -> None:
    old, fresh = candidate("old"), candidate("fresh")
    repo.save_snapshot(
        session,
        repo.upsert_token(session, old, NOW),
        analysis(old, "GREEN", NOW - timedelta(hours=30)),
    )
    repo.save_snapshot(
        session, repo.upsert_token(session, fresh, NOW), analysis(fresh, "GREEN", NOW)
    )

    rows = repo.list_tokens(session, updated_since=NOW - timedelta(hours=24))

    assert [t.token_address for t, _ in rows] == ["fresh"]
    assert len(repo.list_tokens(session)) == 2


# --- issue 16: real deployment age, not just snapshot freshness ---


def test_list_tokens_hides_tokens_deployed_before_the_cutoff(session: Session) -> None:
    old = candidate("old", deployed=NOW - timedelta(hours=30))
    fresh = candidate("fresh", deployed=NOW - timedelta(hours=1))
    repo.save_snapshot(session, repo.upsert_token(session, old, NOW), analysis(old, "GREEN", NOW))
    repo.save_snapshot(
        session, repo.upsert_token(session, fresh, NOW), analysis(fresh, "GREEN", NOW)
    )

    rows = repo.list_tokens(session, min_deployed_at=NOW - timedelta(hours=24))

    assert [t.token_address for t, _ in rows] == ["fresh"]
    assert len(repo.list_tokens(session)) == 2  # no cutoff: both are listed


def test_a_stale_but_recently_reevaluated_token_is_still_hidden_by_age(session: Session) -> None:
    """The exact issue 16 bug: a snapshot from minutes ago must not hide the token's real age."""
    old = candidate("old", deployed=NOW - timedelta(hours=30))
    repo.save_snapshot(
        session,
        repo.upsert_token(session, old, NOW),
        analysis(old, "GREEN", NOW - timedelta(minutes=1)),
    )

    rows = repo.list_tokens(session, min_deployed_at=NOW - timedelta(hours=24))

    assert rows == []


def test_a_token_with_no_deployment_date_is_never_hidden_by_the_age_cutoff(
    session: Session,
) -> None:
    unknown = candidate("unknown", deployed=None)
    repo.save_snapshot(
        session, repo.upsert_token(session, unknown, NOW), analysis(unknown, "GREEN", NOW)
    )

    rows = repo.list_tokens(session, min_deployed_at=NOW - timedelta(hours=24))

    assert [t.token_address for t, _ in rows] == ["unknown"]


# --- search ---


def test_list_tokens_search_matches_symbol_or_address_case_insensitively(
    session: Session,
) -> None:
    cand = Candidate(
        chain="solana",
        token_address="AbCdEf123",
        token_symbol="WIRED",
        token_age_hours=1.0,
        market_cap_usd=1e6,
        token_deployment_date=NOW - timedelta(hours=1),
    )
    repo.save_snapshot(session, repo.upsert_token(session, cand, NOW), analysis(cand, "GREEN", NOW))
    _add(session, "other", "WATCH", 40)

    assert [t.symbol for t, _ in repo.list_tokens(session, search="wired")] == ["WIRED"]
    assert [t.symbol for t, _ in repo.list_tokens(session, search="abcdef")] == ["WIRED"]
    assert repo.list_tokens(session, search="nope") == []


# --- sort ---


def test_list_tokens_sort_by_age_and_direction(session: Session) -> None:
    younger = candidate("younger", deployed=NOW - timedelta(hours=1))
    older = candidate("older", deployed=NOW - timedelta(hours=10))
    repo.save_snapshot(
        session, repo.upsert_token(session, younger, NOW), analysis(younger, "GREEN", NOW)
    )
    repo.save_snapshot(
        session, repo.upsert_token(session, older, NOW), analysis(older, "GREEN", NOW)
    )

    oldest_first = repo.list_tokens(session, sort="age", sort_dir="desc", now=NOW)
    youngest_first = repo.list_tokens(session, sort="age", sort_dir="asc", now=NOW)

    assert [t.token_address for t, _ in oldest_first] == ["older", "younger"]
    assert [t.token_address for t, _ in youngest_first] == ["younger", "older"]


def test_list_tokens_sort_by_smart_wallets_reads_the_json_field(session: Session) -> None:
    many = candidate("many")
    few = candidate("few")
    many_result = analysis(many, "GREEN", NOW)
    many_result.smart_money.wallet_count = 9
    few_result = analysis(few, "GREEN", NOW)
    few_result.smart_money.wallet_count = 1
    repo.save_snapshot(session, repo.upsert_token(session, many, NOW), many_result)
    repo.save_snapshot(session, repo.upsert_token(session, few, NOW), few_result)

    rows = repo.list_tokens(session, sort="smart_wallets", sort_dir="desc")

    assert [t.token_address for t, _ in rows] == ["many", "few"]


def test_list_tokens_default_sort_is_unaffected_by_the_sort_parameter_being_absent(
    session: Session,
) -> None:
    _add(session, "a", "AVOID", 99)
    _add(session, "b", "GREEN", 1)

    assert [t.token_address for t, _ in repo.list_tokens(session)] == ["b", "a"]


# --- verdict counts ---


def test_verdict_counts_ignores_the_verdict_filter_itself(session: Session) -> None:
    _add(session, "a", "GREEN", 90)
    _add(session, "b", "GREEN", 80)
    _add(session, "c", "WATCH", 50)
    _add(session, "d", "AVOID", 10, chain="base")

    assert repo.verdict_counts(session) == {"GREEN": 2, "WATCH": 1, "AVOID": 1}
    assert repo.verdict_counts(session, chain="base") == {"GREEN": 0, "WATCH": 0, "AVOID": 1}


def test_verdict_counts_respects_the_age_cutoff(session: Session) -> None:
    old = candidate("old", deployed=NOW - timedelta(hours=30))
    repo.save_snapshot(session, repo.upsert_token(session, old, NOW), analysis(old, "GREEN", NOW))

    assert repo.verdict_counts(session, min_deployed_at=NOW - timedelta(hours=24)) == {
        "GREEN": 0,
        "WATCH": 0,
        "AVOID": 0,
    }
