"""Turn backtest results into docs/backtest.md and a chart. Statistics only: no raw Nansen data."""

import math
import statistics
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from backtest.results import OutcomeRow, ResultRow, SkipRow, load_results, load_skips

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
REPORT_PATH = DOCS_DIR / "backtest.md"
CHART_PATH = DOCS_DIR / "backtest.svg"
VERDICTS = ("GREEN", "WATCH", "AVOID")


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion, as fractions."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _outcome(row: ResultRow, hours: int) -> OutcomeRow | None:
    return next((o for o in row.outcomes if o.hours == hours), None)


def dump_rate(rows: list[ResultRow], hours: int) -> tuple[int, int]:
    """(dumped, eligible) among rows whose outcome for that horizon is complete."""
    eligible = [o for r in rows if (o := _outcome(r, hours)) is not None and o.complete]
    return sum(1 for o in eligible if o.dumped), len(eligible)


def _values(rows: list[ResultRow], hours: int, field: str) -> list[float]:
    found = []
    for row in rows:
        outcome = _outcome(row, hours)
        if outcome is not None and outcome.complete:
            value = getattr(outcome, field)
            if value is not None:
                found.append(float(value))
    return found


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):+.1f}%" if values else "n/a"


def _rate(rows: list[ResultRow], hours: int) -> str:
    dumped, total = dump_rate(rows, hours)
    if total == 0:
        return "n/a"
    lo, hi = wilson(dumped, total)
    return f"{100 * dumped / total:.0f}% ({dumped}/{total}, 95% CI {100 * lo:.0f}-{100 * hi:.0f}%)"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def headline(rows: list[ResultRow], hours: int, threshold: float) -> str:
    by = {v: [r for r in rows if r.verdict == v] for v in VERDICTS}
    avoid, green = dump_rate(by["AVOID"], hours), dump_rate(by["GREEN"], hours)
    if avoid[1] == 0:
        return "No AVOID tokens with a complete outcome in the sample."
    text = (
        f"AVOID tokens fell more than {threshold:.0f}% within {hours}h in "
        f"{100 * avoid[0] / avoid[1]:.0f}% of cases ({avoid[0]}/{avoid[1]})"
    )
    if green[1] == 0:
        return text + "; there were no GREEN tokens with a complete outcome to compare with."
    return text + f", against {100 * green[0] / green[1]:.0f}% for GREEN ({green[0]}/{green[1]})."


def veto_effects(rows: list[ResultRow], hours: int) -> list[list[str]]:
    codes = sorted({code for row in rows for code in row.vetoes})
    table = []
    for code in codes:
        with_veto = [r for r in rows if code in r.vetoes]
        without = [r for r in rows if code not in r.vetoes]
        table.append(
            [f"`{code}`", str(len(with_veto)), _rate(with_veto, hours), _rate(without, hours)]
        )
    return table


