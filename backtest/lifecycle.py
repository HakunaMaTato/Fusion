"""How a token fares over 72 hours, and a volume threshold for "dead" taken from the data itself.

A fixed rule such as "volume fell below 5% of the first day" would be a number picked by hand.
Instead, tokens are labelled by price alone (rugged: at least `RUG_PCT` below the decision price at
+72h; survivors: within `SURVIVE_PCT` of it or above) and the volume share that best separates the
two groups becomes the threshold. It is only trusted when both groups are large enough, and its
accuracy is checked leave-one-out so it is not graded on the tokens that set it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from backtest.results import LifecycleRow, ResultRow
from backtest.timeline import Candle, Decision

HORIZON_HOURS = 72
EARLY_HOURS = 24  # volume window right after the decision
LATE_FROM_HOURS = 48  # volume window ending at the horizon

DEAD = "dead"
COLLAPSED = "collapsed"
RECOVERED = "recovered"
HELD = "held"
CLASSES = (DEAD, COLLAPSED, RECOVERED, HELD)

RETRACE_PCT = 50.0  # a fall this deep at any time counts as a retrace
RUG_PCT = 90.0
SURVIVE_PCT = 25.0
MIN_GROUP = 3

# "Clean" tokens: what the signal is meant to pick out, for the second cut of the report.
CLEAN_MAX_BUNDLE_PCT = 5.0
CLEAN_MIN_SMART_WALLETS = 2


def compute_lifecycle(
    candles: list[Candle], decision: Decision, *, data_end: datetime
) -> LifecycleRow:
    start = decision.decision_at
    end = start + timedelta(hours=HORIZON_HOURS)
    if data_end < end:
        return LifecycleRow(
            complete=False,
            trough_pct=None,
            trough_hours=None,
            change_pct=None,
            volume_first_usd=None,
            volume_late_usd=None,
        )
    ref = decision.reference_price
    window = [c for c in candles if c.start >= start and c.end <= end]
    lows = [(c.low, c.start) for c in window if c.low is not None]
    closes = [c.close for c in window if c.close is not None]
    trough, trough_at = min(lows) if lows else (ref, start)
    early_end = start + timedelta(hours=EARLY_HOURS)
    late_start = start + timedelta(hours=LATE_FROM_HOURS)
    return LifecycleRow(
        complete=True,
        trough_pct=min((trough / ref - 1) * 100, 0.0),
        trough_hours=(trough_at - start).total_seconds() / 3600,
        change_pct=(closes[-1] / ref - 1) * 100 if closes else 0.0,
        volume_first_usd=sum(c.volume_usd or 0.0 for c in window if c.start < early_end),
        volume_late_usd=sum(c.volume_usd or 0.0 for c in window if c.start >= late_start),
    )


def volume_share(lc: LifecycleRow | None) -> float | None:
    """Volume in hours 48-72 as a share of the volume in the first 24 hours."""
    if lc is None or not lc.complete or not lc.volume_first_usd:
        return None
    return (lc.volume_late_usd or 0.0) / lc.volume_first_usd


def _balanced_accuracy(threshold: float, rugged: list[float], survivors: list[float]) -> float:
    caught = sum(share < threshold for share in rugged) / len(rugged)
    spared = sum(share >= threshold for share in survivors) / len(survivors)
    return (caught + spared) / 2


def best_threshold(rugged: list[float], survivors: list[float]) -> float:
    """The share below which a token is called dead: best balanced accuracy, middle of ties."""
    values = sorted(set(rugged + survivors))
    if len(values) == 1:
        return values[0]
    candidates = [(a + b) / 2 for a, b in zip(values, values[1:], strict=False)]
    scores = [_balanced_accuracy(c, rugged, survivors) for c in candidates]
    tied = [c for c, s in zip(candidates, scores, strict=True) if s == max(scores)]
    return tied[len(tied) // 2]


@dataclass(frozen=True)
class Calibration:
    rugged: list[float]
    survivors: list[float]
    threshold: float | None
    accuracy: float | None  # balanced accuracy on the labelled tokens themselves
    loo_accuracy: float | None  # leave-one-out: each token judged by a threshold not using it
    loo_n: int
    rug_pct: float
    survive_pct: float


def _labelled(
    rows: list[ResultRow], rug_pct: float, survive_pct: float
) -> list[tuple[float, bool]]:
    found = []
    for row in rows:
        lc, share = row.lifecycle, volume_share(row.lifecycle)
        if lc is None or share is None or lc.change_pct is None:
            continue
        if lc.change_pct <= -rug_pct:
            found.append((share, True))
        elif lc.change_pct >= -survive_pct:
            found.append((share, False))
    return found


def calibrate(
    rows: list[ResultRow],
    rug_pct: float = RUG_PCT,
    survive_pct: float = SURVIVE_PCT,
    min_group: int = MIN_GROUP,
) -> Calibration:
    labelled = _labelled(rows, rug_pct, survive_pct)
    rugged = [s for s, is_rug in labelled if is_rug]
    survivors = [s for s, is_rug in labelled if not is_rug]
    if len(rugged) < min_group or len(survivors) < min_group:
        return Calibration(rugged, survivors, None, None, None, 0, rug_pct, survive_pct)
    threshold = best_threshold(rugged, survivors)
    right = evaluated = 0
    for i, (share, is_rug) in enumerate(labelled):
        rest = labelled[:i] + labelled[i + 1 :]
        rest_rug = [s for s, r in rest if r]
        rest_ok = [s for s, r in rest if not r]
        if len(rest_rug) < min_group or len(rest_ok) < min_group:
            continue
        evaluated += 1
        right += (share < best_threshold(rest_rug, rest_ok)) == is_rug
    return Calibration(
        rugged,
        survivors,
        threshold,
        _balanced_accuracy(threshold, rugged, survivors),
        right / evaluated if evaluated else None,
        evaluated,
        rug_pct,
        survive_pct,
    )


def classify(lc: LifecycleRow | None, threshold: float | None) -> str | None:
    """dead (volume gone), collapsed, recovered after a deep dip, or held; None if not complete."""
    if lc is None or not lc.complete or lc.change_pct is None or lc.trough_pct is None:
        return None
    share = volume_share(lc)
    if threshold is not None and share is not None and share < threshold:
        return DEAD
    if lc.change_pct <= -RETRACE_PCT:
        return COLLAPSED
    if lc.trough_pct <= -RETRACE_PCT:
        return RECOVERED
    return HELD


def is_clean(row: ResultRow) -> bool:
    return (
        not row.vetoes
        and row.bundle_supply_pct < CLEAN_MAX_BUNDLE_PCT
        and row.sm_wallets >= CLEAN_MIN_SMART_WALLETS
        and not row.sm_net_selling
    )
