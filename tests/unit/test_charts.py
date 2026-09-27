from datetime import UTC, datetime

from app.pipeline.smart_money import SmartWalletSummary
from app.storage.tables import SnapshotRow
from app.web.charts import (
    SMART_MONEY_SHARE_UNAVAILABLE_NOTE,
    TOP_HOLDERS_UNAVAILABLE_NOTE,
    buy_sell_data,
    ring_aria_label,
    ring_slices,
    score_history_data,
    tier_donut_data,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def wallet(
    address: str, tier: str, bought: float, sold: float = 0.0, tokens: float = 0.0
) -> SmartWalletSummary:
    return SmartWalletSummary(
        address=address,
        tier=tier,
        bought_usd=bought,
        sold_usd=sold,
        still_holding=tokens > 0,
        current_tokens=tokens,
        current_value_usd=None,
    )


# --- ring_slices ---


def test_ring_slices_with_full_data() -> None:
    slices, notes = ring_slices(
        bundle_supply_pct=20.0,
        bundle_wallet_count=5,
        smart_money_tokens=1_000.0,
        smart_money_wallet_count=2,
        circulating_supply=10_000.0,
    )

    assert [s["kind"] for s in slices] == ["bundle", "smart", "rest"]
    assert slices[0] == {"name": "Bundle", "pct": 20.0, "kind": "bundle", "wallets": 5}
    assert slices[1]["pct"] == 10.0  # 1,000 / 10,000
    assert slices[2]["pct"] == 70.0
    assert notes == [TOP_HOLDERS_UNAVAILABLE_NOTE]  # top holders is always unavailable today


def test_ring_slices_omits_bundle_and_smart_money_when_zero() -> None:
    slices, _ = ring_slices(
        bundle_supply_pct=0.0,
        bundle_wallet_count=0,
        smart_money_tokens=0.0,
        smart_money_wallet_count=0,
        circulating_supply=10_000.0,
    )

    assert [s["kind"] for s in slices] == ["rest"]
    assert slices[0]["pct"] == 100.0


def test_ring_slices_notes_missing_circulating_supply_only_when_smart_money_exists() -> None:
    with_smart, notes_with = ring_slices(
        bundle_supply_pct=0.0,
        bundle_wallet_count=0,
        smart_money_tokens=500.0,
        smart_money_wallet_count=2,
        circulating_supply=None,
    )
    without_smart, notes_without = ring_slices(
        bundle_supply_pct=0.0,
        bundle_wallet_count=0,
        smart_money_tokens=0.0,
        smart_money_wallet_count=0,
        circulating_supply=None,
    )

    assert SMART_MONEY_SHARE_UNAVAILABLE_NOTE in notes_with
    assert [s["kind"] for s in with_smart] == ["rest"]  # can't show a slice without a pct
    assert with_smart[0]["pct"] == 100.0

    assert SMART_MONEY_SHARE_UNAVAILABLE_NOTE not in notes_without  # nothing to report missing


def test_ring_slices_never_exceeds_the_pie() -> None:
    """Bundle + smart money over 100% (stale/inconsistent data) never produces a negative rest."""
    slices, _ = ring_slices(
        bundle_supply_pct=90.0,
        bundle_wallet_count=1,
        smart_money_tokens=2_000.0,
        smart_money_wallet_count=1,
        circulating_supply=1_000.0,  # would compute 200% on its own
    )

    assert all(s["pct"] >= 0 for s in slices)
    assert [s["kind"] for s in slices] == ["bundle", "smart"]  # rest is 0, so it's left out


def test_ring_aria_label_lists_every_slice() -> None:
    slices, _ = ring_slices(
        bundle_supply_pct=20.0,
        bundle_wallet_count=5,
        smart_money_tokens=1_000.0,
        smart_money_wallet_count=2,
        circulating_supply=10_000.0,
    )

    assert (
        ring_aria_label(slices)
        == "Supply breakdown: Bundle 20.0%, Smart money 10.0%, Rest of supply 70.0%"
    )


# --- score_history_data ---


def snapshot(created_at: datetime, score: float, verdict: str) -> SnapshotRow:
    return SnapshotRow(created_at=created_at, score=score, verdict=verdict)


def test_score_history_data_carries_time_score_and_verdict() -> None:
    data = score_history_data(
        [snapshot(NOW, 55.0, "WATCH")],
        watch_min=40.0,
        green_min=70.0,
    )

    assert data == {
        "points": [{"t": int(NOW.timestamp() * 1000), "score": 55.0, "verdict": "WATCH"}],
        "watchMin": 40.0,
        "greenMin": 70.0,
    }


def test_score_history_data_is_empty_for_no_snapshots() -> None:
    assert score_history_data([], watch_min=40.0, green_min=70.0)["points"] == []


# --- tier_donut_data ---


def test_tier_donut_data_sums_by_tier_largest_first() -> None:
    wallets = [
        wallet("a", "Fund", 1000.0),
        wallet("b", "Smart Trader", 500.0),
        wallet("c", "Fund", 2000.0),
    ]

    assert tier_donut_data(wallets) == [
        {"tier": "Fund", "usd": 3000.0},
        {"tier": "Smart Trader", "usd": 500.0},
    ]


def test_tier_donut_data_is_empty_without_wallets() -> None:
    assert tier_donut_data([]) == []


# --- buy_sell_data ---


def test_buy_sell_data_carries_a_short_label_and_both_sides() -> None:
    wallets = [wallet("0x" + "ab" * 20, "Fund", 4200.0, 5200.0)]

    data = buy_sell_data(wallets)

    assert data == [
        {
            "address": "0x" + "ab" * 20,
            "label": "0xabab…abab",
            "bought": 4200.0,
            "sold": 5200.0,
        }
    ]


def test_buy_sell_data_preserves_input_order() -> None:
    wallets = [wallet("b", "Fund", 100.0), wallet("a", "Fund", 900.0)]

    assert [row["address"] for row in buy_sell_data(wallets)] == ["b", "a"]
