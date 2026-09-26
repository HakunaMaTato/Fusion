from dataclasses import dataclass
from datetime import datetime
from html import escape

from markupsafe import Markup

WIDTH = 640
HEIGHT = 180
PAD_LEFT = 34
PAD_RIGHT = 12
PAD_TOP = 12
PAD_BOTTOM = 22


@dataclass(frozen=True)
class ScorePoint:
    at: datetime
    score: float
    verdict: str


def score_history_svg(points: list[ScorePoint], watch_min: float, green_min: float) -> Markup:
    """Score over time as inline SVG (0-100 axis, verdict thresholds as guide lines).

    Colours come from CSS classes so a strict Content-Security-Policy can stay in place, and every
    value placed in the markup is a number or an escaped string.
    """
    if not points:
        return Markup("")
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    start, end = points[0].at, points[-1].at
    span = (end - start).total_seconds()

    def x(point: ScorePoint) -> float:
        if len(points) == 1 or span <= 0:
            return PAD_LEFT + plot_w / 2
        return PAD_LEFT + plot_w * (point.at - start).total_seconds() / span

    def y(score: float) -> float:
        return PAD_TOP + plot_h * (1 - max(0.0, min(100.0, score)) / 100)

    parts = [
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="Score history, {len(points)} snapshots, latest {points[-1].score:.0f}">'
    ]
    for level, label in ((0, "0"), (watch_min, "WATCH"), (green_min, "GREEN"), (100, "100")):
        parts.append(
            f'<line class="chart-grid" x1="{PAD_LEFT}" x2="{WIDTH - PAD_RIGHT}" '
            f'y1="{y(level):.1f}" y2="{y(level):.1f}"/>'
            f'<text class="chart-label" x="{PAD_LEFT - 4}" y="{y(level) + 3:.1f}" '
            f'text-anchor="end">{escape(label)}</text>'
        )
    if len(points) > 1:
        line = " ".join(f"{x(p):.1f},{y(p.score):.1f}" for p in points)
        parts.append(f'<polyline class="chart-line" points="{line}"/>')
    for p in points:
        css = f"chart-point verdict-{escape(p.verdict.lower())}"
        title = f"{p.at:%Y-%m-%d %H:%M} UTC: {p.score:.0f} {p.verdict}"
        parts.append(
            f'<circle class="{css}" cx="{x(p):.1f}" cy="{y(p.score):.1f}" r="4">'
            f"<title>{escape(title)}</title></circle>"
        )
    parts.append(
        f'<text class="chart-label" x="{PAD_LEFT}" y="{HEIGHT - 6}">{start:%d %b %H:%M}</text>'
        f'<text class="chart-label" x="{WIDTH - PAD_RIGHT}" y="{HEIGHT - 6}" text-anchor="end">'
        f"{end:%d %b %H:%M}</text></svg>"
    )
    return Markup("".join(parts))
