from datetime import UTC, datetime, timedelta

import pytest

from app.pipeline.monitor import (
    EVENT_BUNDLE_DISTRIBUTING,
    EVENT_DOWNGRADE,
    EVENT_NEW_GREEN,
    EVENT_SM_SELLING,
    SnapshotState,
    detect_events,
    is_due_for_reeval,
    outside_cooldown,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def state(verdict: str = "WATCH", bundle: str = "holding", selling: bool = False) -> SnapshotState:
    return SnapshotState(verdict=verdict, bundle_status=bundle, sm_net_selling=selling)


def types(prev: SnapshotState | None, new: SnapshotState) -> list[str]:
    return [event.event_type for event in detect_events(prev, new)]


@pytest.mark.parametrize(
    ("prev", "new", "expected"),
    [
        (None, state("GREEN"), [EVENT_NEW_GREEN]),
        (state("WATCH"), state("GREEN"), [EVENT_NEW_GREEN]),
        (state("AVOID"), state("GREEN"), [EVENT_NEW_GREEN]),
        (state("GREEN"), state("GREEN"), []),
        (None, state("WATCH"), []),
        (None, state("AVOID"), []),
        (state("GREEN"), state("WATCH"), [EVENT_DOWNGRADE]),
        (state("WATCH"), state("AVOID"), [EVENT_DOWNGRADE]),
        (state("GREEN"), state("AVOID"), [EVENT_DOWNGRADE]),
        (state("AVOID"), state("WATCH"), []),
        (state("WATCH"), state("WATCH"), []),
    ],
)
def test_verdict_transitions(
    prev: SnapshotState | None, new: SnapshotState, expected: list[str]
) -> None:
    assert types(prev, new) == expected


def test_bundle_turning_to_distributing_alerts_once() -> None:
    assert types(state(bundle="holding"), state(bundle="distributing")) == [
        EVENT_BUNDLE_DISTRIBUTING
    ]
    assert types(state(bundle="none"), state(bundle="distributing")) == [EVENT_BUNDLE_DISTRIBUTING]
    assert types(state(bundle="distributing"), state(bundle="distributing")) == []
    assert types(state(bundle="distributing"), state(bundle="exited")) == []


def test_a_first_snapshot_with_a_distributing_bundle_does_not_alert() -> None:
    assert types(None, state(bundle="distributing")) == []


def test_smart_money_flipping_to_selling_alerts_once() -> None:
    assert types(state(selling=False), state(selling=True)) == [EVENT_SM_SELLING]
    assert types(state(selling=True), state(selling=True)) == []
    assert types(state(selling=True), state(selling=False)) == []
    assert types(None, state(selling=True)) == []


def test_several_events_can_fire_together() -> None:
    result = types(state("GREEN", "holding", False), state("AVOID", "distributing", True))

    assert result == [EVENT_DOWNGRADE, EVENT_BUNDLE_DISTRIBUTING, EVENT_SM_SELLING]


def test_cooldown_boundaries() -> None:
    assert outside_cooldown(None, NOW, 60) is True
    assert outside_cooldown(NOW - timedelta(minutes=59, seconds=59), NOW, 60) is False
    assert outside_cooldown(NOW - timedelta(minutes=60), NOW, 60) is True
    assert outside_cooldown(NOW - timedelta(days=2), NOW, 60) is True


def test_reeval_due_boundaries() -> None:
    assert is_due_for_reeval(NOW - timedelta(seconds=899), NOW, 900) is False
    assert is_due_for_reeval(NOW - timedelta(seconds=900), NOW, 900) is True
