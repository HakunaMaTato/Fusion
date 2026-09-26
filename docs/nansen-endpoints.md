# Nansen API — verified endpoints

Verified 2026-09-24 against `https://docs.nansen.ai` (live docs, `.md` pages, embedded OpenAPI
3.1 fragments). Base URL `https://api.nansen.ai`. All endpoints are `POST` with JSON bodies,
authenticated with a case-insensitive `apikey` header.

**The API has been restructured since SPEC.md §2 was drafted.** Paths mostly still match, but
field names, response shapes and one very recent rename (see below) do not always match SPEC's
guesses. This file is the source of truth; SPEC.md §2 is direction, not a contract.

## Cross-cutting behaviour (verified from `getting-started/*` and every endpoint's OpenAPI fragment)

- **Auth**: `apikey: <key>` header. 401 on missing/invalid key, 403 when the key lacks access,
  402 for endpoints that also accept x402/MPP agentic payments (none of ours do by default).
- **Credit headers** on every response: `X-Nansen-Credits-Cost` (quoted cost), `X-Nansen-Credits-Used`
  (actually deducted — this is the one SPEC.md rule 4 says to persist), `X-Nansen-Credits-Remaining`.
  Matches SPEC's assumption.
- **Rate limits**: per API key. Free: 15 req/s, 300 req/min. Pro: 75 req/s, 1,500 req/min. Plus
  a handful of per-endpoint minute limits (none of our 8 endpoints are in that list as of this
  check). Headers: `RateLimit-{Limit,Remaining,Reset}` and `X-RateLimit-{Limit,Remaining,Reset}`
  (the latter is the most-constrained app-enforced window; self-throttle on the smaller of the
  two). `429` responses carry `Retry-After` (seconds) and `X-Nansen-RateLimit-Scope`
  (`second` | `minute` | `endpoint`, open set — handle unknown values generically).
- **Errors**: structured JSON envelope `{error, message, code, status, request_id, doc_url,
  param?, retry_after?}`. `code` is a stable machine-readable enum (`missing_field`,
  `rate_limit_exceeded`, `insufficient_credits`, `internal_error`, `upstream_unavailable`,
  `query_timeout`, ...). Docs say the code set may grow — clients must fall back to HTTP status
  for unknown codes. Retryable per docs: `429`, `502/503/504` (`upstream_unavailable`), and
  `internal_error` (500) — our client retries 429 and 5xx with backoff, honoring `Retry-After`.
- **Credits & plan**: see `getting-started/credits.md` for the full per-endpoint cost table.
  Costs relevant to us: `token-screener`=1, `smart-money/dex-trades`=5, `tgm/dex-trades`=1,
  `profiler/address/related-wallets`=1, `tgm/holders`=5 (150 if `premium_labels=true` — we never
  set this), `profiler/address/current-balance`=1, `tgm/token-information`=1,
  `tgm/flow-intelligence`=1.
- **`wallet_address` rename (changelog 2026-09-24, today)**: `profiler/address/related-wallets`
  now takes `wallet_address` (the deprecated `address` alias still works but we use the new
  name). This rename has **not** propagated to `profiler/address/current-balance`, which still
  uses `address` — verified directly on that endpoint's page, not assumed. Endpoints are not
  uniform; each was checked individually.

## Endpoints (Phase 1 scope — the 8 non-beta endpoints)

