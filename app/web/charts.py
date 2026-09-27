"""Chart view models for the token detail page (UI_REDESIGN.md §6.2/6.5/6.7, §8).

Each function returns a plain JSON-serialisable structure. The template embeds it as
`<script type="application/json">` via Jinja's `tojson` filter, and `app/web/static/js/charts.js`
reads it to build the actual ECharts option -- no chart-drawing logic lives in Python.
"""

from app.pipeline.smart_money import SmartWalletSummary
from app.storage.tables import SnapshotRow
from app.web.format import pct, short_address
from app.web.links import wallet_explorer_url

TOP_HOLDERS_UNAVAILABLE_NOTE = (
    "Top holders aren't broken out separately (not queried, to save Nansen credits)."
)
SMART_MONEY_SHARE_UNAVAILABLE_NOTE = (
    "Smart money's share of supply needs a token-information lookup this snapshot doesn't have."
)


def ring_slices(
    *,
    bundle_supply_pct: float,
    bundle_wallet_count: int,
    smart_money_tokens: float,
    smart_money_wallet_count: int,
    circulating_supply: float | None,
) -> tuple[list[dict[str, object]], list[str]]:
    """The supply ring's slices, and any "not available" legend notes.

    UI_REDESIGN.md §7 item 3 asks for a stored `supply_breakdown` with per-cluster slices and a
    top-holders slice. This computes an equivalent view at render time from data already stored
    instead, for two reasons: clusters have no stable identity across snapshots to break the
    bundle into genuine per-cluster slices (so it's one combined "Bundle" slice here, matching how
    the §6.6 headline already describes the bundle as a whole rather than per cluster), and the
    top-holders endpoint was deliberately never queried (an earlier project decision, to save
    credits), so that slice is never available and is folded into "Rest" rather than invented.
    See docs/roadmap.md for what real per-cluster tracking would take.
    """
    notes: list[str] = []
    slices: list[dict[str, object]] = []
    bundle_pct = max(0.0, min(100.0, bundle_supply_pct))
    if bundle_pct > 0:
        slices.append(
            {"name": "Bundle", "pct": bundle_pct, "kind": "bundle", "wallets": bundle_wallet_count}
        )

    smart_pct = 0.0
    if circulating_supply:
        smart_pct = max(0.0, min(100.0, 100 * smart_money_tokens / circulating_supply))
        if smart_pct > 0:
            slices.append(
                {
                    "name": "Smart money",
                    "pct": smart_pct,
                    "kind": "smart",
                    "wallets": smart_money_wallet_count,
                }
            )
    elif smart_money_wallet_count:
        notes.append(SMART_MONEY_SHARE_UNAVAILABLE_NOTE)

    notes.append(TOP_HOLDERS_UNAVAILABLE_NOTE)

    rest_pct = max(0.0, 100.0 - bundle_pct - smart_pct)
    if rest_pct > 0 or not slices:
        slices.append({"name": "Rest of supply", "pct": rest_pct, "kind": "rest", "wallets": None})
    return slices, notes


def ring_aria_label(slices: list[dict[str, object]]) -> str:
    parts = [f"{s['name']} {pct(float(s['pct']))}" for s in slices]  # type: ignore[arg-type]
    return "Supply breakdown: " + ", ".join(parts)


def score_history_data(
    history: list[SnapshotRow], *, watch_min: float, green_min: float
) -> dict[str, object]:
    return {
        "points": [
            {
                "t": int(s.created_at.timestamp() * 1000),
                "score": s.score,
                "verdict": s.verdict,
            }
            for s in history
        ],
        "watchMin": watch_min,
        "greenMin": green_min,
    }


def tier_donut_data(wallets: list[SmartWalletSummary]) -> list[dict[str, object]]:
    """USD bought per tier (§6.7), largest first."""
    totals: dict[str, float] = {}
    for wallet in wallets:
        totals[wallet.tier] = totals.get(wallet.tier, 0.0) + wallet.bought_usd
    return [
        {"tier": tier, "usd": usd}
        for tier, usd in sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
    ]


def buy_sell_data(wallets: list[SmartWalletSummary], chain: str) -> list[dict[str, object]]:
    """One diverging bar per wallet: bought right, sold left (§6.7). Same order as the table.

    Each bar also carries the wallet's block-explorer URL, so clicking it opens the same page the
    table row's own explorer-link icon does -- one more way to jump from "this wallet looks
    interesting" to actually inspecting it on-chain, without a page reload.
    """
    return [
        {
            "address": wallet.address,
            "label": short_address(wallet.address),
            "bought": wallet.bought_usd,
            "sold": wallet.sold_usd,
            "explorerUrl": wallet_explorer_url(chain, wallet.address),
        }
        for wallet in wallets
    ]
