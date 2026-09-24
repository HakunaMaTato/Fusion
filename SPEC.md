# LP Radar — Build Spec

## 1. What we're building

A service that, every minute or two:

1. **Discovers** fresh tokens (age < 24h) that pumped fast (reached ≥ $1M market cap within 0–4h of deployment), plus fresh tokens Nansen Smart Money is buying.
2. **Analyses the launch for bundling**: clusters of early buyers that share a funder, bought in the same second, or bought suspiciously similar sizes. It measures how much supply those clusters still hold.
3. **Analyses Smart Money**: how many smart wallets are in, which labels (Fund vs short-term trader), net flow, and whether they're holding or have flipped.
4. **Cross-checks** the two: are any smart money wallets themselves inside a bundle cluster?
5. **Scores** each token as an LP candidate: GREEN / WATCH / AVOID, with reasons.
6. **Monitors** tokens on the watchlist and **alerts** (Telegram) when the verdict changes, especially when a bundle or smart money starts selling.
7. **Shows** everything on a web dashboard, and **proves** the signal with a backtest over Nansen's historical endpoints.

Out of scope for v1: executing trades, managing LP positions, holding keys.

## 2. Nansen API reference (verify before use)

Base URL `https://api.nansen.ai`. Auth header `apikey: <KEY>`. All endpoints are `POST` with JSON bodies. Responses include credit headers (`X-Nansen-Credits-Used`, `X-Nansen-Credits-Remaining`) and rate-limit headers; 429 responses include `Retry-After`.

The endpoints below are the ones we expect to use. Paths, fields and credit costs **must be verified** against the live docs before implementation (see CLAUDE.md rule 4). Docs index: `https://docs.nansen.ai/llms.txt`.

| Purpose | Endpoint | Notes to verify |
|---|---|---|
| Discovery: fresh pumps | `/api/v1/token-screener` | `timeframe` (5m…30d), filters `token_age_days` (float), `market_cap_usd`; response has `token_age_hours`, `token_deployment_date`, `market_cap_usd`, `liquidity`, `volume`. Live only, no historical snapshots. |
| Discovery + SM analysis | `/api/v1/smart-money/dex-trades` | Trailing 24h only. Filters incl. `token_bought_address`, `token_sold_address`, `token_bought_age_days` (response field is an integer day count), `token_bought_market_cap`, `include_smart_money_labels`. |
| Early trades for bundle detection | `/api/v1/tgm/dex-trades` | Sort `block_timestamp ASC`; fields `block_timestamp`, `transaction_hash`, `trader_address`, `trader_address_label`, `action`, amounts. Check whether a slot/block number exists. |
| Funder clustering | `/api/v1/profiler/address/related-wallets` | First-degree relations. Confirm how "funded by" is represented. 1 credit per wallet — cache aggressively. |
| Current balances | `/api/v1/tgm/holders`, `/api/v1/profiler/address/current-balance` | Holders returns top holders only; fall back to per-wallet balance for cluster wallets not in the top list. |
| Supply / token stats | `/api/v1/tgm/token-information` | Total/circulating supply, holders count, deployer if available. |
| Segment flows | `/api/v1/tgm/flow-intelligence` | Smart Money, whales, fresh wallets, exchanges. |
| Backtest | `/api/v1beta1/tgm/historical-dex-trades`, `.../historical-top-holders`, `token-screener/historical` | Beta; request body shapes differ (`date_range: {from, to}`). |

Record every verified schema, cost and limit in `docs/nansen-endpoints.md`, with the date you checked.

## 3. Repository layout

```
lp-radar/
  app/
    main.py              FastAPI app (dashboard + JSON API + /healthz)
    worker.py            polling loop entrypoint
    config.py            pydantic-settings; reads .env and config/scoring.yaml
    nansen/
      client.py          the only HTTP client: auth, retries, rate limits, credit tracking, cache, replay mode
      models.py          pydantic response models
      endpoints.py       one typed function per endpoint
    pipeline/
      discovery.py       merge both feeds into candidates
      bundle.py          cluster detection + supply share + status
      smart_money.py     SM participation metrics
      crosscheck.py      SM-in-bundle detection
      scoring.py         LP score, vetoes, verdict, reasons
      monitor.py         re-evaluate watchlist, detect verdict transitions
    alerts/telegram.py
    storage/db.py, storage/tables.py
    web/routes.py, web/templates/
  backtest/run.py, backtest/report.py
  config/scoring.yaml
  tests/unit/, tests/contract/, tests/live/, tests/fixtures/, tests/factories.py
  scripts/record_fixtures.py
  deploy/Caddyfile, deploy/setup-vm.sh
  docs/nansen-endpoints.md, docs/scoring.md, docs/deploy.md
  Dockerfile, docker-compose.yml, Makefile, pyproject.toml
  .env.example, .gitignore, .dockerignore
  .github/workflows/ci.yml, .github/workflows/deploy.yml
  README.md, CLAUDE.md, SPEC.md
```

