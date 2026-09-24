from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.nansen.exceptions import BudgetExceeded, NansenAPIError
from app.pipeline.bundle import (
    REASON_COMMON_FUNDER,
    REASON_DEPLOYER_LINK,
    REASON_SAME_SECOND,
    EarlyBuy,
    Funder,
    analyze_bundle,
    early_buys,
    find_clusters,
    funder_of,
    is_ignored_funder,
    measure_bundle,
    merge_clusters,
    same_second_clusters,
    wallets_to_look_up,
)
from tests.factories import StubClient, bundle_config, holder, related, tgm_trade

DEPLOYED = datetime(2026, 9, 24, 2, 0, 0, tzinfo=UTC)
CFG = bundle_config()
PAGINATION = {"page": 1, "per_page": 10, "is_last_page": True}


def ts(seconds: float) -> str:
    return (DEPLOYED + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def buys_of(*specs: tuple[str, float, float, float]) -> list[EarlyBuy]:
    """Each spec is (wallet, seconds after deployment, tokens, usd)."""
    trades = [tgm_trade(w, ts(sec), tokens=tok, usd=usd) for w, sec, tok, usd in specs]
    return early_buys(trades, DEPLOYED, CFG)


def funded_by(
    address: str, wallets: list[str], label: str | None = None
) -> dict[str, Funder | None]:
    return {wallet: Funder(address=address, label=label) for wallet in wallets}


# --- the five scenarios from SPEC.md Phase 3 ---


def test_organic_launch_has_no_cluster() -> None:
    wallets = [f"w{i}" for i in range(10)]
    buys = buys_of(*((w, i + 1, 1000 + i * 137, 90 + i * 31) for i, w in enumerate(wallets)))
    funders: dict[str, Funder | None] = {w: Funder(address=f"funder-{w}") for w in wallets}

    clusters = find_clusters(buys, funders, None, CFG)
    analysis = measure_bundle(clusters, buys, [], CFG)

    assert clusters == []
    assert analysis.status == "none"
    assert analysis.supply_pct == 0.0
    assert analysis.bundled_wallets == frozenset()


def test_obvious_bundle_one_funder_twelve_wallets_same_second() -> None:
    wallets = [f"b{i}" for i in range(12)]
    buys = buys_of(*((w, 5.0, 1000.0, 100.0) for w in wallets))
    funders = funded_by("F", wallets)
    holders = [holder(w, 1000.0, 3.0) for w in wallets]

    clusters = find_clusters(buys, funders, None, CFG)
    analysis = measure_bundle(clusters, buys, holders, CFG)

    assert len(clusters) == 1
    assert clusters[0].wallets == frozenset(wallets)
    assert clusters[0].reasons == frozenset({REASON_SAME_SECOND, REASON_COMMON_FUNDER})
    assert clusters[0].funders == frozenset({"F"})
    assert clusters[0].similar_size is True
    assert analysis.supply_pct == pytest.approx(36.0)
    assert analysis.sold_pct == 0.0
    assert analysis.status == "holding"


def test_cex_funded_wallets_are_not_flagged() -> None:
    wallets = [f"u{i}" for i in range(6)]
    buys = buys_of(*((w, i + 1, 1000.0, 100.0 + i * 40) for i, w in enumerate(wallets)))
    funders = funded_by("cex-hot-wallet", wallets, label="Binance Exchange 14")

    clusters = find_clusters(buys, funders, None, CFG)

    assert clusters == []


def test_partial_bundle_that_is_distributing() -> None:
    wallets = [f"p{i}" for i in range(5)]
    buys = buys_of(*((w, 3.0, 1000.0, 100.0) for w in wallets))
    holders = [holder(w, 1000.0, 2.0) for w in wallets[:3]]

    clusters = find_clusters(buys, {}, None, CFG)
    analysis = measure_bundle(clusters, buys, holders, CFG)

    assert len(clusters) == 1
    assert clusters[0].reasons == frozenset({REASON_SAME_SECOND})
    assert analysis.supply_pct == pytest.approx(6.0)
    assert analysis.sold_pct == pytest.approx(40.0)
    assert analysis.status == "distributing"


def test_deployer_linked_bundle() -> None:
    wallets = [f"d{i}" for i in range(4)]
    buys = buys_of(*((w, i + 1, 1000.0, 100.0 + i * 50) for i, w in enumerate(wallets)))
    funders = funded_by("deployer-1", wallets)

    clusters = find_clusters(buys, funders, "deployer-1", CFG)

    assert len(clusters) == 1
    assert clusters[0].wallets == frozenset(wallets)
    assert clusters[0].reasons == frozenset({REASON_COMMON_FUNDER, REASON_DEPLOYER_LINK})


# --- edge and failure cases ---


def test_no_trades_means_no_bundle() -> None:
    clusters = find_clusters([], {}, None, CFG)

    assert clusters == []
    assert measure_bundle(clusters, [], [], CFG).status == "none"


def test_two_same_second_wallets_are_below_the_threshold() -> None:
    buys = buys_of(("a", 2.0, 1000.0, 100.0), ("b", 2.0, 1000.0, 100.0))

    assert same_second_clusters(buys, CFG) == []


def test_same_second_uses_whole_seconds() -> None:
    buys = buys_of(("a", 2.1, 1.0, 1.0), ("b", 2.9, 1.0, 1.0), ("c", 3.1, 1.0, 1.0))

    clusters = same_second_clusters(buys, CFG)

    assert clusters == []


def test_overlapping_clusters_are_unioned() -> None:
    buys = buys_of(
        ("a", 5.0, 1.0, 1.0),
        ("b", 5.0, 1.0, 1.0),
        ("c", 5.0, 1.0, 1.0),
        ("d", 20.0, 1.0, 1.0),
        ("e", 40.0, 1.0, 1.0),
    )
    funders = funded_by("X", ["c", "d", "e"])

    clusters = find_clusters(buys, funders, None, CFG)

    assert len(clusters) == 1
    assert clusters[0].wallets == frozenset("abcde")
    assert clusters[0].reasons == frozenset({REASON_SAME_SECOND, REASON_COMMON_FUNDER})


def test_disjoint_clusters_stay_separate() -> None:
    a = same_second_clusters(buys_of(*((w, 1.0, 1.0, 1.0) for w in "abc")), CFG)
    b = same_second_clusters(buys_of(*((w, 9.0, 1.0, 1.0) for w in "xyz")), CFG)

    assert len(merge_clusters([*a, *b])) == 2


def test_wallet_missing_from_holders_counts_as_fully_sold() -> None:
    buys = buys_of(*((w, 1.0, 500.0, 50.0) for w in "abc"))
    clusters = find_clusters(buys, {}, None, CFG)

    analysis = measure_bundle(clusters, buys, [], CFG)

    assert analysis.sold_pct == 100.0
    assert analysis.status == "exited"
    assert analysis.supply_pct == 0.0


def test_holder_with_missing_fields_counts_as_zero() -> None:
    buys = buys_of(*((w, 1.0, 500.0, 50.0) for w in "abc"))
    clusters = find_clusters(buys, {}, None, CFG)

    analysis = measure_bundle(clusters, buys, [holder("a", 0.0, 0.0)], CFG)

    assert analysis.supply_pct == 0.0
    assert analysis.status == "exited"


def test_holding_more_than_bought_does_not_go_negative() -> None:
    buys = buys_of(*((w, 1.0, 500.0, 50.0) for w in "abc"))
    clusters = find_clusters(buys, {}, None, CFG)

    analysis = measure_bundle(clusters, buys, [holder(w, 900.0, 1.0) for w in "abc"], CFG)

    assert analysis.sold_pct == 0.0
    assert analysis.status == "holding"


def test_status_boundaries() -> None:
    buys = buys_of(*((w, 1.0, 1000.0, 50.0) for w in "abcd"))  # peak 4000 tokens
    clusters = find_clusters(buys, {}, None, CFG)

    at_25 = measure_bundle(clusters, buys, [holder("a", 3000.0, 1.0)], CFG)
    at_90 = measure_bundle(clusters, buys, [holder("a", 400.0, 1.0)], CFG)
    just_below = measure_bundle(clusters, buys, [holder("a", 3100.0, 1.0)], CFG)

    assert at_25.status == "distributing"
    assert at_90.status == "exited"
    assert just_below.status == "holding"


def test_similar_size_is_false_when_sizes_are_spread() -> None:
    buys = buys_of(("a", 1.0, 1.0, 100.0), ("b", 1.0, 1.0, 500.0), ("c", 1.0, 1.0, 1000.0))

    clusters = find_clusters(buys, {}, None, CFG)

    assert clusters[0].similar_size is False


def test_similar_size_alone_never_creates_a_cluster() -> None:
    buys = buys_of(*((w, i + 1, 1.0, 100.0) for i, w in enumerate("abcdef")))

    assert find_clusters(buys, {}, None, CFG) == []


def test_deployer_as_a_clustered_wallet_is_flagged() -> None:
    buys = buys_of(*((w, 1.0, 1.0, 1.0) for w in ("dep", "b", "c")))

    clusters = find_clusters(buys, {}, "dep", CFG)

    assert REASON_DEPLOYER_LINK in clusters[0].reasons


def test_unknown_deployer_adds_no_flag() -> None:
    buys = buys_of(*((w, 1.0, 1.0, 1.0) for w in "abc"))

    clusters = find_clusters(buys, {}, None, CFG)

    assert REASON_DEPLOYER_LINK not in clusters[0].reasons


def test_early_buys_filters_window_side_and_caps() -> None:
    trades = [
        tgm_trade("late", ts(601)),
        tgm_trade("early", ts(-1)),
        tgm_trade("seller", ts(5), action="SELL"),
        tgm_trade("c", ts(30)),
        tgm_trade("a", ts(1)),
        tgm_trade("b", ts(2)),
        tgm_trade("edge", ts(600)),
    ]

    everything = early_buys(trades, DEPLOYED, CFG)
    capped = early_buys(trades, DEPLOYED, bundle_config(max_early_buys=2))

    assert [b.wallet for b in everything] == ["a", "b", "c", "edge"]
    assert [b.wallet for b in capped] == ["a", "b"]


def test_funder_lookup_cap_takes_earliest_unique_buyers() -> None:
    buys = buys_of(
        ("a", 1.0, 1.0, 1.0), ("a", 2.0, 1.0, 1.0), ("b", 3.0, 1.0, 1.0), ("c", 4.0, 1.0, 1.0)
    )

    assert wallets_to_look_up(buys, bundle_config(max_funder_lookups=2)) == ["a", "b"]


def test_funder_of_uses_configured_relation_and_lowest_order() -> None:
    rows = [
        related("signer", "Signer", order=1),
        related("late", "first funder", order=5),
        related("early", "First Funder", order=2, label="Whale"),
    ]

    funder = funder_of(rows, CFG)

    assert funder == Funder(address="early", label="Whale")


def test_funder_of_returns_none_without_a_funder_relation() -> None:
    assert funder_of([related("s", "Signer"), related("d", "Deployed via")], CFG) is None
    assert funder_of([], CFG) is None


def test_ignored_funder_label_matching() -> None:
    assert is_ignored_funder(Funder(address="x", label="Binance Exchange 14"), CFG)
    assert is_ignored_funder(Funder(address="x", label="jupiter dex router"), CFG)
    assert not is_ignored_funder(Funder(address="x", label="Some Whale"), CFG)
    assert not is_ignored_funder(Funder(address="x", label=None), CFG)


# --- async wiring ---


def _trade_rows(wallets: list[str], seconds: float) -> list[dict[str, Any]]:
    return [tgm_trade(w, ts(seconds), tokens=1000.0, usd=100.0).model_dump() for w in wallets]


def _related_row(address: str) -> dict[str, Any]:
    return related(address, "First Funder").model_dump()


def _holder_rows(*holders: tuple[str, float, float]) -> list[dict[str, Any]]:
    return [holder(*spec).model_dump() for spec in holders]


@pytest.mark.asyncio
async def test_analyze_bundle_end_to_end_with_a_failed_funder_lookup() -> None:
    wallets = ["b1", "b2", "b3", "b4"]

    def related_wallets(body: dict[str, Any]) -> dict[str, Any]:
        if body["wallet_address"] == "b4":
            raise NansenAPIError(status=500, message="boom")
        return {"data": [_related_row("F")], "pagination": PAGINATION}

    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [{"data": _trade_rows(wallets, 1), "pagination": PAGINATION}],
            "/api/v1/profiler/address/related-wallets": related_wallets,
            "/api/v1/tgm/holders": [
                {
                    "data": _holder_rows(("b1", 1000.0, 2.0), ("b2", 1000.0, 2.0)),
                    "pagination": PAGINATION,
                }
            ],
        }
    )
    try:
        analysis = await analyze_bundle(
            client,
            CFG,
            chain="solana",
            token_address="tok",
            deployed_at=DEPLOYED,
            deployer=None,
            related_wallets_ttl_seconds=86400,
        )
    finally:
        await client.aclose()

    trades_body = client.requests[0][1]
    assert trades_body["date"] == {"from": "2026-09-24T02:00:00Z", "to": "2026-09-24T02:10:00Z"}
    assert trades_body["filters"] == {"action": "BUY"}
    assert trades_body["order_by"] == [{"field": "block_timestamp", "direction": "ASC"}]
    assert analysis.bundled_wallets == frozenset(wallets)
    assert analysis.supply_pct == pytest.approx(4.0)
    assert analysis.sold_pct == pytest.approx(50.0)
    assert analysis.status == "distributing"
    holders_body = next(b for e, b in client.requests if e == "/api/v1/tgm/holders")
    assert holders_body["filters"]["address"] == wallets
    assert holders_body["filters"]["value_usd"] == {"min": 0.0}


