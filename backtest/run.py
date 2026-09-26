"""Backtest CLI. Every command that spends credits prints an estimate and asks for confirmation.

    python -m backtest.run estimate --limit 15
    python -m backtest.run discover --max-credits 300
    python -m backtest.run analyze --limit 3 --max-credits 500
    python -m backtest.run report

Run the paid commands with NANSEN_MODE=live (for example `$env:NANSEN_MODE="live"` in PowerShell).
Responses are cached under backtest/cache/, so repeating a command costs nothing.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime

from app.config import BacktestConfig, ScoringConfig, Settings, get_settings, load_scoring_config
from app.nansen.exceptions import BudgetExceeded, NansenError
from backtest.client import BacktestClient
from backtest.discover import (
    BACKTEST_CHAINS,
    SCREENER_CREDITS,
    HistCandidate,
    discover,
    sample_order,
    screener_calls,
)
from backtest.pipeline import process
from backtest.report import main_report
from backtest.results import (
    CANDIDATES_PATH,
    RESULTS_PATH,
    SKIPS_PATH,
    ResultRow,
    append_jsonl,
    load_results,
    load_skips,
)

OHLCV_CREDITS = 5
TOKEN_INFO_CREDITS = 1
TRADES_PAGE_CREDITS = 5
FUNDER_LOOKUPS = 30


def estimate_discover(bcfg: BacktestConfig, chains: list[str]) -> int:
    return screener_calls(bcfg, chains) * SCREENER_CREDITS


def estimate_per_token(bcfg: BacktestConfig, cfg: ScoringConfig) -> tuple[int, int]:
    """(typical, worst case) credits for one analysed token."""
    tiers = len(cfg.smart_money.label_weights)
    fixed = OHLCV_CREDITS + TOKEN_INFO_CREDITS + FUNDER_LOOKUPS
    typical = fixed + TRADES_PAGE_CREDITS * (2 + tiers + 2)
    worst = fixed + TRADES_PAGE_CREDITS * (
        bcfg.max_trade_pages + tiers * bcfg.tier_pages + bcfg.exit_trade_pages
    )
    return typical, worst


def confirm(text: str, input_fn: Callable[[str], str]) -> bool:
    print(text)
    return input_fn("Proceed? [y/N] ").strip().lower() == "y"


def require_live(settings: Settings) -> bool:
    if settings.nansen_mode != "live":
        print("Refusing to run: set NANSEN_MODE=live for this command (it spends credits).")
        return False
    return True


def load_candidates() -> list[HistCandidate]:
    if not CANDIDATES_PATH.exists():
        return []
    raw = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    return [HistCandidate.model_validate(item) for item in raw]


def print_summary(client: BacktestClient) -> None:
    print(
        f"Credits used this run: {client.run_credits} of {client.max_credits} allowed; "
        f"{client.live_calls} live calls, {client.cache_hits} served from the cache."
    )


async def run_discover(
    settings: Settings,
    bcfg: BacktestConfig,
    chains: list[str],
    max_credits: int,
    today: date,
) -> None:
    client = BacktestClient(settings, max_credits=max_credits)
    try:
        candidates = await discover(client, bcfg, chains, today)
    except BudgetExceeded:
        print("Stopped: the credit cap was reached before discovery finished.")
        print_summary(client)
        await client.aclose()
        return
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates], indent=1), encoding="utf-8"
    )
    per_chain = {chain: sum(1 for c in candidates if c.chain == chain) for chain in chains}
    print(f"Found {len(candidates)} candidate tokens: {per_chain}")
    print_summary(client)
    await client.aclose()


async def run_analyze(
    settings: Settings,
    bcfg: BacktestConfig,
    cfg: ScoringConfig,
    limit: int,
    max_credits: int,
    today: date,
) -> None:
    done = {(r.chain, r.token_address) for r in load_results()} | {
        (s.chain, s.token_address) for s in load_skips()
    }
    todo = [c for c in sample_order(load_candidates()) if c.key not in done][:limit]
    client = BacktestClient(settings, max_credits=max_credits)
    try:
        for candidate in todo:
            try:
                outcome = await process(client, bcfg, cfg, candidate, today)
            except BudgetExceeded:
                print("Stopped: the credit cap was reached.")
                break
            except NansenError as exc:
                print(f"Skipping {candidate.chain}/{candidate.token_address}: {type(exc).__name__}")
                continue
            append_jsonl(RESULTS_PATH if isinstance(outcome, ResultRow) else SKIPS_PATH, outcome)
            label = (
                f"{outcome.verdict} score {outcome.score:.0f}"
                if isinstance(outcome, ResultRow)
                else f"skipped ({outcome.reason})"
            )
            print(f"{candidate.chain}/{candidate.symbol}: {label}")
    finally:
        print_summary(client)
        await client.aclose()


def main(
    argv: list[str] | None = None,
    input_fn: Callable[[str], str] = input,
    today: date | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="backtest.run", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    estimate = sub.add_parser("estimate", help="print credit estimates; spends nothing")
    estimate.add_argument("--limit", type=int, default=15)
    disc = sub.add_parser("discover", help="find candidate tokens (spends credits)")
    disc.add_argument("--max-credits", type=int, required=True)
    analyze = sub.add_parser("analyze", help="analyse candidates (spends credits)")
    analyze.add_argument("--limit", type=int, required=True)
    analyze.add_argument("--max-credits", type=int, required=True)
    sub.add_parser("report", help="write docs/backtest.md from the stored results")
    args = parser.parse_args(argv)

    cfg = load_scoring_config()
    bcfg = cfg.backtest
    settings = get_settings()
    chains = [c for c in settings.chains if c in BACKTEST_CHAINS]
    today = today or datetime.now(UTC).date()
    typical, worst = estimate_per_token(bcfg, cfg)

    if args.command == "estimate":
        print(
            f"Discovery ({bcfg.days} days x {len(chains)} chains): "
            f"{estimate_discover(bcfg, chains)} credits"
        )
        print(
            f"Analysis of {args.limit} tokens: about {args.limit * typical} credits "
            f"(worst case {args.limit * worst}); {typical} typical, {worst} worst case per token"
        )
        return 0

    if args.command == "report":
        main_report(bcfg.dump_threshold_pct, bcfg.horizons_hours)
        print("Wrote docs/backtest.md and docs/backtest.svg")
        return 0

    if not require_live(settings):
        return 2
    if args.command == "discover":
        need = estimate_discover(bcfg, chains)
        if need > args.max_credits:
            print(
                f"Refusing: discovery needs about {need} credits but "
                f"--max-credits is {args.max_credits}."
            )
            return 2
        if not confirm(
            f"Discovery will spend about {need} credits (cap {args.max_credits}).", input_fn
        ):
            print("Aborted.")
            return 1
        asyncio.run(run_discover(settings, bcfg, chains, args.max_credits, today))
        return 0

    text = (
        f"Analysis of up to {args.limit} tokens: about {args.limit * typical} credits "
        f"(worst case {args.limit * worst}), hard cap {args.max_credits}."
    )
    if not confirm(text, input_fn):
        print("Aborted.")
        return 1
    asyncio.run(run_analyze(settings, bcfg, cfg, args.limit, args.max_credits, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