## 4. Configuration

`.env.example`:

```
NANSEN_API_KEY=replace-me
NANSEN_MODE=replay              # replay | live
DAILY_CREDIT_BUDGET=3000
CHAINS=solana,base,bnb
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DATABASE_URL=sqlite:////data/lp-radar.db
DASHBOARD_BASIC_AUTH=           # user:bcrypt-hash, optional, enforced by Caddy
LOG_LEVEL=INFO
```

`config/scoring.yaml` holds every threshold. Starting values (tune with the backtest):

```yaml
discovery:
  max_age_hours: 24
  pump_window_hours: 4
  min_market_cap_usd: 1000000
  screener_timeframe: 1h
  poll_seconds: 90
bundle:
  early_window_minutes: 10
  max_early_buys: 150
  same_second_min_wallets: 3
  common_funder_min_wallets: 3
  similar_size_max_cv: 0.15
  ignore_funder_labels: [CEX, Exchange, DEX Router, Bridge]   # hot wallets are not bundlers
  distributing_sold_pct: 25
  exited_sold_pct: 90
smart_money:
  min_wallets_for_signal: 2
  label_weights: {Fund: 1.5, "180D Smart Trader": 1.3, "90D Smart Trader": 1.2, "Smart Trader": 1.0, "30D Smart Trader": 0.7}
scoring:
  veto_bundle_supply_pct: 30
  veto_sm_net_selling: true
  veto_sm_in_bundle: true
  green_min: 70
  watch_min: 40
budget:
  max_deep_analyses_per_hour: 20
  related_wallets_cache_hours: 24
```

## 5. Build phases

Each phase = one branch + one PR. Acceptance criteria must all be met.

### Phase 0 — Scaffold and CI
- `pyproject.toml`, `Makefile`, `.gitignore` (must include `.env`, `*.db`, `.venv/`), `.env.example`, empty package tree, `/healthz` endpoint.
- GitHub Actions `ci.yml`: on push and PR, run ruff, mypy, pytest (replay mode, no secrets needed), and `docker build`.
- **Accept:** `make check` passes; CI is green on the first PR; `git grep -i "apikey"` finds no real keys.

### Phase 1 — Nansen client
- Async `httpx` client with: auth header; timeouts; retries with exponential backoff on 5xx and network errors; honour `Retry-After` on 429; a client-side rate limiter kept safely under the plan's limits.
- Credit tracking: read `X-Nansen-Credits-Used` from every response and persist a daily total. Refuse calls (raise `BudgetExceeded`) once `DAILY_CREDIT_BUDGET` is hit.
- TTL cache keyed by endpoint + body (related-wallets results cached 24h).
- `NANSEN_MODE=replay`: serve responses from `tests/fixtures/<endpoint>/<hash>.json`, and fail clearly if a fixture is missing.
- `scripts/record_fixtures.py` records a small, fixed set of live calls (asks for confirmation and prints an estimated credit cost first).
- Typed functions in `endpoints.py` + pydantic models for every endpoint in §2.
- **Accept:** unit tests (with `respx`) cover success, 429 with Retry-After, 5xx retry then success, budget exceeded, cache hit, and replay-missing-fixture. Contract tests parse every recorded fixture with the models.

### Phase 2 — Discovery
- Feed A: token screener with `token_age_days.max = pump_window_hours/24` and `market_cap_usd.min`, then re-filter locally on `token_age_hours`.
- Feed B: smart money DEX trades where the bought token is age 0 days; keep tokens with ≥ `min_wallets_for_signal` distinct smart wallets; confirm age and market cap via the screener or token-information.
- Merge and dedupe by `(chain, token_address)`; record which feed(s) found it and when.
- **Accept:** unit tests for merge/dedupe and for the age re-filter (e.g. a 4.5h-old token is excluded; a token with a missing age is excluded and logged).

### Phase 3 — Bundle analysis
- Pull early buys: `tgm/dex-trades` from deployment to `early_window_minutes` (or `max_early_buys`), ascending.
- Heuristics, each producing clusters of wallets with a reason:
  - **Same second:** ≥ `same_second_min_wallets` distinct buyers in the same `block_timestamp` second (or same slot, if the API exposes it).
  - **Common funder:** group early buyers by first-degree funder from related-wallets; groups ≥ `common_funder_min_wallets` form a cluster. Funders whose label matches `ignore_funder_labels` are ignored.
  - **Similar size:** within a candidate cluster, buy sizes with coefficient of variation ≤ `similar_size_max_cv` strengthen the cluster's confidence.
  - **Deployer link:** flag if the deployer (when known) funded or is in a cluster.
