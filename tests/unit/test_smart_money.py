import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.nansen.exceptions import NansenAPIError
from app.nansen.models import TGMDexTrade
from app.pipeline.smart_money import (
    OTHER_LABEL,
    SmartMoneyMetrics,
    analyze_smart_money,
    compute_smart_money_metrics,
    match_label,
    wallet_label_and_weight,
)
from tests.factories import StubClient, smart_money_config, tgm_trade

DEPLOYED = datetime(2026, 9, 24, 2, 0, 0, tzinfo=UTC)
NOW = DEPLOYED + timedelta(hours=2)
CFG = smart_money_config()
PAGINATION = {"page": 1, "per_page": 10, "is_last_page": True}


def at(minutes: float) -> str:
    return (DEPLOYED + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def buy(
    wallet: str,
    minutes: float,
    tokens: float = 1000.0,
    usd: float = 1000.0,
    label: str | None = None,
) -> TGMDexTrade:
    return tgm_trade(wallet, at(minutes), tokens=tokens, usd=usd, label=label)


def sell(
    wallet: str,
    minutes: float,
    tokens: float = 1000.0,
    usd: float = 1000.0,
    label: str | None = None,
) -> TGMDexTrade:
    return tgm_trade(wallet, at(minutes), tokens=tokens, usd=usd, action="SELL", label=label)


def metrics(trades: list[TGMDexTrade], **overrides: Any) -> SmartMoneyMetrics:
    args: dict[str, Any] = {"deployed_at": DEPLOYED, "now": NOW, "circulating_supply": 1_000_000.0}
    args.update(overrides)
    cfg = args.pop("cfg", CFG)
    return compute_smart_money_metrics(trades, cfg, **args)


# --- the four SPEC scenarios ---


def test_accumulating_smart_money() -> None:
    trades = [
        buy("w1", 2, label="Fund"),
        buy("w2", 3, label="Smart Trader"),
        buy("w3", 4, label="30D Smart Trader"),
        sell("w1", 30, tokens=100.0, usd=110.0),
    ]

    result = metrics(trades)

    assert result.wallet_count == 3
    assert result.signal is True
    assert result.label_counts == {"Fund": 1, "Smart Trader": 1, "30D Smart Trader": 1}
    assert result.weighted_score == pytest.approx(3.2)
    assert result.net_flow_usd == pytest.approx(2890.0)
    assert result.net_selling is False
    assert result.bought_tokens == 3000.0
    assert result.sold_tokens == 100.0
    assert result.holding_ratio == pytest.approx(2900 / 3000)
    assert result.wallets_still_holding == 3
    assert result.first_entry_at == DEPLOYED + timedelta(minutes=2)
    assert result.minutes_after_launch == pytest.approx(2.0)
    assert result.market_cap_at_entry == pytest.approx(1_000_000.0)


def test_flipped_smart_money_bought_then_sold() -> None:
    trades = [
        buy("w1", 2, usd=1000.0),
        buy("w2", 3, usd=1000.0),
        sell("w1", 40, usd=1500.0),
        sell("w2", 50, usd=1500.0),
    ]

    result = metrics(trades)

    assert result.net_flow_usd == pytest.approx(-1000.0)
    assert result.net_selling is True
    assert result.holding_ratio == 0.0
    assert result.wallets_still_holding == 0
    assert result.wallet_count == 2


def test_mixed_labels_pick_the_longest_and_best_match() -> None:
    trades = [
        buy("w1", 1, label="180D Smart Trader"),
        buy("w2", 2, label="Some Whale"),
        buy("w3", 3, label=None),
        buy("w4", 4, label="Smart Trader"),
        buy("w4", 5, label="Fund"),
    ]

    result = metrics(trades)

    assert result.label_counts == {"180D Smart Trader": 1, OTHER_LABEL: 2, "Fund": 1}
    assert result.weighted_score == pytest.approx(1.3 + 1.0 + 1.0 + 1.5)


def test_zero_smart_money() -> None:
    result = metrics([])

    assert result.wallet_count == 0
    assert result.signal is False
    assert result.label_counts == {}
    assert result.weighted_score == 0.0
    assert result.net_flow_usd == 0.0
    assert result.net_selling is False
    assert result.holding_ratio is None
    assert result.first_entry_at is None
    assert result.minutes_after_launch is None
    assert result.market_cap_at_entry is None


# --- edge and failure cases ---


def test_repeat_buyer_counts_once() -> None:
    result = metrics([buy("w1", 1), buy("w1", 2), buy("w1", 3)])

    assert result.wallet_count == 1
    assert result.weighted_score == pytest.approx(1.0)


def test_signal_boundary_is_min_wallets() -> None:
    assert metrics([buy("w1", 1)]).signal is False
    assert metrics([buy("w1", 1), buy("w2", 2)]).signal is True


def test_sells_outside_the_flow_window_still_count_for_holding_ratio() -> None:
    now = DEPLOYED + timedelta(hours=30)
    trades = [buy("w1", 60, usd=1000.0), sell("w1", 600, tokens=500.0, usd=500.0)]

    result = metrics(trades, now=now)

    # the buy at +1h is before now-24h (+6h), the sell at +10h is inside it
    assert result.net_flow_usd == pytest.approx(-500.0)
    assert result.holding_ratio == pytest.approx(0.5)


def test_trades_after_now_are_excluded_from_flow() -> None:
    result = metrics([buy("w1", 1, usd=1000.0), buy("w2", 500, usd=9999.0)])

    assert result.net_flow_usd == pytest.approx(1000.0)


def test_sell_only_history_has_no_buyers_and_no_ratio() -> None:
    result = metrics([sell("w1", 5)])

    assert result.wallet_count == 0
    assert result.holding_ratio is None
    assert result.first_entry_at is None
    assert result.net_selling is True


def test_market_cap_is_none_without_supply() -> None:
    result = metrics([buy("w1", 1)], circulating_supply=None)

    assert result.market_cap_at_entry is None
    assert result.minutes_after_launch == pytest.approx(1.0)


def test_minutes_after_launch_is_none_without_deployment_time() -> None:
    result = metrics([buy("w1", 1)], deployed_at=None)

    assert result.minutes_after_launch is None
    assert result.market_cap_at_entry is not None


def test_zero_token_entry_gives_no_market_cap() -> None:
    result = metrics([buy("w1", 1, tokens=0.0)])

    assert result.market_cap_at_entry is None


def test_unsorted_input_uses_the_earliest_buy_as_first_entry() -> None:
    result = metrics([buy("w2", 9), buy("w1", 3)])

    assert result.first_entry_at == DEPLOYED + timedelta(minutes=3)


def test_match_label_prefers_longest_key_case_insensitively() -> None:
    assert match_label("my 180d smart trader", CFG) == "180D Smart Trader"
    assert match_label("Smart Trader", CFG) == "Smart Trader"
    assert match_label("Random", CFG) is None
    assert match_label(None, CFG) is None
    assert match_label("", CFG) is None


def test_unmatched_wallet_uses_default_weight() -> None:
    cfg = smart_money_config(default_label_weight=0.5)

    assert wallet_label_and_weight(["nope", None], cfg) == (OTHER_LABEL, 0.5)


# --- async wiring ---


def _rows(*trades: TGMDexTrade) -> list[dict[str, Any]]:
    return [trade.model_dump() for trade in trades]


@pytest.mark.asyncio
async def test_analyze_smart_money_requests_smart_trades_and_supply() -> None:
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [
                {"data": _rows(buy("w1", 2, label="Fund")), "pagination": PAGINATION}
            ],
            "/api/v1/tgm/token-information": [
                {"data": {"token_details": {"circulating_supply": 500_000.0}}}
            ],
        }
    )
    try:
        result = await analyze_smart_money(
            client, CFG, chain="solana", token_address="tok", deployed_at=DEPLOYED, now=NOW
        )
    finally:
        await client.aclose()

    body = client.requests[0][1]
    assert body["only_smart_money"] is True
    assert body["date"] == {"from": "2026-09-24T02:00:00Z", "to": "2026-09-24T04:00:00Z"}
    assert body["order_by"] == [{"field": "block_timestamp", "direction": "ASC"}]
    assert client.requests[1][1]["timeframe"] == "1d"
    assert result.market_cap_at_entry == pytest.approx(500_000.0)


