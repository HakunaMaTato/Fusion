from datetime import datetime, timedelta

from pydantic import BaseModel

EVENT_NEW_GREEN = "new_green"
EVENT_DOWNGRADE = "verdict_downgrade"
EVENT_BUNDLE_DISTRIBUTING = "bundle_distributing"
EVENT_SM_SELLING = "sm_selling"

VERDICT_RANK = {"AVOID": 0, "WATCH": 1, "GREEN": 2}


class SnapshotState(BaseModel):
    """The parts of a snapshot that alerts compare."""

    verdict: str
    bundle_status: str
    sm_net_selling: bool


class AlertEvent(BaseModel):
    event_type: str
    detail: str


def detect_events(prev: SnapshotState | None, new: SnapshotState) -> list[AlertEvent]:
    events: list[AlertEvent] = []
    if new.verdict == "GREEN" and (prev is None or prev.verdict != "GREEN"):
        events.append(AlertEvent(event_type=EVENT_NEW_GREEN, detail="new GREEN candidate"))
    if prev is not None:
        if VERDICT_RANK[new.verdict] < VERDICT_RANK[prev.verdict]:
            events.append(
                AlertEvent(
                    event_type=EVENT_DOWNGRADE,
                    detail=f"verdict downgraded {prev.verdict} to {new.verdict}",
                )
            )
        if new.bundle_status == "distributing" and prev.bundle_status != "distributing":
            events.append(
                AlertEvent(event_type=EVENT_BUNDLE_DISTRIBUTING, detail="bundle is distributing")
            )
        if new.sm_net_selling and not prev.sm_net_selling:
            events.append(
                AlertEvent(
                    event_type=EVENT_SM_SELLING, detail="smart money net flow turned to selling"
                )
            )
    return events


def outside_cooldown(last_sent_at: datetime | None, now: datetime, cooldown_minutes: float) -> bool:
    """True when an event of this type may be sent again for the token."""
    return last_sent_at is None or now - last_sent_at >= timedelta(minutes=cooldown_minutes)


def is_due_for_reeval(last_snapshot_at: datetime, now: datetime, reeval_seconds: float) -> bool:
    return now - last_snapshot_at >= timedelta(seconds=reeval_seconds)
