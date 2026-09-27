# Icons (UI redesign, §4/§5/§8)

[Feather Icons](https://github.com/feathericons/feather) (v4.29.2, MIT, Copyright (c)
2013-2023 Cole Bemis), inlined as raw `<svg>` markup directly inside the Jinja component macros
(`app/web/templates/components/icons.html`) rather than loaded as files, so using an icon costs no
extra HTTP request. Each one uses `stroke="currentColor"`, so it always takes the surrounding
text's colour from CSS — never an inline `style` attribute, which the CSP and
`tests/unit/test_web.py` both forbid.

| Icon | Used for |
|---|---|
| `check-circle` | Verdict pill, GREEN |
| `eye` | Verdict pill, WATCH |
| `alert-triangle` | Verdict pill, AVOID |
| `copy` | Address chip's copy button |
| `check` | Address chip's copy button, shown for 1.5s after a successful copy |
| `external-link` | Address chip's explorer link |
| `clock` | List table, a row whose "Updated" time is stale (issue 15) |
| `search` | List toolbar's search input |
| `chevron-up` / `chevron-down` | Sortable table headers, `aria-sort` |
| `x` | Toolbar's "Clear filters" and the empty state |

Source: `https://raw.githubusercontent.com/feathericons/feather/main/icons/<name>.svg`, fetched
2026-09-27. The MIT licence text is not vendored as a separate file since nothing beyond the
markup itself is redistributed; this page is the attribution.

## Updating

Feather rarely changes existing icon paths (they're simple line-icon glyphs), but to refresh one:
fetch the same URL for the icon name, copy the `<path>`/`<circle>`/`<polyline>`/`<rect>` children
into the matching macro in `icons.html`, and keep the wrapping `<svg>` attributes (`viewBox="0 0 24
24"`, `stroke="currentColor"`, etc.) as they are.
