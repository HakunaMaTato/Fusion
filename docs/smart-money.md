# Smart money analysis (Phase 4)

`app/pipeline/smart_money.py` turns a token's smart-money trades into `SmartMoneyMetrics`. It only
measures; verdicts, vetoes and weights belong to Phase 5. Thresholds come from the `smart_money:`
section of `config/scoring.yaml`.

## Inputs (about 7 credits per token)
1. `tgm/dex-trades` with `only_smart_money: true` (1 credit per page): both BUYs and SELLs from
   deployment (or the flow window if the deployment time is unknown) to now, ascending. Pages are read
   until the last page, at most 5. Ascending order means a capped read drops the newest trades and biases
   the result toward "still holding", so hitting the cap logs a warning.
2. One filtered `tgm/dex-trades` call per tier in `label_weights` (5 credits, skipped when nobody
   bought): `only_smart_money`, `action: BUY` and `include_smart_money_labels: [tier]`, first page only.
   The wallets a call returns belong to that tier. A failed tier call is logged and that tier is ignored.
3. `tgm/token-information` (1 credit, always): `circulating_supply` for the market cap at first entry and
   `total_holders` for holder velocity in scoring. A failed call leaves those fields empty.

This replaces `smart-money/dex-trades` (5 credits, trailing 24h only, one side of a trade per call).

## Metrics
- `wallet_count`: distinct smart wallets that bought. `signal` is true at `min_wallets_for_signal` or more.
- `label_counts` and `weighted_score`: each buyer gets its best label. A trade label matches the
  `label_weights` key it contains (case-insensitive, longest key first, so "180D Smart Trader" beats
  "Smart Trader"); across a wallet's trades the highest weight wins. No match means `other` with
  `default_label_weight`.
- `net_flow_usd` and `net_selling`: buy USD minus sell USD in the last `flow_window_hours` (24).
- `holding_ratio`: `max(bought - sold, 0) / bought` over all trades (`None` without buys), and
  `wallets_still_holding`: buyers with a positive net token position.
- `first_entry_at`, `minutes_after_launch`, `market_cap_at_entry`: the first smart BUY, its delay after
  deployment, and its price (`estimated_value_usd / token_amount`) times circulating supply. Any missing
  input leaves the field `None`; nothing is guessed.

## Known limitations
- **Verified live (2026-09-24, Solana): `trader_address_label` comes back as an empty string for the
  trades when `only_smart_money` is true and does not name the tier**, so tier membership is taken from
  the per-tier calls above, not from the rows.
- **Tier filtering (live test, one Solana token with 21 smart wallets, 8 credits):** each tier call
  returned a subset of the unfiltered wallets (180D 7, 90D 10, Smart Trader 14, 30D 9, Fund 0). All 21
  wallets fell into at least one tier and 10 were in several, so tiers overlap and a wallet takes the
  highest weight among its tiers.
- The same test showed trader labels of the form "<TOKEN> Token Deployer". If the deployer trades its own
  token early, that label could feed the dormant `deployer_link` heuristic in `docs/bundle.md`.
- The holding ratio comes from DEX trades only, so tokens moved without a trade are invisible.
- Market cap at entry uses today's circulating supply, which can differ from the supply at entry.
- Smart-money wallets are matched by exact address string. Checked live on Solana (holders returned the
  requested addresses unchanged); EVM casing is still unverified.
