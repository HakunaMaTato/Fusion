from datetime import UTC, datetime


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware UTC datetime (naive input is UTC)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_optional_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None
