# CLAUDE.md — LP Radar

LP Radar finds freshly launched tokens that are pumping, checks whether their early buying was bundled, checks what Nansen Smart Money is doing with them, and rates each token as an LP candidate (GREEN / WATCH / AVOID). It is a submission for the Nansen Meridian Buildathon.

The full build plan, acceptance criteria and test plan live in `SPEC.md`. Read it before starting any phase. This file holds the rules that apply to every task.

## Ground rules

1. **Read-only product.** This app never holds private keys, never signs transactions, never trades or moves liquidity. If a task seems to require that, stop and ask.
2. **Never commit secrets.** API keys and tokens live in `.env` (gitignored). Only `.env.example` with placeholder values is committed. Before every commit, check `git diff --staged` for anything that looks like a key.
3. **Credits cost real money.** Default to `NANSEN_MODE=replay` (recorded fixtures) for development and tests. Only use `NANSEN_MODE=live` when the task explicitly needs fresh data, and never run anything that could exceed 500 credits without asking first.
4. **Verify the API, don't guess it.** Before writing or changing code for a Nansen endpoint, fetch its docs page as Markdown (append `.md` to the docs URL, e.g. `https://docs.nansen.ai/api/token-god-mode/token-screener.md`) and match field names, types and limits exactly. Record what you verified in `docs/nansen-endpoints.md`.
5. **Work in phases.** One phase from `SPEC.md` per branch and per pull request. Don't start the next phase until the current one meets its acceptance criteria.
6. **Plan first.** For each phase, write a short plan (files to touch, approach, test cases) before coding. For anything non-trivial, show the plan and wait for approval.

## Stack

- Python 3.12, FastAPI, Jinja2 + HTMX for the dashboard (no JS build step)
- `httpx` (async) for HTTP, `pydantic` v2 for API models and config
- SQLite in WAL mode via SQLAlchemy 2.x; the worker is the only writer
- `pytest`, `pytest-asyncio`, `respx` for HTTP mocking
- `ruff` (lint + format), `mypy --strict` on `app/`
- Docker + docker compose, Caddy as reverse proxy

## Commands

```bash
make install     # create venv, install deps (incl. dev)
make check       # ruff + mypy + pytest (replay mode) — must pass before every commit
make test        # pytest only
make run         # web + worker locally, replay mode
make run-live    # web + worker against the real API (costs credits)
make record      # record fresh fixtures from the live API (costs credits; ask first)
make backtest    # run the backtest over historical endpoints (costs credits; ask first)
make docker      # build the image and run docker compose locally
```

## Code conventions

- All Nansen calls go through `app/nansen/client.py`. No other module talks to `api.nansen.ai` directly.
- Every endpoint response is parsed into a pydantic model in `app/nansen/models.py`. Unknown fields are allowed; missing required fields fail loudly.
- Pipeline logic (`app/pipeline/`) is pure functions over typed inputs wherever possible, so it can be unit-tested without HTTP.
- All thresholds and weights come from `config/scoring.yaml`, never hardcoded.
- Timestamps are timezone-aware UTC everywhere.
- Log with the standard `logging` module in JSON format; never log the API key or full request headers.

## Definition of done (every phase)

- `make check` passes locally and in CI.
- New logic has unit tests, including at least one edge case and one failure case.
- The self-review checklist in `SPEC.md` §8 has been walked through and the PR description says so.
- `README.md` and `docs/` are updated if behaviour, config or commands changed.