- Union overlapping clusters. Compute `bundle_supply_pct` (current balance of all cluster wallets / supply) and `bundle_status` (`holding` / `distributing` / `exited`, from the share of peak holdings sold).
- **Accept:** fixture factories in `tests/factories.py` for: an organic launch (no cluster), an obvious bundle (one funder → 12 wallets, same second), a CEX-funded false positive (must NOT be flagged), a partial bundle that is distributing, and a deployer-linked bundle. Each has a test with an exact expected result.

### Phase 4 — Smart money analysis
- For each candidate: distinct smart wallets, label breakdown, weighted participation, net USD flow (24h), holding ratio (tokens still held / tokens bought), time and market cap at first smart entry relative to launch.
- **Accept:** tests for accumulating, flipped (bought then sold), mixed labels, and zero-SM cases.

### Phase 5 — Cross-check and scoring
- `sm_in_bundle`: any smart wallet that is a member of a bundle cluster.
- Score 0–100 from: smart money participation (weighted), SM holding ratio, low `bundle_supply_pct`, bundle status, volume/liquidity ratio (fee proxy), holder growth. Weights come from config.
- Vetoes force AVOID: bundle supply above threshold, SM net selling, SM in bundle (if enabled).
- Verdict: GREEN ≥ `green_min`, WATCH ≥ `watch_min`, else AVOID. Always return a list of human-readable reasons (e.g. "3 funds accumulating, still holding 92%", "bundle of 14 wallets holds 38% of supply").
- Document the formula in `docs/scoring.md`.
- **Accept:** table-driven tests covering each quadrant (SM holding + low bundle → GREEN; SM holding + high bundle → AVOID via veto; SM selling + high bundle → AVOID; no SM + low bundle → WATCH) and each veto.

### Phase 6 — Storage, monitoring, alerts
- Tables: `tokens`, `snapshots` (score, verdict, metrics, reasons, timestamp), `clusters`, `cluster_wallets`, `credit_usage`, `alerts_sent`.
- Worker loop: discovery every `poll_seconds`; deep analysis for new candidates within the hourly budget; re-evaluate watchlist tokens (non-AVOID, under 24h old) on a slower cadence.
- Alerts on: new GREEN; any verdict downgrade; bundle status → `distributing`; SM net flow flipping to selling. Deduplicate so the same event doesn't alert twice. Telegram message includes chain, symbol, address, verdict, top 3 reasons, and a link to the dashboard page.
- Worker writes a heartbeat; `/healthz` reports unhealthy if the heartbeat is more than 5 minutes old.
- **Accept:** tests for transition detection and alert dedupe; the worker runs for 10 minutes in replay mode without errors.

### Phase 7 — Dashboard
- `/` live table: token, chain, age, market cap, verdict badge, score, bundle %, SM wallets, last updated; filter by verdict and chain; auto-refresh via HTMX every 30s.
- `/token/{chain}/{address}`: reasons, score history chart, bundle clusters with wallets and why they were clustered, smart money trades, links to Nansen and a block explorer.
- `/status`: credits used today vs budget, last poll time, worker health.
- Footer disclaimer: "Research tool, not financial advice."
- **Accept:** renders with replay data; usable on mobile width; no API key or internal error details ever shown in the page.

### Phase 8 — Backtest
- For launches in a past window (e.g. the last 14–30 days), rebuild what the pipeline would have seen at the moment each token first crossed $1M, using only data available up to that moment, using historical endpoints.
- Outcome metrics per token: max drawdown and price change at +6h, +24h, +72h; whether the bundle exited within 24h.
- Report (`backtest/report.py` → Markdown + a chart): outcomes by verdict; how often AVOID tokens dumped more than 50% vs GREEN tokens; the effect of each veto. Use it to tune `scoring.yaml`.
- Always print an estimated credit cost and ask for confirmation before running.
- **Accept:** there is no look-ahead (a test asserts that no data after the decision timestamp is used); the report is committed to `docs/backtest.md`.

### Phase 9 — Docker and deploy
See §9.

## 6. Testing strategy

- **Unit tests** (`tests/unit/`): pure pipeline logic against factory-built inputs. Fast, no network. The bulk of the tests.
- **Contract tests** (`tests/contract/`): parse every recorded fixture with the pydantic models. This catches Nansen schema drift. Re-record monthly or when a test fails.
- **Live smoke tests** (`tests/live/`, marker `@pytest.mark.live`, skipped by default): one call per endpoint with tiny pagination; run manually with `pytest -m live`, capped at ~50 credits total.
- **Worker soak test:** run the worker in replay mode for 10 minutes; assert no exceptions and a steady heartbeat.
- **Backtest as evaluation:** the signal's real test. Any scoring change must include before/after backtest numbers in its PR.
- **Coverage target:** ≥ 85% on `app/pipeline/`, ≥ 70% overall.

## 7. Git workflow

