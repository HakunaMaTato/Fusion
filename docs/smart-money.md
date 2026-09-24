# Smart money analysis (Phase 4)

`app/pipeline/smart_money.py` turns a token's smart-money trades into `SmartMoneyMetrics`. It only
measures; verdicts, vetoes and weights belong to Phase 5. Thresholds come from the `smart_money:`
section of `config/scoring.yaml`.

## Inputs (about 2 credits per token)
1. `tgm/dex-trades` with `only_smart_money: true` (1 credit per page): both BUYs and SELLs from
   deployment (or the flow window if the deployment time is unknown) to now, ascending. Pages are read
   until the last page, at most 5. Ascending order means a capped read drops the newest trades and biases
   the result toward "still holding", so hitting the cap logs a warning.
2. `tgm/token-information` (1 credit, skipped when nobody bought): `circulating_supply`, only used for the
   market cap at first entry. A failed call is logged and leaves that one field empty.

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
- The format of `trader_address_label` is not documented; matching is by substring on the configured keys.
  Check it on the first live recording.
- The holding ratio comes from DEX trades only, so tokens moved without a trade are invisible.
- Market cap at entry uses today's circulating supply, which can differ from the supply at entry.
- Smart-money wallets are matched by exact address string (same EVM-casing caveat as `docs/bundle.md`).
