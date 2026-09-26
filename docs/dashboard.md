# Dashboard (Phase 7)

Server-rendered with Jinja2 and HTMX; no JavaScript build step. The web app only reads the SQLite database
the worker writes (`docs/worker.md`). It never calls Nansen and never creates tables, so it cannot spend
credits.

## Pages
- `/`: table of tokens with their latest verdict: token, chain, age (computed at render time), market cap,
  verdict badge, score, bundle %, smart wallets, last updated. GREEN first, then WATCH, then AVOID, by score
  within each; at most 200 rows. Filters for verdict and chain are plain links (`?verdict=GREEN&chain=solana`),
  so the page works without JavaScript. HTMX re-fetches only the table from `/partials/tokens` every 30 seconds,
  keeping the current filters. Unknown filter values are ignored, never echoed.
- `/token/{chain}/{address}`: verdict, score, why (vetoes and reasons), the score components, an inline SVG score
  history, the bundle clusters (wallets and why they were clustered), and a per-wallet smart money summary
  (tier, bought, sold, still holding). Links go to the block explorer (Solscan, Basescan, BscScan, Etherscan) and
  to the Nansen app home. Nansen documents no token-level app URL, so there is no deep link. A token whose
  address is malformed for its chain gets no explorer link.
- `/status`: credits used today against the budget, this hour's credit share, data mode, worker heartbeat, last
  discovery poll, last analysis, and a health line (healthy, stale or no heartbeat). It never shows the API key,
  database URL or any error text.
- Every page has the footer "Research tool, not financial advice." and works at phone width: under 640px the
  tables become cards and nothing scrolls sideways.

## Security
- Jinja2 autoescape is on. Token names, symbols, labels and reasons come from outside, and tests render a hostile
  `<img onerror>` symbol and a `<script>` reason.
- Explorer links are built only from a strict address-format check and URL-quoted.
- `Content-Security-Policy: default-src 'self'` (no inline scripts or styles, no third-party hosts),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on pages. Colours
  and chart styling use CSS classes, and HTMX's inline indicator style and `eval` are switched off in its config.
- Database and unexpected errors render a generic page ("temporarily unavailable" or "Something went wrong");
  details go to the server log only. An empty or table-less database shows an empty state.
- A test asserts that a synthetic API key set in the settings appears in none of the pages, error pages included.
- The dashboard has no login. Put it behind the reverse proxy's basic auth (Phase 9) if the data must not be
  public; see the Nansen redistribution note in SPEC.md section 10.

## HTMX
`app/web/static/htmx.min.js` is htmx 2.0.4, downloaded once from
`https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js`, so no third-party host is needed at runtime.
SHA-256 `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` (a test fails if the file changes);
SHA-384 `HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+`. To upgrade, download the new
release, update both checksums here and in `tests/unit/test_web.py`.

## Trying it without credits
Use the project's virtual environment (created by `make install` or `python -m venv .venv`), not the system
Python, otherwise `import app` fails.

PowerShell (Windows):
```powershell
.\.venv\Scripts\python.exe scripts\seed_demo.py sqlite:///demo.db
$env:DATABASE_URL = "sqlite:///demo.db"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# afterwards: Remove-Item Env:DATABASE_URL
```

bash (Linux, macOS, Git Bash):
```bash
.venv/bin/python scripts/seed_demo.py sqlite:///demo.db
DATABASE_URL=sqlite:///demo.db .venv/bin/python -m uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000. The script fills four demo tokens with history (one has a hostile name). The seeded heartbeat is only fresh for five minutes; re-run the script for a healthy status line. Existing
databases are not migrated: the heartbeat table gained a `last_discovery_at` column, so a database created by an
older worker build needs to be recreated.
