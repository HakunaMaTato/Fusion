from datetime import UTC, datetime


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{sign}${magnitude / 1e9:.2f}B"
    if magnitude >= 1e6:
        return f"{sign}${magnitude / 1e6:.2f}M"
    if magnitude >= 1e3:
        return f"{sign}${magnitude / 1e3:.1f}K"
    return f"{sign}${magnitude:,.0f}"


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def age(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    minutes = int(max(hours, 0) * 60)
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes // (24 * 60)}d {(minutes % (24 * 60)) // 60}h"


def age_since(moment: datetime | None, now: datetime | None = None) -> str:
    """Age of a timestamp, for example '2m ago'."""
    if moment is None:
        return "never"
    seconds = int(((now or datetime.now(UTC)) - moment).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s ago"
    return f"{age(seconds / 3600)} ago"


def short_address(address: str) -> str:
    return address if len(address) <= 14 else f"{address[:6]}...{address[-4:]}"


CLUSTER_REASONS = {
    "same_second": "bought in the same second",
    "common_funder": "shared funder",
    "similar_size": "similar buy sizes",
    "deployer_link": "linked to the deployer",
}


def cluster_reason(reason: str) -> str:
    return CLUSTER_REASONS.get(reason, reason.replace("_", " "))


COMPONENT_LABELS = {
    "sm_participation": "Smart money participation",
    "sm_holding": "Smart money holding",
    "bundle_supply": "Low bundle supply",
    "bundle_status": "Bundle status",
    "volume_liquidity": "Volume / liquidity",
    "holder_growth": "Holder growth",
}


def component_label(name: str) -> str:
    return COMPONENT_LABELS.get(name, name.replace("_", " ").capitalize())