@pytest.mark.asyncio
async def test_analyze_smart_money_reads_holder_count_even_without_buys() -> None:
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [{"data": [], "pagination": PAGINATION}],
            "/api/v1/tgm/token-information": [{"data": {"spot_metrics": {"total_holders": 812}}}],
        }
    )
    try:
        result = await analyze_smart_money(
            client, CFG, chain="solana", token_address="tok", deployed_at=DEPLOYED, now=NOW
        )
    finally:
        await client.aclose()

    assert result.signal is False
    assert result.total_holders == 812
    assert result.smart_wallets == frozenset()
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_analyze_smart_money_survives_a_failed_token_information_call() -> None:
    def failing(body: dict[str, Any]) -> dict[str, Any]:
        raise NansenAPIError(status=503, message="down")

    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [{"data": _rows(buy("w1", 2)), "pagination": PAGINATION}],
            "/api/v1/tgm/token-information": failing,
        }
    )
    try:
        result = await analyze_smart_money(
            client, CFG, chain="solana", token_address="tok", deployed_at=DEPLOYED, now=NOW
        )
    finally:
        await client.aclose()

    assert result.wallet_count == 1
    assert result.market_cap_at_entry is None


@pytest.mark.asyncio
async def test_analyze_smart_money_reads_every_page() -> None:
    more = {"page": 1, "per_page": 1000, "is_last_page": False}
    last = {"page": 2, "per_page": 1000, "is_last_page": True}
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [
                {"data": _rows(buy("w1", 2)), "pagination": more},
                {"data": _rows(sell("w1", 40)), "pagination": last},
            ],
            "/api/v1/tgm/token-information": [{"data": {}}],
        }
    )
    try:
        result = await analyze_smart_money(
            client, CFG, chain="solana", token_address="tok", deployed_at=DEPLOYED, now=NOW
        )
    finally:
        await client.aclose()

    pages = [b["pagination"]["page"] for e, b in client.requests if e.endswith("dex-trades")]
    assert pages == [1, 2]
    assert result.holding_ratio == 0.0
    assert result.net_flow_usd == 0.0
    assert result.net_selling is False


@pytest.mark.asyncio
async def test_analyze_smart_money_warns_when_the_page_cap_truncates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    more = {"page": 1, "per_page": 1000, "is_last_page": False}
    client = StubClient(
        {
            "/api/v1/tgm/dex-trades": [{"data": [], "pagination": more} for _ in range(5)],
            "/api/v1/tgm/token-information": [{"data": {}}],
        }
    )
    try:
        with caplog.at_level(logging.WARNING):
            await analyze_smart_money(
                client, CFG, chain="solana", token_address="tok", deployed_at=None, now=NOW
            )
    finally:
        await client.aclose()

    assert "truncated" in caplog.text
    assert len(client.requests) == 6
    assert client.requests[0][1]["date"]["from"] == "2026-09-23T04:00:00Z"


def test_smart_wallets_are_the_distinct_buyers() -> None:
    result = metrics([buy("w1", 1), buy("w2", 2), sell("w3", 3), buy("w1", 4)])

    assert result.smart_wallets == frozenset({"w1", "w2"})
