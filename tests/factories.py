from collections.abc import Callable
from typing import Any

from app.config import BundleConfig, DiscoveryConfig, Settings
from app.nansen.client import NansenClient
from app.nansen.models import (
    ProfilerRelatedWallet,
    SmartMoneyDexTrade,
    TGMDexTrade,
    TGMHolder,
    TokenScreenerToken,
)

Responder = list[dict[str, Any]] | Callable[[dict[str, Any]], dict[str, Any]]


class StubClient(NansenClient):
    """NansenClient whose post() serves canned responses per endpoint and records requests."""

    def __init__(self, responses: dict[str, Responder]) -> None:
        super().__init__(Settings(nansen_mode="replay"))
        self._responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(
        self, endpoint: str, body: dict[str, Any], *, cache_ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        self.requests.append((endpoint, body))
        responder = self._responses[endpoint]
        if callable(responder):
            return responder(body)
        return responder.pop(0)


def bundle_config(**overrides: object) -> BundleConfig:
    values: dict[str, object] = {
        "early_window_minutes": 10,
        "max_early_buys": 150,
        "max_funder_lookups": 30,
        "funder_relations": ["First Funder"],
        "same_second_min_wallets": 3,
        "common_funder_min_wallets": 3,
        "similar_size_max_cv": 0.15,
        "ignore_funder_labels": ["CEX", "Exchange", "DEX Router", "Bridge"],
        "distributing_sold_pct": 25,
        "exited_sold_pct": 90,
    }
    values.update(overrides)
    return BundleConfig(**values)  # type: ignore[arg-type]


def tgm_trade(
    wallet: str,
    timestamp: str,
    tokens: float = 1000.0,
    usd: float = 100.0,
    action: str = "BUY",
) -> TGMDexTrade:
    return TGMDexTrade(
        block_timestamp=timestamp,
        transaction_hash=f"tx-{wallet}-{timestamp}",
        trader_address=wallet,
        action=action,  # type: ignore[arg-type]
        token_address="tok",
        token_name="TOK",
        token_amount=tokens,
        traded_token_address="sol",
        traded_token_name="SOL",
        traded_token_amount=1.0,
        estimated_swap_price_usd=0.1,
        estimated_value_usd=usd,
    )


def holder(address: str, tokens: float, ownership_pct: float) -> TGMHolder:
    return TGMHolder(address=address, token_amount=tokens, ownership_percentage=ownership_pct)


def related(
    address: str, relation: str, order: int = 1, label: str | None = None
) -> ProfilerRelatedWallet:
    return ProfilerRelatedWallet(
        address=address,
        address_label=label,
        relation=relation,
        transaction_hash="fund-tx",
        block_timestamp="2026-09-24T01:00:00Z",
        order=order,
        chain="solana",
    )


def discovery_config(**overrides: object) -> DiscoveryConfig:
    values: dict[str, object] = {
        "max_age_hours": 24,
        "pump_window_hours": 4,
        "min_market_cap_usd": 1_000_000,
        "screener_timeframe": "1h",
        "poll_seconds": 120,
        "smart_money_poll_seconds": 900,
    }
    values.update(overrides)
    return DiscoveryConfig(**values)  # type: ignore[arg-type]


def screener_token(
    address: str = "tok1",
    chain: str = "solana",
    age_hours: float | None = 2.0,
    market_cap: float | None = 2_000_000,
    symbol: str = "TOK",
) -> TokenScreenerToken:
    return TokenScreenerToken(
        chain=chain,
        token_address=address,
        token_symbol=symbol,
        token_age_hours=age_hours,
        market_cap_usd=market_cap,
    )


def smart_trade(
    trader: str,
    token: str = "tok1",
    chain: str = "solana",
    age_days: int = 0,
) -> SmartMoneyDexTrade:
    return SmartMoneyDexTrade(
        chain=chain,
        block_timestamp="2026-09-24T05:00:00Z",
        transaction_hash=f"tx-{trader}-{token}",
        trader_address=trader,
        trader_address_label="Fund",
        token_bought_address=token,
        token_sold_address="usdc",
        token_bought_symbol="TOK",
        token_sold_symbol="USDC",
        token_bought_age_days=age_days,
        token_sold_age_days=1000,
    )
