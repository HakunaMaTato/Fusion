from app.config import DiscoveryConfig
from app.nansen.models import SmartMoneyDexTrade, TokenScreenerToken


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
