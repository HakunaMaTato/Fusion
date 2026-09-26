import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.pipeline.discovery import (
    CONFIRM_BATCH_SIZE,
    FEED_SCREENER,
    FEED_SMART_MONEY,
    Candidate,
    confirm_smart_money_tokens,
    fetch_feed_a,
    fetch_feed_b,
    filter_screener_tokens,
    merge_candidates,
    smart_wallets_by_token,
)
from tests.factories import StubClient, discovery_config, screener_token, smart_trade

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
CFG = discovery_config()
PAGINATION = {"page": 1, "per_page": 10, "is_last_page": True}


def _candidate(address: str, feed: str, seen: datetime, chain: str = "solana") -> Candidate:
    return Candidate(
        chain=chain,
        token_address=address,
        token_symbol="T",
        token_age_hours=1.0,
        market_cap_usd=2e6,
        feeds={feed: seen},
    )


def test_feed_a_keeps_token_inside_window() -> None:
    kept = filter_screener_tokens([screener_token(age_hours=4.0)], CFG, NOW)

    assert [c.token_address for c in kept] == ["tok1"]
    assert kept[0].feeds == {FEED_SCREENER: NOW}


def test_feed_a_excludes_token_older_than_window() -> None:
    assert filter_screener_tokens([screener_token(age_hours=4.5)], CFG, NOW) == []


def test_feed_a_excludes_low_market_cap() -> None:
    assert filter_screener_tokens([screener_token(market_cap=999_999)], CFG, NOW) == []


