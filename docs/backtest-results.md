# Backtest results

Generated 2026-09-26 13:30 UTC by `python -m backtest.run report`. 135 tokens analysed, 514 skipped.

## Headline

Dead or collapsed by +72h: AVOID 63% (52/83, 95% CI 52-72%); GREEN 44% (4/9, 95% CI 19-73%).

AVOID tokens fell more than 50% within 24h in 47% of cases (41/87), against 44% for GREEN (4/9).

![Dump rate by verdict](backtest-results.svg)

## Pre-registered checks on fresh tokens

These four checks were written down, with their predicted direction, before the fresh tokens were fetched, and the scoring was left unchanged. H1 and H2 came from looking at the first 83 tokens, so they are hypotheses being tested here on tokens they did not come from. H3 and H4 ask whether the current verdicts and vetoes do anything. Two-sided Fisher exact test; with 4 checks the bar is p < 0.0125. The outcome is dead or collapsed by +72h, using the volume threshold calibrated on the first tokens only (1.6%).

Fresh tokens analysed: 52; with a complete 72-hour window: 52.

| Check | Predicted worse group | Comparison group | p | Result |
|---|---|---|---|---|
| H1 smart money | no smart wallets: 22/27 (81%) | 3+ smart wallets: 3/11 (27%) | 0.003 | supported |
| H2 bundle status | bundle holding: 16/20 (80%) | bundle distributing: 6/15 (40%) | 0.032 | not supported |
| H3 verdicts separate | AVOID: 21/32 (66%) | GREEN or WATCH: 9/20 (45%) | 0.162 | not supported |
| H4 vetoes work | a veto fired: 5/13 (38%) | no veto: 25/39 (64%) | 0.121 | opposite direction |

## Lifecycle at +72h

Price alone is a poor test of a volatile new token: many fall by half and come back, and some keep trading at a low price. Each token is followed for 72 hours from the decision and put in one class:

- **dead**: volume in hours 48-72 is below the threshold below (share of the volume in the first 24 hours)
- **collapsed**: the price at +72h is at least 50% below the decision price, and volume is still there
- **recovered**: the price fell at least 50% at some point but ended above that level
- **held**: it never fell that far

### Volume threshold, taken from the data

Tokens are labelled by price only: *rugged* = at least 90% below the decision price at +72h, *survivor* = within 25% of it or above. Volume share = volume in hours 48-72 / volume in the first 24 hours.

- Rugged (39): median 0.0% (range 0.0-2.5%)
- Survivors (64): median 15.6% (range 0.0-287.0%)
- **Threshold: a volume share below 1.6% is called dead.** Balanced accuracy on these tokens: 85%; leave-one-out (each token judged by a threshold that did not use it): 83% over 103 tokens.

### By verdict

|  | Tokens | Dead | Collapsed | Recovered | Held | Dead or collapsed | Fell 50%+ at any time |
|---|---|---|---|---|---|---|---|
| GREEN | 9 | 1 | 3 | 2 | 3 | 44% (4/9, 95% CI 19-73%) | 6/9 |
| WATCH | 38 | 18 | 5 | 6 | 9 | 61% (23/38, 95% CI 45-74%) | 27/38 |
| AVOID | 83 | 44 | 8 | 8 | 23 | 63% (52/83, 95% CI 52-72%) | 48/83 |

### Clean tokens against the rest

Clean = no veto, bundle holding under 5% of supply, at least 2 smart-money wallets, and no net selling by them.

|  | Tokens | Dead | Collapsed | Recovered | Held | Dead or collapsed | Fell 50%+ at any time |
|---|---|---|---|---|---|---|---|
| Clean | 18 | 3 | 6 | 3 | 6 | 50% (9/18, 95% CI 29-71%) | 12/18 |
| Not clean | 112 | 60 | 10 | 13 | 29 | 62% (70/112, 95% CI 53-71%) | 69/112 |

### How much the threshold depends on the labels

| Rugged / survivor cut-offs | Tokens (rugged / survivors) | Threshold |
|---|---|---|
| 80% / 0% | 48 / 48 | 6.6% |
| 80% / 25% | 48 / 64 | 6.4% |
| 80% / 50% | 48 / 69 | 1.8% |
| 90% / 0% | 39 / 48 | 1.6% |
| 90% / 25% | 39 / 64 | 1.6% |
| 90% / 50% | 39 / 69 | 1.6% |
| 95% / 0% | 32 / 48 | 1.5% |
| 95% / 25% | 32 / 64 | 1.5% |
| 95% / 50% | 32 / 69 | 1.5% |

## Outcomes by verdict

"Dumped" means the price fell more than 50% below the price at the decision time within the horizon. Only tokens whose data reaches the horizon are counted.

| Verdict | Tokens | Dumped within 6h | Dumped within 24h | Dumped within 72h | Median max drawdown 24h | Median change +24h | Bundle exited within 24h |
|---|---|---|---|---|---|---|---|
| GREEN | 9 | 33% (3/9, 95% CI 12-65%) | 44% (4/9, 95% CI 19-73%) | 67% (6/9, 95% CI 35-88%) | -49.3% | -41.4% | n/a |
| WATCH | 39 | 23% (9/39, 95% CI 13-38%) | 62% (24/39, 95% CI 46-75%) | 71% (27/38, 95% CI 55-83%) | -76.8% | -58.0% | 0/1 |
| AVOID | 87 | 33% (29/87, 95% CI 24-44%) | 47% (41/87, 95% CI 37-58%) | 58% (48/83, 95% CI 47-68%) | -46.9% | -23.2% | 4/12 |

### Median price change after the decision

| Verdict | +6h | +24h | +72h |
|---|---|---|---|
| GREEN | +11.9% | -41.4% | -48.4% |
| WATCH | +26.9% | -58.0% | -62.1% |
| AVOID | +18.6% | -23.2% | -16.8% |

## Effect of each veto

Dump rate within 24h for tokens that triggered a veto against those that did not.

| Veto | Tokens | With the veto | Without it |
|---|---|---|---|
| `bundle_supply` | 10 | 60% (6/10, 95% CI 31-83%) | 50% (63/125, 95% CI 42-59%) |
| `sm_in_bundle` | 9 | 33% (3/9, 95% CI 12-65%) | 52% (66/126, 95% CI 44-61%) |
| `sm_net_selling` | 18 | 56% (10/18, 95% CI 34-75%) | 50% (59/117, 95% CI 41-59%) |

## Coverage

- Tokens analysed: 135
- Skipped, `never_reached_market_cap`: 463
- Skipped, `trades_truncated`: 51
- Rows stamped after a decision time that were dropped before analysis: 0
- Credits spent on the analysed tokens: 10083

## Method and limits

- Candidates: young, liquid tokens from the historical screener, not filtered on market cap (that would hide tokens that pumped and then collapsed).
- Decision time: the close of the first 5-minute candle at or above the market cap threshold within the pump window. Only data up to that moment reaches the analysis.
- Reduced-input score: holder count and liquidity do not exist point-in-time, so those two components are left out and the remaining weights are re-scaled. Verdict thresholds are unchanged.
- Balances are rebuilt from trades between launch and the decision; a wallet with no trades in that window counts as holding nothing.
- Funder lookups use the current related-wallets endpoint; a funding relationship is a permanent on-chain fact, so this does not look ahead.
- All historical endpoints are beta and may be restated. The sample is small and the intervals are wide: treat this as evidence for tuning `scoring.yaml`, not proof.
