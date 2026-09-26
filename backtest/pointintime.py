"""Guards against look-ahead: nothing after the decision time may reach the analysis."""

from collections.abc import Callable
from datetime import datetime

from app.pipeline.timeutil import parse_timestamp


class LookAheadError(Exception):
    pass


def assert_range_ends_by(decision_at: datetime, range_to: datetime) -> None:
    """A request for point-in-time data must not ask for anything after the decision."""
    if range_to > decision_at:
        raise LookAheadError(f"request range ends {range_to} after the decision at {decision_at}")


def keep_until[T](
    decision_at: datetime, items: list[T], timestamp_of: Callable[[T], str]
) -> tuple[list[T], int]:
    """Items stamped at or before the decision, and how many later ones were dropped."""
    kept = [item for item in items if parse_timestamp(timestamp_of(item)) <= decision_at]
    return kept, len(items) - len(kept)
