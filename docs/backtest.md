# Backtest

**Status: not yet run.** This file holds the method. Once a funded run has been approved and
executed, `python -m backtest.run report` overwrites it with the real numbers, the headline result
and the chart (`docs/backtest.svg`). No number in this repository is invented before then.

## Question

When the pipeline says AVOID, do tokens really dump more often than when it says GREEN?
"Dump" means the price fell more than 50% below the decision price at any point within the
horizon (+6h, +24h, +72h; configurable in `config/scoring.yaml` under `backtest:`).

## Method

1. **Candidates.** For each of the last 14 days (ending 3 days ago, so +72h has elapsed) and each
   chain the historical screener lists young (`token_age_days <= 1`), liquid (volume >= $300k)
   tokens, sorted by volume. There is deliberately **no market-cap filter**: one measured at the end
   of the day would drop tokens that pumped and then collapsed, and dumps would look rarer than
   they are. A token seen on two days keeps its earliest day.
2. **Decision time.** The close of the first 5-minute candle whose market cap is at least $1M, no
   later than 4 hours after launch. Tokens that never get there are skipped, and counted.
3. **Analysis at the decision time.** The same code as production (bundle detection, smart-money
   metrics, scoring), fed only data up to the decision time.
4. **Outcome.** Max drawdown from the decision price and price change at each horizon, from the
   candles after the decision; and how much of the bundle's position was sold within 24h.
5. **Report.** Dump rate per verdict with 95% Wilson intervals, veto effects, coverage.

## No look-ahead

- Every request used for the analysis has `date_range.to` no later than the decision time
  (`assert_range_ends_by`, raises `LookAheadError`).
- Rows the API returns stamped after the decision are dropped and counted (`keep_until`); the
  count is reported.
- Only the outcome computation reads later data, through separate calls.
- Tests plant post-decision rows and assert the verdict is identical with and without them.

## Known limits (stated, not hidden)

- **Reduced inputs.** Historical volume/liquidity and holder growth are not reconstructable at the
  decision time, so those two components are excluded and the remaining weights re-normalised. The
  backtest therefore evaluates the bundle and smart-money parts of the score, not the live score.
- **Balances from trades.** Holdings are rebuilt as buys minus sells inside the analysed window
  (ownership = net tokens / total supply). Transfers and pre-window holdings are invisible.
- **Selection.** Only tokens the screener lists that day and that reached $1M market cap.
- **Smart-money labels** are the historical label set (Fund, Smart Trader, 90D/180D Smart Trader),
  which is not identical to the current labels.
- **Supply is implied, not fetched:** total supply is the decision candle's market cap divided by
  its price. This is point-in-time and costs nothing, but it inherits whatever supply definition
  Nansen used for the market cap. The funder lookups (`related-wallets`, first funder only, which
  pre-dates the first buy) are the only inputs not stamped in time.
- **Label tiers are fetched with `tier_pages` pages each**; a very busy token can have smart-money
  trades beyond that, which understates smart money (it never overstates it).
- A trade stamped exactly at the decision time is used by the analysis and also starts the exit
  window; at second resolution this affects at most a handful of rows.
- Small samples give wide intervals; the report shows them.

## Running it

Every paid command prints an estimate and asks for confirmation. Responses are cached under
`backtest/cache/` (git-ignored), so repeating a command costs nothing.

```bash
python -m backtest.run estimate --limit 15                 # spends nothing
NANSEN_MODE=live python -m backtest.run discover --max-credits 300
NANSEN_MODE=live python -m backtest.run analyze --limit 3 --max-credits 500     # smoke
NANSEN_MODE=live python -m backtest.run analyze --limit 25 --max-credits 2500   # pilot
NANSEN_MODE=live python -m backtest.run analyze --limit 100 --max-credits 12000 # full
python -m backtest.run report
```

Stop after `discover` and check the candidate count before spending on `analyze`.
Analysis costs about 80-130 credits per token (worst case is printed by `estimate`).
To re-score after editing `scoring.yaml` at no cost, delete `backtest/cache/results.jsonl` and
run `analyze` again: the cached responses are reused as long as the requests are unchanged.
