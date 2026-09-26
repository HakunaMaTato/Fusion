"""Turn backtest results into docs/backtest-results.md and a chart. Statistics only: no raw Nansen data."""

import math
import statistics
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from backtest.lifecycle import (
    CLASSES,
    CLEAN_MAX_BUNDLE_PCT,
    CLEAN_MIN_SMART_WALLETS,
    COLLAPSED,
    DEAD,
    MIN_GROUP,
    RETRACE_PCT,
    Calibration,
    calibrate,
    classify,
    is_clean,
)
from backtest.results import OutcomeRow, ResultRow, SkipRow, load_results, load_skips

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
REPORT_PATH = DOCS_DIR / "backtest-results.md"
CHART_PATH = DOCS_DIR / "backtest-results.svg"
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


def _share_text(values: list[float]) -> str:
    if not values:
        return "none"
    return (
        f"median {100 * statistics.median(values):.1f}% "
        f"(range {100 * min(values):.1f}-{100 * max(values):.1f}%)"
    )


def _bad_rate(rows: list[ResultRow], threshold: float | None) -> str:
    classes = [classify(r.lifecycle, threshold) for r in rows]
    known = [c for c in classes if c is not None]
    if not known:
        return "n/a"
    bad = sum(1 for c in known if c in (DEAD, COLLAPSED))
    lo, hi = wilson(bad, len(known))
    return (
        f"{100 * bad / len(known):.0f}% ({bad}/{len(known)}, 95% CI {100 * lo:.0f}-{100 * hi:.0f}%)"
    )


def _class_row(label: str, rows: list[ResultRow], threshold: float | None) -> list[str]:
    counts = Counter(c for r in rows if (c := classify(r.lifecycle, threshold)) is not None)
    troughs = [
        r.lifecycle.trough_pct
        for r in rows
        if r.lifecycle is not None and r.lifecycle.trough_pct is not None
    ]
    deep = sum(1 for t in troughs if t <= -RETRACE_PCT)
    return [
        label,
        str(sum(counts.values())),
        *(str(counts[c]) for c in CLASSES),
        _bad_rate(rows, threshold),
        f"{deep}/{len(troughs)}" if troughs else "n/a",
    ]


def lifecycle_headline(rows: list[ResultRow], cal: Calibration) -> str:
    avoid = [r for r in rows if r.verdict == "AVOID"]
    green = [r for r in rows if r.verdict == "GREEN"]
    if not any(classify(r.lifecycle, cal.threshold) for r in rows):
        return "No token has 72 hours of data after its decision yet."
    text = f"Dead or collapsed by +72h: AVOID {_bad_rate(avoid, cal.threshold)}"
    return text + f"; GREEN {_bad_rate(green, cal.threshold)}."