def chart_svg(rows: list[ResultRow], hours: int) -> str:
    """Dump rate by verdict with 95% intervals, as a self-contained SVG."""
    colours = {"GREEN": "#14783f", "WATCH": "#b07a00", "AVOID": "#b3261e"}
    width, height, left, top, plot_h = 480, 240, 48, 20, 170
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Share of tokens that fell more than the threshold within {hours} hours">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for tick in (0, 25, 50, 75, 100):
        y = top + plot_h * (1 - tick / 100)
        parts.append(
            f'<line x1="{left}" x2="{width - 10}" y1="{y:.1f}" y2="{y:.1f}" stroke="#dfe3e8"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" '
            f'fill="#5d6673">{tick}%</text>'
        )
    for i, verdict in enumerate(VERDICTS):
        dumped, total = dump_rate([r for r in rows if r.verdict == verdict], hours)
        x = left + 40 + i * 130
        if total:
            lo, hi = wilson(dumped, total)
            rate = dumped / total
            y = top + plot_h * (1 - rate)
            parts.append(
                f'<rect x="{x}" y="{y:.1f}" width="70" height="{plot_h * rate:.1f}" '
                f'fill="{colours[verdict]}"/>'
            )
            y_lo, y_hi = top + plot_h * (1 - lo), top + plot_h * (1 - hi)
            parts.append(
                f'<line x1="{x + 35}" x2="{x + 35}" y1="{y_hi:.1f}" y2="{y_lo:.1f}" '
                'stroke="#16181d"/>'
            )
        label = f"{verdict} (n={total})"
        parts.append(
            f'<text x="{x + 35}" y="{top + plot_h + 18}" font-size="12" text-anchor="middle" '
            f'fill="#16181d">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def build_report(
    rows: list[ResultRow],
    skips: list[SkipRow],
    *,
    threshold: float,
    horizons: list[int],
    generated_at: datetime,
    reduced_inputs: str,
) -> str:
    by = {v: [r for r in rows if r.verdict == v] for v in VERDICTS}
    lines = [
        "# Backtest results",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC by `python -m backtest.run report`. "
        f"{len(rows)} tokens analysed, {len(skips)} skipped.",
        "",
        "## Headline",
        "",
        headline(rows, 24, threshold),
        "",
        "![Dump rate by verdict](backtest.svg)",
        "",
        "## Outcomes by verdict",
        "",
        f'"Dumped" means the price fell more than {threshold:.0f}% below the price at the decision '
        "time within the horizon. Only tokens whose data reaches the horizon are counted.",
        "",
    ]
    header = ["Verdict", "Tokens"]
    for h in horizons:
        header += [f"Dumped within {h}h"]
    header += ["Median max drawdown 24h", "Median change +24h", "Bundle exited within 24h"]
    table = []
    for verdict in VERDICTS:
        group = by[verdict]
        exits = [r.bundle_exited_24h for r in group if r.bundle_exited_24h is not None]
        table.append(
            [verdict, str(len(group))]
            + [_rate(group, h) for h in horizons]
            + [
                _median(_values(group, 24, "max_drawdown_pct")),
                _median(_values(group, 24, "price_change_pct")),
                f"{sum(exits)}/{len(exits)}" if exits else "n/a",
            ]
        )
    lines += [_table(header, table), ""]

    change_rows = [
        [verdict] + [_median(_values(by[verdict], h, "price_change_pct")) for h in horizons]
        for verdict in VERDICTS
    ]
    lines += [
        "### Median price change after the decision",
        "",
        _table(["Verdict"] + [f"+{h}h" for h in horizons], change_rows),
        "",
        "## Effect of each veto",
        "",
        "Dump rate within 24h for tokens that triggered a veto against those that did not.",
        "",
    ]
    effects = veto_effects(rows, 24)
    if effects:
        lines += [_table(["Veto", "Tokens", "With the veto", "Without it"], effects), ""]
    else:
        lines += ["No token triggered a veto in this sample.", ""]

    reasons = Counter(s.reason for s in skips)
    lines += ["## Coverage", "", f"- Tokens analysed: {len(rows)}"]
    lines += [f"- Skipped, `{reason}`: {count}" for reason, count in sorted(reasons.items())]
    dropped = sum(r.lookahead_rows_dropped for r in rows)
    lines += [
        f"- Rows stamped after a decision time that were dropped before analysis: {dropped}",
        f"- Credits spent on the analysed tokens: {sum(r.credits_spent for r in rows)}",
        "",
        "## Method and limits",
        "",
        "- Candidates: young, liquid tokens from the historical screener, not filtered on "
        "market cap (that would hide tokens that pumped and then collapsed).",
        "- Decision time: the close of the first 5-minute candle at or above the market cap "
        "threshold within the pump window. Only data up to that moment reaches the analysis.",
        f"- {reduced_inputs}",
        "- Balances are rebuilt from trades between launch and the decision; a wallet with no "
        "trades in that window counts as holding nothing.",
        "- Funder lookups use the current related-wallets endpoint; a funding relationship is a "
        "permanent on-chain fact, so this does not look ahead.",
        "- All historical endpoints are beta and may be restated. The sample is small and the "
        "intervals are wide: treat this as evidence for tuning `scoring.yaml`, not proof.",
        "",
    ]
    return "\n".join(lines)


REDUCED_INPUTS = (
    "Reduced-input score: holder count and liquidity do not exist point-in-time, so those two "
    "components are left out and the remaining weights are re-scaled. Verdict thresholds are "
    "unchanged."
)


def write_report(
    rows: list[ResultRow],
    skips: list[SkipRow],
    *,
    threshold: float,
    horizons: list[int],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    report_path: Path = REPORT_PATH,
    chart_path: Path = CHART_PATH,
) -> str:
    text = build_report(
        rows,
        skips,
        threshold=threshold,
        horizons=horizons,
        generated_at=now(),
        reduced_inputs=REDUCED_INPUTS,
    )
    report_path.write_text(text, encoding="utf-8")
    chart_path.write_text(chart_svg(rows, 24), encoding="utf-8")
    return text


def main_report(threshold: float, horizons: list[int]) -> str:
    return write_report(load_results(), load_skips(), threshold=threshold, horizons=horizons)
