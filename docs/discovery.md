# Discovery (Phase 2)

`app/pipeline/discovery.py` produces `Candidate`s (fresh tokens worth a deeper look) from two feeds
and merges them by `(chain, token_address)`. Every threshold comes from `config/scoring.yaml`.

## Feed A: screener (pump feed)
One `token-screener` call (1 credit) filtered on `token_age_days.max = pump_window_hours / 24` and
`market_cap_usd.min = min_market_cap_usd`. The API only filters on fractional days, so the result
is re-filtered locally on `token_age_hours <= pump_window_hours` (inclusive: 4.0h is kept, 4.5h is
dropped). A row with a missing age or market cap is excluded and logged as a warning.

## Feed B: smart money
1. `smart-money/dex-trades` (5 credits) filtered to `token_bought_age_days <= 0`. Distinct
   `trader_address` values are counted per `(chain, token)`; tokens with fewer than
   `smart_money.min_wallets_for_signal` wallets are dropped.
2. One batched `token-screener` call per 100 tokens (`filters.token_address` is a list, 1 credit)
   confirms age and market cap. Here the age cap is `max_age_hours` (24h), not the pump window, and
   there is no minimum market cap, only that it is known. Tokens the screener does not return, or
   returns without age or market cap, are dropped and logged.

## Cost and cadence
Feed A costs 1 credit per poll (`poll_seconds`, default 120: about 720 credits/day). Feed B costs
5 credits plus 1 per confirm call per poll, so it has its own slower cadence,
`smart_money_poll_seconds` (default 900: about 580 credits/day). Together they fit inside the default
`DAILY_CREDIT_BUDGET=3000`. The scheduling itself is Phase 6; this phase only adds the config keys.

## Known limitations
- Only the first page of each feed is read (100 screener rows, 1000 trades). Truncation logs a warning.
- The confirm call uses the `screener_timeframe` window, so a token with no trades in that window may
  not be returned and is dropped as "not confirmed".
- Feed B matches trade and screener rows on exact `(chain, address)` strings. If Nansen returns EVM
  addresses in different casing between the two endpoints, those tokens would be dropped. Verify
  when the first live fixtures are recorded; the fix must be chain-aware (Solana is case-sensitive).
