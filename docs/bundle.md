# Bundle analysis (Phase 3)

`app/pipeline/bundle.py` finds groups of early buyers that look coordinated and measures how much of
the supply they still hold. All thresholds come from the `bundle:` section of `config/scoring.yaml`.

## Inputs (one token)
1. `tgm/dex-trades` (1 credit): BUY trades from deployment to `early_window_minutes`, ascending, at
   most `max_early_buys`. The API requires a `date` range, so the caller must supply the deployment
   time (`Candidate.token_deployment_date`). The API exposes no slot number, only `block_timestamp`.
2. `profiler/address/related-wallets` (1 credit each, cached 24h): the funder of the earliest
   `max_funder_lookups` unique buyers (default 30). The funder is the row whose `relation` matches
   `funder_relations` (default `First Funder`, case-insensitive), lowest `order` first. A failed lookup
   is logged and treated as "funder unknown"; a `BudgetExceeded` is re-raised after all lookups finish.
3. `tgm/holders` (5 credits per 100 wallets): balance and `ownership_percentage` of the clustered
   wallets only, via `filters.address` and `value_usd.min = 0`. Skipped when there is no cluster.

Typical cost is about 36 credits per token (1 + up to 30 + 5).

## Heuristics
- **same_second**: at least `same_second_min_wallets` distinct buyers in one whole `block_timestamp` second.
- **common_funder**: at least `common_funder_min_wallets` looked-up buyers share a funder. Funders whose
  label contains any `ignore_funder_labels` entry (case-insensitive substring) are ignored, so exchange
  hot wallets are not treated as bundlers. Real Nansen labels are names such as "Binance 14", so this
  list should be extended once real labels have been recorded.
- **similar_size**: the coefficient of variation of the cluster's per-wallet buy USD is at most
  `similar_size_max_cv`. It only marks a cluster; it never creates one.
- **deployer_link**: the deployer is a clustered wallet, a cluster funder, or the funder of a clustered
  wallet. Nansen exposes no deployer address, so production callers currently pass `None` and this
  stays dormant until a source exists.
- Clusters that share any wallet are merged (reasons and funders combined).

## Outputs
`BundleAnalysis`: clusters, `bundled_wallets`, `supply_pct` (sum of `ownership_percentage`, assumed to be
0-100), `sold_pct` and `status` (`none`, `holding`, `distributing`, `exited`).
`sold_pct = 1 - current tokens / tokens bought in the early window`, clamped to 0-100, then compared with
`distributing_sold_pct` and `exited_sold_pct`. A cluster wallet missing from the holders response counts
as holding 0.

## Known limitations (verify with the first live recording)
- The `relation` strings are only documented for Ethereum; the Solana values are unverified.
- `ownership_percentage` is assumed to be a 0-100 percentage, not a fraction.
- Wallets are matched on exact address strings. If Nansen returns EVM addresses in different casing
  between endpoints, a live bundle would read as `exited`. Solana is case-sensitive, so any fix must be
  chain-aware.
- "Sold" is inferred from balances. Tokens moved to a wallet outside the cluster look like sales.
- Only the earliest `max_funder_lookups` buyers are checked for a common funder.