@pytest.mark.asyncio
async def test_analyze_bundle_skips_holders_call_when_no_cluster() -> None:
    wallets = ["a", "b"]
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [{"data": _trade_rows(wallets, 1), "pagination": PAGINATION}],
            "/api/v1/profiler/address/related-wallets": lambda body: {
                "data": [],
                "pagination": PAGINATION,
            },
        }
    )
    try:
        analysis = await analyze_bundle(
            client,
            CFG,
            chain="solana",
            token_address="tok",
            deployed_at=DEPLOYED,
            deployer=None,
            related_wallets_ttl_seconds=86400,
        )
    finally:
        await client.aclose()

    assert analysis.status == "none"
    assert all(endpoint != "/api/v1/tgm/holders" for endpoint, _ in client.requests)


@pytest.mark.asyncio
async def test_analyze_bundle_respects_the_funder_lookup_cap() -> None:
    wallets = [f"w{i}" for i in range(10)]
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [
                {
                    "data": [tgm_trade(w, ts(i + 1)).model_dump() for i, w in enumerate(wallets)],
                    "pagination": PAGINATION,
                }
            ],
            "/api/v1/profiler/address/related-wallets": lambda body: {
                "data": [],
                "pagination": PAGINATION,
            },
        }
    )
    try:
        await analyze_bundle(
            client,
            bundle_config(max_funder_lookups=4),
            chain="solana",
            token_address="tok",
            deployed_at=DEPLOYED,
            deployer=None,
            related_wallets_ttl_seconds=86400,
        )
    finally:
        await client.aclose()

    lookups = [b for e, b in client.requests if e.endswith("related-wallets")]
    assert [b["wallet_address"] for b in lookups] == wallets[:4]