def lifecycle_lines(rows: list[ResultRow], cal: Calibration) -> list[str]:
    lines = [
        "## Lifecycle at +72h",
        "",
        "Price alone is a poor test of a volatile new token: many fall by half and come back, and "
        "some keep trading at a low price. Each token is followed for 72 hours from the decision "
        "and put in one class:",
        "",
        "- **dead**: volume in hours 48-72 is below the threshold below (share of the volume in "
        "the first 24 hours)",
        f"- **collapsed**: the price at +72h is at least {RETRACE_PCT:.0f}% below the decision "
        "price, and volume is still there",
        f"- **recovered**: the price fell at least {RETRACE_PCT:.0f}% at some point but ended "
        f"above that level",
        "- **held**: it never fell that far",
        "",
        "### Volume threshold, taken from the data",
        "",
        f"Tokens are labelled by price only: *rugged* = at least {cal.rug_pct:.0f}% below the "
        f"decision price at +72h, *survivor* = within {cal.survive_pct:.0f}% of it or above. "
        "Volume share = volume in hours 48-72 / volume in the first 24 hours.",
        "",
        f"- Rugged ({len(cal.rugged)}): {_share_text(cal.rugged)}",
        f"- Survivors ({len(cal.survivors)}): {_share_text(cal.survivors)}",
    ]
    if cal.threshold is None:
        lines += [
            f"- **Not enough tokens to set a threshold** (at least {MIN_GROUP} in each group are "
            "needed), so no token is called dead yet; only the price-based classes are shown.",
            "",
        ]
    else:
        loo = (
            f"{100 * cal.loo_accuracy:.0f}% over {cal.loo_n} tokens"
            if cal.loo_accuracy is not None
            else "n/a"
        )
        lines += [
            f"- **Threshold: a volume share below {100 * cal.threshold:.1f}% is called dead.** "
            f"Balanced accuracy on these tokens: {100 * (cal.accuracy or 0):.0f}%; "
            f"leave-one-out (each token judged by a threshold that did not use it): {loo}.",
            "",
        ]
    header = ["", "Tokens", *(c.capitalize() for c in CLASSES)]
    header += ["Dead or collapsed", f"Fell {RETRACE_PCT:.0f}%+ at any time"]
    by = [(v, [r for r in rows if r.verdict == v]) for v in VERDICTS]
    lines += [
        "### By verdict",
        "",
        _table(header, [_class_row(v, g, cal.threshold) for v, g in by]),
        "",
    ]
    clean = [r for r in rows if is_clean(r)]
    other = [r for r in rows if not is_clean(r)]
    lines += [
        "### Clean tokens against the rest",
        "",
        f"Clean = no veto, bundle holding under {CLEAN_MAX_BUNDLE_PCT:.0f}% of supply, at least "
        f"{CLEAN_MIN_SMART_WALLETS} smart-money wallets, and no net selling by them.",
        "",
        _table(
            header,
            [
                _class_row("Clean", clean, cal.threshold),
                _class_row("Not clean", other, cal.threshold),
            ],
        ),
        "",
    ]
    sens = []
    for rug in (80.0, 90.0, 95.0):
        for survive in (0.0, 25.0, 50.0):
            c = calibrate(rows, rug, survive)
            sens.append(
                [
                    f"{rug:.0f}% / {survive:.0f}%",
                    f"{len(c.rugged)} / {len(c.survivors)}",
                    f"{100 * c.threshold:.1f}%" if c.threshold is not None else "n/a",
                ]
            )
    lines += [
        "### How much the threshold depends on the labels",
        "",
        _table(["Rugged / survivor cut-offs", "Tokens (rugged / survivors)", "Threshold"], sens),
        "",
    ]
    return lines


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for the 2x2 table [[a, b], [c, d]]."""
    row1, col1, total = a + b, a + c, a + b + c + d

    def prob(x: int) -> float:
        return math.comb(col1, x) * math.comb(total - col1, row1 - x) / math.comb(total, row1)

    observed = prob(a)
    low, high = max(0, row1 - (total - col1)), min(row1, col1)
    return min(1.0, sum(p for x in range(low, high + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def _bad_count(rows: list[ResultRow], threshold: float | None) -> tuple[int, int]:
    known = [c for r in rows if (c := classify(r.lifecycle, threshold)) is not None]
    return sum(1 for c in known if c in (DEAD, COLLAPSED)), len(known)


CHECKS = 4
ALPHA = 0.05 / CHECKS  # Bonferroni: four checks were written down


def _check_row(
    name: str,
    a_label: str,
    a: list[ResultRow],
    b_label: str,
    b: list[ResultRow],
    threshold: float | None,
) -> list[str]:
    """Predicted: group A has the higher dead-or-collapsed rate than group B."""
    (bad_a, n_a), (bad_b, n_b) = _bad_count(a, threshold), _bad_count(b, threshold)
    if n_a == 0 or n_b == 0:
        return [name, f"{a_label}: n/a", f"{b_label}: n/a", "n/a", "not testable"]
    p = fisher_two_sided(bad_a, n_a - bad_a, bad_b, n_b - bad_b)
    as_predicted = bad_a / n_a > bad_b / n_b
    verdict = "supported" if as_predicted and p < ALPHA else "not supported"
    return [
        name,
        f"{a_label}: {bad_a}/{n_a} ({100 * bad_a / n_a:.0f}%)",
        f"{b_label}: {bad_b}/{n_b} ({100 * bad_b / n_b:.0f}%)",
        f"{p:.3f}",
        verdict if as_predicted else "opposite direction",
    ]


def fresh_lines(rows: list[ResultRow]) -> list[str]:
    fresh = [r for r in rows if r.batch == "fresh"]
    if not fresh:
        return []
    pilot = [r for r in rows if r.batch != "fresh"]
    threshold = calibrate(pilot).threshold  # fixed on the pilot tokens only; never sees fresh ones
    done = [r for r in fresh if classify(r.lifecycle, threshold) is not None]
    # H1 and H2 predict that the first group does BETTER, so test them with the groups swapped.
    h1 = _check_row(
        "H1 smart money",
        "no smart wallets",
        [r for r in done if r.sm_wallets == 0],
        "3+ smart wallets",
        [r for r in done if r.sm_wallets >= 3],
        threshold,
    )
    h2 = _check_row(
        "H2 bundle status",
        "bundle holding",
        [r for r in done if r.bundle_status == "holding"],
        "bundle distributing",
        [r for r in done if r.bundle_status == "distributing"],
        threshold,
    )
    h3 = _check_row(
        "H3 verdicts separate",
        "AVOID",
        [r for r in done if r.verdict == "AVOID"],
        "GREEN or WATCH",
        [r for r in done if r.verdict != "AVOID"],
        threshold,
    )
    h4 = _check_row(
        "H4 vetoes work",
        "a veto fired",
        [r for r in done if r.vetoes],
        "no veto",
        [r for r in done if not r.vetoes],
        threshold,
    )
    return [
        "## Pre-registered checks on fresh tokens",
        "",
        "These four checks were written down, with their predicted direction, before the fresh "
        "tokens were fetched, and the scoring was left unchanged. H1 and H2 came from looking at "
        "the first 83 tokens, so they are hypotheses being tested here on tokens they did not "
        "come from. H3 and H4 ask whether the current verdicts and vetoes do anything. "
        f"Two-sided Fisher exact test; with {CHECKS} checks the bar is p < {ALPHA:.4f}. "
        "The outcome is dead or collapsed by +72h, using the volume threshold calibrated on the "
        "first tokens only" + (f" ({100 * threshold:.1f}%)." if threshold is not None else "."),
        "",
        f"Fresh tokens analysed: {len(fresh)}; with a complete 72-hour window: {len(done)}.",
        "",
        _table(
            ["Check", "Predicted worse group", "Comparison group", "p", "Result"], [h1, h2, h3, h4]
        ),
        "",
    ]


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
    cal = calibrate(rows)
    lines = [
        "# Backtest results",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC by `python -m backtest.run report`. "
        f"{len(rows)} tokens analysed, {len(skips)} skipped.",
        "",
        "## Headline",
        "",
        lifecycle_headline(rows, cal),
        "",
        headline(rows, 24, threshold),
        "",
        "![Dump rate by verdict](backtest-results.svg)",
        "",
        *fresh_lines(rows),
        *lifecycle_lines(rows, cal),
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
