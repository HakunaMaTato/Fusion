from datetime import date, timedelta

from app.config import BacktestConfig, ScoringConfig
from backtest.analyze import analyze_at, bundle_exit_fraction, net_positions
from backtest.client import BacktestClient
from backtest.discover import HistCandidate
from backtest.fetch import Skipped, fetch_later_trades, fetch_token_data
from backtest.lifecycle import compute_lifecycle
from backtest.results import OutcomeRow, ResultRow, SkipRow
from backtest.timeline import compute_outcomes

EXIT_HOURS = 24


async def process(
    client: BacktestClient,
    bcfg: BacktestConfig,
    cfg: ScoringConfig,
    candidate: HistCandidate,
    today: date,
) -> ResultRow | SkipRow:
    credits_before = client.run_credits
    try:
        data = await fetch_token_data(client, bcfg, cfg, candidate, today)
    except Skipped as skip:
        return SkipRow(
            chain=candidate.chain, token_address=candidate.token_address, reason=skip.reason
        )

    pit = data.point_in_time
    analysis = analyze_at(pit, cfg)
    outcomes = compute_outcomes(
        data.candles,
        data.decision,
        data_end=data.data_end,
        horizons_hours=bcfg.horizons_hours,
        dump_threshold_pct=bcfg.dump_threshold_pct,
    )

    exit_fraction: float | None = None
    exited: bool | None = None
    bundled = analysis.bundle.bundled_wallets
    exit_window_end = pit.decision_at + timedelta(hours=EXIT_HOURS)
    if bundled and data.data_end >= exit_window_end:
        later = await fetch_later_trades(client, bcfg, candidate, pit.decision_at, EXIT_HOURS)
        if later is not None:
            exit_fraction = bundle_exit_fraction(bundled, net_positions(pit.trades), later)
            if exit_fraction is not None:
                exited = exit_fraction >= cfg.bundle.exited_sold_pct / 100

    return ResultRow(
        chain=candidate.chain,
        token_address=candidate.token_address,
        symbol=candidate.symbol,
        day=candidate.day,
        launch_at=pit.launch_at,
        decision_at=pit.decision_at,
        decision_market_cap=data.decision.market_cap,
        verdict=analysis.score.verdict,
        score=analysis.score.score,
        vetoes=analysis.score.vetoes,
        bundle_status=analysis.bundle.status,
        bundle_supply_pct=analysis.bundle.supply_pct,
        bundle_wallets=len(bundled),
        sm_wallets=analysis.smart_money.wallet_count,
        sm_net_selling=analysis.smart_money.net_selling,
        smart_in_bundle=len(analysis.smart_wallets_in_bundle),
        outcomes=[
            OutcomeRow(
                hours=o.hours,
                complete=o.complete,
                max_drawdown_pct=o.max_drawdown_pct,
                price_change_pct=o.price_change_pct,
                dumped=o.dumped,
            )
            for o in outcomes
        ],
        bundle_exit_fraction=exit_fraction,
        bundle_exited_24h=exited,
        lookahead_rows_dropped=data.lookahead_rows_dropped,
        credits_spent=client.run_credits - credits_before,
        lifecycle=compute_lifecycle(data.candles, data.decision, data_end=data.data_end),
    )
