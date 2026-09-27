# UI tests (`tests/ui/`)

UI_REDESIGN.md §10's screenshot and accessibility pass, automated with Playwright. Separate from
the main `pytest` run (`tests/unit`, `tests/contract`) because it needs a downloaded Chromium
build (~200 MB) and drives a real HTTP server instead of FastAPI's in-process `TestClient`.

## Running it

```bash
pip install -e .[ui]
playwright install chromium
make ui-test          # = NANSEN_MODE=replay pytest tests/ui
```

CI runs this in its own `ui-tests` job (`.github/workflows/ci.yml`), separate from the fast
`check` job, and uploads `tests/ui/screenshots/` as a build artifact.

## What it checks

- **Screenshots**: both pages (`/` and a token detail page), at the three breakpoints §10 names
  (1440×900, 1024×768, 390×844), saved to `tests/ui/screenshots/` (gitignored).
- **Accessibility**: an axe-core pass on `/`, a token page, and `/status`, failing on any
  violation with `impact` of `serious` or `critical`.
- **Keyboard navigation**: a spot check that tabbing from the top of the list page reaches the
  search input with a visible focus indicator, without getting stuck.

## What it deliberately does not do

**No pixel-diff visual regression against committed baseline images.** §10 also asks for this
("commit baseline screenshots... fail on unexpected diffs... update baselines deliberately, in
the PR that changes the design"). It isn't implemented, because the project has no established
review workflow for it yet: a screenshot that changes because a phase *intentionally* changed the
design would fail the same way as one that changed because of a real regression, and someone still
has to look at the diff and decide which it was -- at which point they're doing exactly what the
manual "look at every screenshot yourself" step from earlier phases already did. Automating the
capture (this harness) removes the toil of taking the screenshots by hand; automating the
judgment call would need a baseline-update process (who approves a new baseline, where the old one
goes, how a reviewer sees the diff in a PR) that doesn't exist yet. Worth building if the project
keeps evolving the design after UI-6, but out of scope here.

## Found by this harness, fixed in UI-6

The first real run of this suite caught three genuine bugs the manual per-phase browser review had
missed:

- `aria-pressed` on a plain `<a>` (the list page's "Include tokens over 24h" toggle) is not a
  valid ARIA attribute without `role="button"` -- axe-core flagged it as a critical violation.
- The token list's short-address text (`--text-faint` on `--surface`, per §5.4's own literal
  wording) measured 3.05:1 contrast, below WCAG AA's 4.5:1 for normal text. Switched to
  `--text-muted` (this is a disclosed deviation from §5.4's exact token choice, in favour of
  actually meeting the accessibility bar the same section's own checklist demands).
- The `--avoid` verdict colour on its own 14%-opacity tint (used for the Avoid pill and the veto
  banner) measured 4.33:1, just under the same 4.5:1 threshold; `--green` and `--watch` were
  already comfortably above it at the same opacity. Lowered `--avoid-tint` to 8% opacity, which
  clears the threshold with a small margin without changing the colour's hue.
