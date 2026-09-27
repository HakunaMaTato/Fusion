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


# usd() and pct() are the UI_REDESIGN.md §4 formatting helpers. money()/percent() above are kept
# as they are (existing templates and tests use them); later UI phases migrate call sites to these
# names and the older ones can then be dropped.
usd = money


def pct(value: float | None) -> str:
    """One decimal; "<0.1%" for a small non-zero value; "0%" only for a true zero (issue 13)."""
    if value is None:
        return "n/a"
    if value == 0:
        return "0%"
    if 0 < value < 0.1:
        return "<0.1%"
    if -0.1 < value < 0:
        return "-<0.1%"
    return f"{value:.1f}%"


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


def width_class(pct: float) -> str:
    """A CSS class for a proportional-width bar (summary strip segments), in steps of 5%.

    A numeric width can't be an inline `style` (the CSP and a test forbid it), so it's rounded to
    one of 21 fixed classes (components.css: .w-0 .. .w-100) instead — the same "closed set of
    classes, never a raw value" approach as avatar_hue()/chain_class().
    """
    step = max(0, min(100, round(pct / 5) * 5))
    return f"w-{step}"


def ratio(value: float | None) -> str:
    """UI_REDESIGN.md §5.4 "Vol / liq" column: "11.6x"; "n/a" if it can't be computed."""
    return "n/a" if value is None else f"{value:.1f}x"


def bundle_severity(pct: float) -> str:
    """Meter colour for the list table's Bundle column (§5.4): under 5% muted, 5-30% watch, over
    30% avoid — the same 30% line as the veto in config/scoring.yaml's `veto_bundle_supply_pct`."""
    if pct >= 30:
        return "avoid"
    if pct >= 5:
        return "watch"
    return "accent"


def verdict_meter_variant(verdict: str) -> str:
    """Meter colour for the list table's Score column: the verdict's own colour, or neutral."""
    return {"GREEN": "green", "WATCH": "watch", "AVOID": "avoid"}.get(verdict, "accent")


def short_address(address: str) -> str:
    """UI_REDESIGN.md §4 address chip: "0x55db…7777" (a real ellipsis, not three periods)."""
    return address if len(address) <= 14 else f"{address[:6]}…{address[-4:]}"


# The chains the app knows a colour and an explorer for (app/web/links.py, tokens.css). Anything
# else is real data (a chain Nansen added that we haven't wired up yet) — never raise on it, and
# never let it become an arbitrary CSS class name; fall back to a neutral, unstyled chip instead.
KNOWN_CHAINS = frozenset({"solana", "base", "bnb", "bsc", "ethereum", "robinhood"})


def chain_class(chain: str) -> str:
    """CSS class suffix for the chain chip's coloured dot: "chain-<name>" or "chain-default"."""
    return f"chain-{chain}" if chain in KNOWN_CHAINS else "chain-default"


CHAIN_LABELS = {
    "solana": "Solana",
    "base": "Base",
    "bnb": "BNB",
    "bsc": "BNB",  # the name Nansen returns for BNB Chain tokens
    "ethereum": "Ethereum",
    "robinhood": "Robinhood",
}


def chain_label(chain: str) -> str:
    """Display name for the chain chip (§5.4/§4): sentence case, except acronyms like BNB."""
    return CHAIN_LABELS.get(chain, chain.capitalize())


AVATAR_HUES = 8


def avatar_hue(address: str) -> int:
    """Which of the 8 token-avatar hues (tokens.css --avatar-0..7) an address gets.

    Deterministic and stable across processes and Python versions (unlike the builtin hash(),
    which is randomised per process for str). Not cryptographic; this only ever picks a colour.
    """
    return sum(address.encode("utf-8")) % AVATAR_HUES


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