def test_feed_a_excludes_and_logs_missing_age(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        kept = filter_screener_tokens([screener_token(age_hours=None)], CFG, NOW)

    assert kept == []
    assert "missing age" in caplog.text


def test_feed_a_excludes_missing_market_cap() -> None:
    assert filter_screener_tokens([screener_token(market_cap=None)], CFG, NOW) == []


def test_feed_b_counts_distinct_wallets_only() -> None:
    trades = [smart_trade("w1"), smart_trade("w1"), smart_trade("w2"), smart_trade("w3", "tok2")]

    result = smart_wallets_by_token(trades, min_wallets=2)

    assert result == {("solana", "tok1"): {"w1", "w2"}}


def test_feed_b_ignores_older_tokens_and_keeps_chains_apart() -> None:
    trades = [
        smart_trade("w1", age_days=3),
        smart_trade("w2", age_days=3),
        smart_trade("w1", chain="base"),
        smart_trade("w2", chain="solana"),
    ]

    assert smart_wallets_by_token(trades, min_wallets=2) == {}


def test_feed_b_confirmation_applies_max_age_not_pump_window() -> None:
    wallets = {("solana", "tok1"): {"w1", "w2"}, ("solana", "old"): {"w1", "w2"}}
    screener = [
        screener_token("tok1", age_hours=10, market_cap=50_000),
        screener_token("old", age_hours=30),
    ]

    kept = confirm_smart_money_tokens(wallets, screener, CFG, NOW)

    assert [c.token_address for c in kept] == ["tok1"]
    assert kept[0].smart_wallet_count == 2
    assert kept[0].feeds == {FEED_SMART_MONEY: NOW}


def test_feed_b_drops_and_logs_unconfirmed_token(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        kept = confirm_smart_money_tokens({("solana", "ghost"): {"w1", "w2"}}, [], CFG, NOW)

    assert kept == []
    assert "not confirmed" in caplog.text


def test_feed_b_drops_token_with_missing_age() -> None:
    wallets = {("solana", "tok1"): {"w1", "w2"}}

    kept = confirm_smart_money_tokens(wallets, [screener_token(age_hours=None)], CFG, NOW)

    assert kept == []


def test_merge_dedupes_and_records_both_feeds() -> None:
    later = NOW + timedelta(minutes=5)
    a = [_candidate("tok1", FEED_SCREENER, later)]
    b = [_candidate("tok1", FEED_SMART_MONEY, NOW)]
    b[0].smart_wallet_count = 3

    merged = merge_candidates(a, b)

    assert len(merged) == 1
    assert merged[0].feeds == {FEED_SCREENER: later, FEED_SMART_MONEY: NOW}
    assert merged[0].smart_wallet_count == 3


def test_merge_keeps_earliest_first_seen_for_same_feed() -> None:
    later = NOW + timedelta(minutes=5)

    merged = merge_candidates(
        [_candidate("tok1", FEED_SCREENER, later)], [_candidate("tok1", FEED_SCREENER, NOW)]
    )

    assert merged[0].feeds == {FEED_SCREENER: NOW}


def test_merge_keeps_same_address_on_different_chains_separate() -> None:
    merged = merge_candidates(
        [_candidate("tok1", FEED_SCREENER, NOW, chain="solana")],
        [_candidate("tok1", FEED_SCREENER, NOW, chain="base")],
    )

    assert len(merged) == 2


def test_merge_does_not_mutate_inputs() -> None:
    a = [_candidate("tok1", FEED_SCREENER, NOW)]
    b = [_candidate("tok1", FEED_SMART_MONEY, NOW)]

    merge_candidates(a, b)

    assert a[0].feeds == {FEED_SCREENER: NOW}


def _screener_row(address: str, age_hours: float, market_cap: float) -> dict[str, Any]:
    return {
        "chain": "solana",
        "token_address": address,
        "token_symbol": "T",
        "token_age_hours": age_hours,
        "market_cap_usd": market_cap,
    }


def _trade_row(trader: str, token: str) -> dict[str, Any]:
    return smart_trade(trader, token).model_dump()


@pytest.mark.asyncio
async def test_fetch_feed_a_sends_day_fraction_filter_and_refilters_hours() -> None:
    client = StubClient(
        {
            "/api/v1/token-screener": [
                {
                    "data": [_screener_row("in", 3.5, 2e6), _screener_row("out", 4.5, 2e6)],
                    "pagination": PAGINATION,
                }
            ]
        }
    )
    try:
        kept = await fetch_feed_a(client, CFG, ["solana"], NOW)
    finally:
        await client.aclose()

    body = client.requests[0][1]
    assert body["filters"]["token_age_days"] == {"max": pytest.approx(4 / 24)}
    assert body["filters"]["market_cap_usd"] == {"min": 1_000_000}
    assert [c.token_address for c in kept] == ["in"]


@pytest.mark.asyncio
async def test_fetch_feed_b_confirms_with_one_batched_screener_call() -> None:
    trades = [_trade_row("w1", "tok1"), _trade_row("w2", "tok1"), _trade_row("w1", "tok2")]
    client = StubClient(
        {
            "/api/v1/smart-money/dex-trades": [{"data": trades, "pagination": PAGINATION}],
            "/api/v1/token-screener": [
                {"data": [_screener_row("tok1", 6, 400_000)], "pagination": PAGINATION}
            ],
        }
    )
    try:
        kept = await fetch_feed_b(client, CFG, ["solana"], min_wallets=2, now=NOW)
    finally:
        await client.aclose()

    assert client.requests[0][1]["filters"]["token_bought_age_days"] == {"max": 0.0}
    screener_calls = [r for r in client.requests if r[0] == "/api/v1/token-screener"]
    assert len(screener_calls) == 1
    assert screener_calls[0][1]["filters"]["token_address"] == ["tok1"]
    assert [c.token_address for c in kept] == ["tok1"]


@pytest.mark.asyncio
async def test_fetch_feed_b_skips_screener_when_no_token_has_enough_wallets() -> None:
    client = StubClient(
        {
            "/api/v1/smart-money/dex-trades": [
                {"data": [_trade_row("w1", "tok1")], "pagination": PAGINATION}
            ]
        }
    )
    try:
        kept = await fetch_feed_b(client, CFG, ["solana"], min_wallets=2, now=NOW)
    finally:
        await client.aclose()

    assert kept == []
    assert len(client.requests) == 1


def _row_on(chain: str, address: str) -> dict[str, Any]:
    return smart_trade("w1", address, chain=chain).model_dump()


@pytest.mark.asyncio
async def test_fetch_feed_b_confirms_each_chain_in_its_own_request() -> None:
    """Nansen's edge blocked one request mixing many addresses from several chains (403)."""
    trades = []
    for chain in ("solana", "bsc", "robinhood"):
        for wallet in ("w1", "w2"):
            trades.append(smart_trade(wallet, f"{chain}-tok", chain=chain).model_dump())
    client = StubClient(
        {
            "/api/v1/smart-money/dex-trades": [{"data": trades, "pagination": PAGINATION}],
            "/api/v1/token-screener": [{"data": [], "pagination": PAGINATION}] * 3,
        }
    )
    try:
        await fetch_feed_b(client, CFG, ["solana", "bnb", "robinhood"], min_wallets=2, now=NOW)
    finally:
        await client.aclose()

    calls = [r[1] for r in client.requests if r[0] == "/api/v1/token-screener"]
    assert sorted((c["chains"], c["filters"]["token_address"]) for c in calls) == [
        (["bsc"], ["bsc-tok"]),
        (["robinhood"], ["robinhood-tok"]),
        (["solana"], ["solana-tok"]),
    ]


@pytest.mark.asyncio
async def test_fetch_feed_b_splits_a_chain_into_batches() -> None:
    count = CONFIRM_BATCH_SIZE + 5
    trades = [
        smart_trade(w, f"tok{i:03d}").model_dump() for i in range(count) for w in ("w1", "w2")
    ]
    client = StubClient(
        {
            "/api/v1/smart-money/dex-trades": [{"data": trades, "pagination": PAGINATION}],
            "/api/v1/token-screener": [{"data": [], "pagination": PAGINATION}] * 2,
        }
    )
    try:
        await fetch_feed_b(client, CFG, ["solana"], min_wallets=2, now=NOW)
    finally:
        await client.aclose()

    sizes = [len(r[1]["filters"]["token_address"]) for r in client.requests[1:]]
    assert sizes == [CONFIRM_BATCH_SIZE, 5]
    assert CONFIRM_BATCH_SIZE <= 50