| Purpose | Verified path | Request (required fields) | Response shape | Notes vs SPEC.md |
|---|---|---|---|---|
| Discovery: fresh pumps | `POST /api/v1/token-screener` | `chains` (1-5 of an enum incl. `solana`/`base`/`bnb`); `timeframe` (`5m`..`30d`) or deprecated `date`; `filters.token_age_days`/`filters.market_cap_usd` are `{min,max}` objects, not bare numbers | `data[]` (only `chain`/`token_address`/`token_symbol` required; rest incl. `token_age_hours`, `token_deployment_date`, `market_cap_usd`, `liquidity`, `volume` optional-but-present), `pagination` | Matches SPEC closely. Path unchanged. |
| Discovery + SM analysis | `POST /api/v1/smart-money/dex-trades` | `chains` (array, `"all"` allowed) | `data[]` with required `token_bought_age_days`/`token_sold_age_days` as **int** (day count, confirmed), optional `token_bought_market_cap` etc. | Matches SPEC. Trailing 24h only, no date param — confirmed. |
| Early trades for bundle detection | `POST /api/v1/tgm/dex-trades` | `chain`, `token_address`, **`date` is required** (`{from,to}`) | `data[]`: `block_timestamp`, `transaction_hash`, `trader_address`, `action` (`BUY`\|`SELL`), amounts — **no slot/block-number field exists**, only `block_timestamp` (second resolution) | `date` being required (not just a filter) means Phase 3 must always pass `{from: deployment_time, to: deployment_time + early_window}`. No slot number — same-second heuristic can only use timestamp seconds, as SPEC anticipated as a fallback. |
| Funder clustering | `POST /api/v1/profiler/address/related-wallets` | `wallet_address` (renamed from `address` today — deprecated alias still accepted), `chain` | `data[]`: `address`, `relation` (free-text string, not an enum), `transaction_hash`, `block_timestamp`, `order`, `chain` | Field rename confirmed. `relation`'s possible string values are not enumerated in the schema — Phase 3 will need to inspect real values before branching on "funded by" semantics. 1 credit/call — cache 24h as SPEC says. |
| Current balances | `POST /api/v1/tgm/holders`, `POST /api/v1/profiler/address/current-balance` | holders: `chain`, `token_address` (`premium_labels` defaults `false` now — was previously `true` by default per an earlier changelog entry, now flipped); current-balance: `chain`, plus `address` **or** `entity_name` | holders: `TGMHolder` has **no required fields at all** (every field nullable) — confirms SPEC's "holders returns top holders only" caveat; current-balance: `ProfilerBalance` requires `chain`,`address`,`token_address`,`token_symbol` | current-balance still uses `address`, NOT `wallet_address` — do not assume the rename is global. |
| Supply / token stats | `POST /api/v1/tgm/token-information` | `chain`, `token_address`, `timeframe` (all required) | `data.token_details.{circulating_supply,total_supply}`, `data.spot_metrics.total_holders` — **no deployer address field exists in this schema** | SPEC hoped for a deployer field; it isn't there. Deployer detection (Phase 3's "deployer link" heuristic) will need another source (e.g. first entry in `related-wallets`/`tgm/dex-trades` ordering) — flag for Phase 3 planning. |
| Segment flows | `POST /api/v1/tgm/flow-intelligence` | `chain`, `token_address` (`timeframe` optional, defaults `1d`) | `data[]` (list, not a single object) of segment metrics: `public_figure_*`, `top_pnl_*`, `whale_*`, `smart_trader_*`, `exchange_*`, `fresh_wallets_*` — `exchange_wallet_count` is documented as **always 0** (not tracked), same for `fresh_wallets_wallet_count` | `smart_trader_*` is the closest match to SPEC's "Smart Money" segment. Two count fields are permanently 0 per the docs themselves — don't treat as a data bug. |

## Historical endpoints (Phase 8, verified against the live docs on 2026-09-24 and 2026-09-26)

All three live under `/api/v1beta1/` and cost **5 credits per call** (5x the current-data
equivalent). The dedicated top-holders history endpoint costs 25 and is **not used**: balances are
rebuilt from trades instead (see `docs/backtest.md`).

| Purpose | Path | Request | Response notes |
|---|---|---|---|
| Candidates per day | `POST /api/v1beta1/token-screener/historical` | `to_date`, `timeframe_days` (1-365), `chains` (solana, base, bnb, ethereum...), `filters.volume_usd`, `filters.token_age_days` (integers), `order_by` (`volume`) | `data[]` with `token_address`, `token_symbol`, `chain`, `volume`, `market_cap_usd`... |
| Trades in a window | `POST /api/v1beta1/tgm/historical-dex-trades` | `chain`, `token_address`, `date_range {from,to}`, `filters.include_labels` (historical label names such as `Fund`, `Smart Trader`, `90D Smart Trader`) | rows carry **no token address** (the request already fixes it); `block_timestamp` has second resolution |
| Price and market cap candles | `POST /api/v1beta1/tgm/historical-token-ohlcv` | `chain`, `token_address`, `timeframe` (`5m`..`1w`), `date_from`, `as_of_date` | `data[]`: `interval_start`, OHLC, `volume_usd`, `market_cap {open,high,low,close}`; `truncated` flag |

Chain coverage checked live on 2026-09-26: `robinhood` works on the current endpoints (screener, tgm dex-trades, holders, smart-money dex-trades, token-information, related-wallets) but is rejected by historical OHLCV and historical trades (valid there: base, bnb, ethereum, solana; OHLCV also hyperliquid), so it cannot be backtested. Nansen returns BSC tokens as `bsc` while requests say `bnb`.

Fixtures for these are hand-authored from the documented schemas, like the rest.

## Fixtures

`tests/fixtures/<endpoint-without-/api/v1/prefix>/<sha256-of-sorted-json-body>[:16].json` — hand
-authored from each endpoint's documented example schema (field names/types/nullability exactly
as verified above), **not** live-recorded, so no credits were spent verifying this phase.
`scripts/record_fixtures.py` exists to replace these with real recordings later, gated on
confirmation and an estimated credit cost per rule 3.