- `main` is protected: merge only via PR with green CI.
- Branch names: `phase-N-short-name`. Conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- PR description template: what changed, why, how it was tested, self-review checklist ticked, credits spent (if any).
- Initial repo creation (run by the human):
  ```bash
  git init && git add . && git commit -m "chore: initial spec"
  gh repo create lp-radar --private --source=. --push
  ```
  Keep the repo private during development and make it public for the submission.

## 8. Review checklist (walk through before every PR)

**Correctness**
- [ ] Field names and types match the verified docs in `docs/nansen-endpoints.md`
- [ ] All timestamps are UTC-aware; the age and pump-window math is correct at boundaries
- [ ] No look-ahead in the backtest
- [ ] Hot wallets (CEX, routers, bridges) are not treated as bundle funders

**Robustness**
- [ ] Every network call handles timeout, 429, 5xx and malformed responses
- [ ] Budget guard can't be bypassed; the cache is used where it should be
- [ ] Worker survives a failing token (logs it, moves on) and a Nansen outage (backs off)

**Security**
- [ ] No secrets in code, logs, fixtures, templates or commit history
- [ ] User-visible text is escaped (token names and symbols are attacker-controlled)
- [ ] Container runs as non-root; only ports 80/443 are exposed publicly

**Quality**
- [ ] Thresholds come from config, not literals
- [ ] Tests cover the new behaviour, including an edge case and a failure case
- [ ] Docs and README are updated

Claude Code: after self-review, also run a fresh review pass on the diff (e.g. a review subagent or `/review`) and fix what it finds before opening the PR.

## 9. Deployment (GCP Compute Engine + Docker)

### 9.1 Container
- `Dockerfile`: multi-stage, `python:3.12-slim`, non-root user, dependencies installed from a lockfile, `HEALTHCHECK` hitting `/healthz`.
- `docker-compose.yml` services, all from the same image:
  - `web`: `uvicorn app.main:app`, internal port 8000
  - `worker`: `python -m app.worker`
  - `caddy`: `caddy:2`, ports 80/443, mounts `deploy/Caddyfile`
  - Named volume `data` mounted at `/data` in web and worker (SQLite); `caddy_data` for certificates.
  - `restart: unless-stopped` on all services; the `.env` file is passed via `env_file`.
- `deploy/Caddyfile`: reverse proxy to `web:8000`; automatic HTTPS when `DOMAIN` is set; optional basic auth on `/status`.

### 9.2 One-time VM setup (human runs, Claude Code writes `docs/deploy.md` and `deploy/setup-vm.sh`)

```bash
# pick a region near you, e.g. europe-west8 (Milan) or europe-west3 (Frankfurt)
gcloud compute addresses create lp-radar-ip --region=europe-west8
gcloud compute instances create lp-radar \
  --zone=europe-west8-a --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB --tags=lp-radar-web \
  --address=lp-radar-ip
gcloud compute firewall-rules create lp-radar-web \
  --allow=tcp:80,tcp:443 --target-tags=lp-radar-web
gcloud compute ssh lp-radar --zone=europe-west8-a
```

On the VM, `deploy/setup-vm.sh` should: install Docker Engine and the compose plugin, create a `deploy` user in the `docker` group, create `/opt/lp-radar` containing the compose file, Caddyfile and `.env` (`chmod 600`), and enable unattended security upgrades. Point a domain's A record at the static IP if you want HTTPS.

### 9.3 Continuous deploy
- `.github/workflows/deploy.yml`, triggered on a push to `main` after CI passes:
  1. Build the image and push to GitHub Container Registry, tagged with the commit SHA and `latest`.
  2. SSH to the VM (secrets: `VM_HOST`, `VM_USER`, `VM_SSH_KEY`) and run `cd /opt/lp-radar && docker compose pull && docker compose up -d`.
  3. Poll `https://<domain>/healthz` until it is healthy; fail the job otherwise.
- Rollback: `IMAGE_TAG=<previous-sha> docker compose up -d`. Document this in `docs/deploy.md`.
- Backups: a daily cron on the VM copies the SQLite file (using `sqlite3 .backup`) to a GCS bucket.

**Accept (Phase 9):** a fresh VM goes from nothing to a live dashboard by following `docs/deploy.md` alone; a merge to `main` deploys automatically; killing the worker container results in an automatic restart and `/healthz` recovering.

## 10. Buildathon submission checklist
- [ ] README: problem, how it works (diagram), which Nansen endpoints are used and why, screenshots, how to run it
- [ ] Backtest results in `docs/backtest.md` with a headline number
- [ ] Short demo video: a live token going from discovery to verdict to alert
- [ ] Nansen's API terms checked for showing their data on a public site; if unclear, put the dashboard behind a login and show aggregates publicly
- [ ] Repo public, license chosen, secrets rotated if one was ever exposed
