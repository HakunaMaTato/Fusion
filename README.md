# LP Radar

LP Radar finds freshly launched tokens that are pumping, checks whether their
early buying was bundled, checks what Nansen Smart Money is doing with them,
and rates each token as an LP candidate (GREEN / WATCH / AVOID). It's a
submission for the Nansen Meridian Buildathon.

Read-only: it never holds keys, signs transactions, or trades.

See [SPEC.md](SPEC.md) for the full build plan and [CLAUDE.md](CLAUDE.md) for
project conventions. This README will grow into the submission writeup
(problem, architecture, endpoints used, screenshots, how to run it) as the
phases land.

## Quickstart

```bash
make install     # create venv, install deps
make check       # ruff + mypy + pytest (replay mode)
make run         # web app locally, replay mode
make worker      # polling worker (writes the SQLite database the web app reads)
make soak        # ten real minutes of the worker against a scripted Nansen (slow)
```

Dashboard: `/` (tokens), `/token/{chain}/{address}`, `/status`; see `docs/dashboard.md` (demo data: `python scripts/seed_demo.py <db-url>`).

Backtest (spends Nansen credits; staged, capped and asked for confirmation; see `docs/backtest.md`):

```bash
python -m backtest.run estimate --limit 15
NANSEN_MODE=live python -m backtest.run discover --max-credits 300
NANSEN_MODE=live python -m backtest.run analyze --limit 3 --max-credits 500
python -m backtest.run report
```

`/healthz` reports 503 when the worker heartbeat is stale. See `docs/worker.md` for the loop, credit pacing and alerts.
