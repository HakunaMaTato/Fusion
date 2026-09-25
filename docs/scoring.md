# Scoring (Phase 5)

`app/pipeline/scoring.py` turns the discovery, bundle and smart-money results into a score, a verdict
and human-readable reasons. `app/pipeline/crosscheck.py` supplies one input: the smart wallets that
are also inside a bundle cluster. Everything below is configured in the `scoring:` section of
`config/scoring.yaml`; the shipped values are starting points to tune with the Phase 8 backtest.

## Score (0-100)
A weighted average of six components, each 0 to 1 before weighting:

| Component | Value | Weight |
|---|---|---|
| `sm_participation` | `min(weighted_score / full_credit_weighted_score, 1)` | 25 |
| `sm_holding` | smart-money `holding_ratio` (0 if unknown) | 15 |
| `bundle_supply` | `max(0, 1 - supply_pct / veto_bundle_supply_pct)` | 20 |
| `bundle_status` | `status_scores[status]`: none 1.0, holding 0.4, exited 0.3, distributing 0.0 | 10 |
| `volume_liquidity` | `min((volume / liquidity) / full_credit_volume_liquidity_ratio, 1)` (fee proxy) | 15 |
| `holder_growth` | `min((total_holders / max(age_hours, 1)) / full_credit_holders_per_hour, 1)` | 15 |

`score = 100 * sum(weight * component) / sum(weights)`. A component whose input is missing (no
liquidity, no holder count or age) scores 0 and adds a reason such as "holder count or token age
unavailable"; nothing is guessed. `holder_growth` is holders per hour since launch, because a single
snapshot has no history; a true growth rate needs the stored snapshots from Phase 6.

With the shipped weights a token with no smart money scores at most 60, so it can be WATCH but never GREEN.

## Vetoes (force AVOID)
- `bundle_supply`: `supply_pct` is strictly above `veto_bundle_supply_pct` (30). Always on.
- `sm_net_selling`: smart-money net flow over the last 24h is negative. Applies even when the
  smart wallet only sold and never bought (usually a bundler). Switch with `veto_sm_net_selling`.
- `sm_in_bundle`: a smart wallet is a member of a bundle cluster. Switch with `veto_sm_in_bundle`.

## Verdict
Any veto gives AVOID. Otherwise GREEN at `green_min` (70) or above, WATCH at `watch_min` (40) or above,
else AVOID. Boundaries are inclusive.

## Reasons
Vetoes first, then the smart-money summary, the bundle summary, volume/liquidity and holders, for
example "3 smart wallets (weighted 3.0), still holding 95%" or
"bundle of 14 wallets holds 38% of supply (veto above 30%)".

## Config validation
`scoring.yaml` is validated at load time: `veto_bundle_supply_pct` and every `full_credit_*` value must
be above 0, `status_scores` must define all four statuses, and the weights must not all be 0. A bad file
fails at startup and not with a division error in the worker.

## Known limitations
- The cross-check only sees smart wallets among the earliest buyers (the bundle clusters are built from
  at most `max_early_buys`), so a smart wallet that bought later can never be flagged as in a bundle.
- `weighted_score` currently equals the smart-wallet count because the smart-money labels are empty (see
  `docs/smart-money.md`); the formula improves on its own once tier labels are available.
