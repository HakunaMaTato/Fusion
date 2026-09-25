"""A scripted Nansen for worker simulations: deterministic responses, credit accounting and a
fake clock, so ten minutes of worker time run in milliseconds."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.nansen.client import NansenClient
from tests.factories import smart_trade, tgm_trade

START = datetime(2026, 9, 25, 10, 0, 0, tzinfo=UTC)
PAGE = {"page": 1, "per_page": 1000, "is_last_page": True}

COSTS = {
    "/api/v1/token-screener": 1,
    "/api/v1/smart-money/dex-trades": 5,
    "/api/v1/tgm/dex-trades": 1,
    "/api/v1/profiler/address/related-wallets": 1,
    "/api/v1/tgm/holders": 5,
    "/api/v1/tgm/token-information": 1,
}
TIER_WALLETS = {"Fund": ["sm0"], "180D Smart Trader": ["sm1"], "Smart Trader": ["sm2"]}

Failure = Callable[[dict[str, Any], datetime], Exception | None]


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


@dataclass
class WorldToken:
    address: str
    symbol: str = "TOK"
    minutes_old: float = 30.0
    market_cap: float = 2_000_000.0
    volume: float = 500_000.0
    liquidity: float = 100_000.0
    holders: int = 3000
    sells_after_seconds: float | None = None
    has_deployment_date: bool = True

    def deployed_at(self, base: datetime = START) -> datetime:
        return base - timedelta(minutes=self.minutes_old)


class ScriptedNansen(NansenClient):
    """Behaves like NansenClient (budget guard, credit counting) but answers from a script."""

    def __init__(
        self,
        settings: Settings,
        clock: FakeClock,
        tokens: list[WorldToken],
        failures: dict[str, Failure] | None = None,
    ) -> None:
        super().__init__(settings, clock=clock.now)
        self.fake_clock = clock
        self.base = clock.now()
        self.tokens = {t.address: t for t in tokens}
        self.failures = failures or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def calls_to(self, endpoint: str) -> list[dict[str, Any]]:
        return [body for name, body in self.calls if name == endpoint]

    async def post(
        self, endpoint: str, body: dict[str, Any], *, cache_ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        self.calls.append((endpoint, body))
        self._check_budget()
        self._roll_credits_if_new_day()
        self._credits_used_today += COSTS[endpoint]
        failure = self.failures.get(endpoint)
        if failure is not None:
            error = failure(body, self.fake_clock.now())
            if error is not None:
                raise error
        return self._respond(endpoint, body)

    # --- the script ---

    def _respond(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if endpoint == "/api/v1/token-screener":
            return self._screener(body)
        if endpoint == "/api/v1/smart-money/dex-trades":
            rows = [
                smart_trade(w, token.address).model_dump()
                for token in self.tokens.values()
                for w in ("sm0", "sm1", "sm2")
            ]
            return {"data": rows, "pagination": PAGE}
        if endpoint == "/api/v1/tgm/dex-trades":
            return self._tgm_trades(body)
        if endpoint == "/api/v1/tgm/token-information":
            token = self.tokens[body["token_address"]]
            return {
                "data": {
                    "token_details": {"circulating_supply": 1e6, "total_supply": 1e6},
                    "spot_metrics": {"total_holders": token.holders},
                }
            }
        return {"data": [], "pagination": PAGE}

    def _screener(self, body: dict[str, Any]) -> dict[str, Any]:
        now = self.fake_clock.now()
        wanted = (body.get("filters") or {}).get("token_address")
        rows = []
        for token in self.tokens.values():
            if wanted is not None and token.address not in wanted:
                continue
            deployed = token.deployed_at(self.base)
            rows.append(
                {
                    "chain": "solana",
                    "token_address": token.address,
                    "token_symbol": token.symbol,
                    "token_age_hours": (now - deployed).total_seconds() / 3600,
                    "token_deployment_date": (
                        deployed.isoformat().replace("+00:00", "Z")
                        if token.has_deployment_date
                        else None
                    ),
                    "market_cap_usd": token.market_cap,
                    "volume": token.volume,
                    "liquidity": token.liquidity,
                }
            )
        return {"data": rows, "pagination": PAGE}

    def _tgm_trades(self, body: dict[str, Any]) -> dict[str, Any]:
        token = self.tokens[body["token_address"]]
        deployed = token.deployed_at(self.base)

        def at(seconds: float) -> str:
            return (deployed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

        if not body.get("only_smart_money"):
            rows = [
                tgm_trade(f"b{i}", at(i + 1), tokens=1000.0 + i * 91, usd=100.0 + i * 37)
                for i in range(10)
            ]
            return {"data": [r.model_dump() for r in rows], "pagination": PAGE}

        labels = (body.get("filters") or {}).get("include_smart_money_labels")
        if labels:
            wallets = TIER_WALLETS.get(labels[0], [])
            rows = [tgm_trade(w, at(120 + i * 60)) for i, w in enumerate(wallets)]
            return {"data": [r.model_dump() for r in rows], "pagination": PAGE}

        rows = [tgm_trade(f"sm{i}", at(120 + i * 60), tokens=1000.0, usd=1000.0) for i in range(3)]
        if token.sells_after_seconds is not None:
            sell_time = self.base + timedelta(seconds=token.sells_after_seconds)
            if self.fake_clock.now() >= sell_time:
                stamp = sell_time.isoformat().replace("+00:00", "Z")
                rows += [
                    tgm_trade(f"sm{i}", stamp, tokens=1000.0, usd=1500.0, action="SELL")
                    for i in range(3)
                ]
        return {"data": [r.model_dump() for r in rows], "pagination": PAGE}


@dataclass
class RecordingNotifier:
    outcome: bool = True
    raises: bool = False
    messages: list[str] = field(default_factory=list)

    async def send(self, text: str) -> bool:
        self.messages.append(text)
        if self.raises:
            raise RuntimeError("notifier down")
        return self.outcome
