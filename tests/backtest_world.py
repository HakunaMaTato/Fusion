"""A scripted historical Nansen for backtest tests: one token that pumps, is bundled, then dumps."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.config import Settings
from backtest.client import COSTS, BacktestClient
from backtest.discover import HistCandidate

DAY = date(2026, 9, 10)
TODAY = date(2026, 9, 20)
LAUNCH = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
DECISION_AT = LAUNCH + 7 * STEP  # candle 6 is the first at $1M and closes here
SUPPLY = 1_000_000_000.0
PRICE_SCALE = 1e-3  # market cap 1.2M at price 1.2e-3 implies SUPPLY
PAGE_LAST = {"page": 1, "per_page": 1000, "is_last_page": True}
BUNDLE = [f"b{i}" for i in range(12)]
ORGANIC = ["o0", "o1", "o2"]
TOKEN = "TOKENaddress"
CANDIDATE = HistCandidate(chain="solana", token_address=TOKEN, symbol="WORLD", day=DAY)


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def trade(wallet: str, at: datetime, action: str = "BUY", tokens: float = 1000.0) -> dict[str, Any]:
    return {
        "block_timestamp": iso(at),
        "transaction_hash": f"tx-{wallet}-{iso(at)}-{action}",
        "trader_address": wallet,
        "action": action,
        "token_name": "WORLD",
        "token_amount": tokens,
        "traded_token_name": "SOL",
        "traded_token_amount": 1.0,
        "estimated_swap_price_usd": 1.0,
        "estimated_value_usd": 100.0,
    }


def candle(i: int, close: float, mcap: float, low: float | None = None) -> dict[str, Any]:
    close, low = close * PRICE_SCALE, None if low is None else low * PRICE_SCALE
    return {
        "interval_start": iso(LAUNCH + i * STEP),
        "open": close,
        "high": close,
        "low": low if low is not None else close,
        "close": close,
        "volume": 10.0,
        "volume_usd": 1000.0,
        "market_cap": {"open": mcap, "high": mcap, "low": mcap, "close": mcap},
    }


def ohlcv() -> dict[str, Any]:
    candles = [candle(i, 1.0, 400_000.0) for i in range(6)]
    candles.append(candle(6, 1.2, 1_200_000.0))
    candles.append(candle(60, 0.5, 500_000.0, low=0.3))  # a crash a few hours later
    return {
        "chain": "solana",
        "token_address": TOKEN,
        "timeframe": "5m",
        "data": candles,
        "truncated": False,
    }


class ScriptedBacktest(BacktestClient):
    """Answers from the script, charges credits per endpoint, and records every request."""

    def __init__(
        self,
        max_credits: int = 100_000,
        *,
        never_pumps: bool = False,
        trades_never_end: bool = False,
        plant_future_rows: bool = True,
    ) -> None:
        super().__init__(Settings(_env_file=None), max_credits=max_credits)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.never_pumps = never_pumps
        self.trades_never_end = trades_never_end
        self.plant_future_rows = plant_future_rows

    async def post(
        self, endpoint: str, body: dict[str, Any], *, cache_ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        self.calls.append((endpoint, body))
        self.run_credits += COSTS[endpoint]
        if endpoint == "/api/v1beta1/tgm/historical-token-ohlcv":
            data = ohlcv()
            if self.never_pumps:
                for c in data["data"]:
                    c["market_cap"]["close"] = 100_000.0
            return data
        if endpoint == "/api/v1/profiler/address/related-wallets":
            wallet = body["wallet_address"]
            funder = [] if wallet in ORGANIC else [_funder_row()]
            return {"data": funder, "pagination": PAGE_LAST}
        return self._trades(body)

    def _trades(self, body: dict[str, Any]) -> dict[str, Any]:
        start = body["date_range"]["from"]
        labels = (body.get("filters") or {}).get("include_labels")
        if labels:
            wallets = {"Fund": ["o0"], "Smart Trader": ["o0", "o1"]}.get(labels[0], [])
            rows = [trade(w, LAUNCH + timedelta(minutes=3)) for w in wallets]
            return {"data": rows, "pagination": PAGE_LAST}
        if start == iso(DECISION_AT):  # the bundle-exit outcome window, after the decision
            rows = [trade(w, DECISION_AT + timedelta(hours=2), "SELL", tokens=4e7) for w in BUNDLE]
            return {"data": rows, "pagination": PAGE_LAST}
        rows = [trade(w, LAUNCH + timedelta(seconds=60), tokens=4e7) for w in BUNDLE]
        rows += [trade(w, LAUNCH + timedelta(seconds=10 * i + 20)) for i, w in enumerate(ORGANIC)]
        if self.plant_future_rows:  # the API misbehaving: rows after the decision
            rows.append(trade("b0", DECISION_AT + timedelta(minutes=1), "SELL", tokens=4e7))
        pagination = (
            {"page": 1, "per_page": 1000, "is_last_page": False}
            if self.trades_never_end
            else PAGE_LAST
        )
        return {"data": rows, "pagination": pagination}


def _funder_row() -> dict[str, Any]:
    return {
        "address": "FUNDER",
        "address_label": None,
        "relation": "First Funder",
        "transaction_hash": "fund-tx",
        "block_timestamp": "2026-09-09T00:00:00Z",
        "order": 1,
        "chain": "solana",
    }
