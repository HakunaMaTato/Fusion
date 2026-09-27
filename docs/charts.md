# Charts (UI redesign, §8)

Apache ECharts, self-hosted under `app/web/static/vendor/echarts.min.js`. Nothing is requested
from a CDN at runtime; `tests/unit/test_web.py` pins the file's hash, the same way it pins the
vendored htmx release.

## Licence

Apache License 2.0 (Copyright © Apache Software Foundation). ECharts is an Apache Software
Foundation project; the licence permits embedding and redistributing the built file as part of an
application. The upstream licence text lives in the [apache/echarts](https://github.com/apache/echarts)
repository (`LICENSE`) and is not duplicated here, matching how the project already treats HTMX's
licence (linked from `docs/dashboard.md`'s existing mention, not vendored as a text file).

## Source and build

`echarts@5.5.1`'s **`echarts.simple.min.js`** build, not the full `echarts.min.js`, because the
dashboard only needs three series types (`line`, `bar`, `pie`, the last used in donut mode for both
the supply ring and the tier breakdown) plus the tooltip/grid/markArea/markLine components used for
the verdict-threshold bands (§6.5). The simple build is about 468 KB versus the full build's
1.0 MB, and no other chart type is used anywhere in the app. The GraphicComponent and the built-in
Title/Legend components are **not** in this build — the app doesn't need them: every chart's center
label and legend are real HTML (§6.2, §8: "the legend or table beside it as real HTML"), not
ECharts elements, so they render even if the script fails to load.

| File | Source version | SHA-256 |
|---|---|---|
| `app/web/static/vendor/echarts.min.js` | echarts 5.5.1 (`dist/echarts.simple.min.js`) | `50e7e49a5bf9d425cf3a10805cb0db1a6824d3e4648e543cd945d06dac24c491` |

Note: the file's own internal `version` string reports `"5.6.0"`, not `"5.5.1"` — an upstream
inconsistency between the npm package version and the build's embedded version constant in this
release, not a sign of a wrong download (verified against the npm registry's own `5.5.1` package
metadata, and reproducible by fetching the same URL again).

`tests/unit/test_web.py::test_the_vendored_echarts_is_the_pinned_release` pins the hash above, so
an accidental or malicious file swap fails CI, the same way `test_the_vendored_htmx_is_the_pinned_release`
already does for htmx.

## Runtime

- `app/web/static/js/charts.js` registers one theme, `lp-radar`, that reads its colours and font
  from the CSS custom properties in `tokens.css` via `getComputedStyle` at page load, so a chart's
  colours can never drift out of sync with the rest of the UI.
- Each chart's data is embedded in the page as `<script type="application/json" id="chart-...">`
  and read by `charts.js` — no extra API call, and it still works if the dashboard is ever served
  behind a CSP that blocks inline `<script>` logic (the JSON blocks carry no executable code).
- Charts are (re-)initialised on `DOMContentLoaded` and on `htmx:afterSwap`, disposing any existing
  instance for that container first, so a future htmx-swapped region never leaks chart instances or
  duplicates them.
- Each chart's container has a `ResizeObserver` that calls `.resize()`, and `animation` is turned
  off for every chart when `prefers-reduced-motion: reduce` is set.
- A chart that fails to find its data script, or whose container isn't on the page, is skipped
  silently — every page still works if `echarts.min.js` fails to load at all (§10: "works with JS
  disabled, apart from the charts").

## Updating

To pick up a new ECharts release:

1. Confirm the exact version and build on the [npm package page](https://www.npmjs.com/package/echarts?activeTab=versions).
2. `curl -sL "https://cdn.jsdelivr.net/npm/echarts@<version>/dist/echarts.simple.min.js" -o app/web/static/vendor/echarts.min.js`
   — switch to `echarts.min.js` (the full build) only if a chart type outside line/bar/pie is ever
   needed.
3. Update the version and hash above and in `test_the_vendored_echarts_is_the_pinned_release`.
4. Re-run the visual review at desktop/tablet/mobile — a major version bump can change default
   spacing or tooltip styling.