@pytest.mark.asyncio
async def test_budget_exceeded_during_lookups_propagates_after_all_lookups_finish() -> None:
    wallets = ["a", "b", "c"]
    seen: list[str] = []

    def related_wallets(body: dict[str, Any]) -> dict[str, Any]:
        seen.append(body["wallet_address"])
        if body["wallet_address"] == "a":
            raise BudgetExceeded(used=3000, budget=3000)
        return {"data": [], "pagination": PAGINATION}

    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [
                {
                    "data": [tgm_trade(w, ts(i + 1)).model_dump() for i, w in enumerate(wallets)],
                    "pagination": PAGINATION,
                }
            ],
            "/api/v1/profiler/address/related-wallets": related_wallets,
        }
    )
    try:
        with pytest.raises(BudgetExceeded):
            await analyze_bundle(
                client,
                CFG,
                chain="solana",
                token_address="tok",
                deployed_at=DEPLOYED,
                deployer=None,
                related_wallets_ttl_seconds=86400,
            )
    finally:
        await client.aclose()

    assert sorted(seen) == wallets


def test_supply_share_reads_the_api_fraction_as_percent() -> None:
    from app.nansen.models import TGMHolder

    buys = buys_of(*((w, 1.0, 1000.0, 100.0) for w in "abc"))
    clusters = find_clusters(buys, {}, None, CFG)
    holders = [
        TGMHolder(address=w, token_amount=1000.0, ownership_percentage=0.0343) for w in "abc"
    ]

    analysis = measure_bundle(clusters, buys, holders, CFG)

    assert analysis.supply_pct == pytest.approx(10.29)
