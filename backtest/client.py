"""A Nansen client for backtests: responses are cached on disk and the run has a credit cap.

A cached request costs nothing, so re-running an analysis or tuning scoring.yaml after the data has
been fetched spends no credits.
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.nansen.client import NansenClient, fixture_path
from app.nansen.exceptions import BudgetExceeded

CACHE_DIR = Path(__file__).parent / "cache"

COSTS = {
    "/api/v1beta1/token-screener/historical": 5,
    "/api/v1beta1/tgm/historical-dex-trades": 5,
    "/api/v1beta1/tgm/historical-token-ohlcv": 5,
    "/api/v1/profiler/address/related-wallets": 1,
    "/api/v1/tgm/token-information": 1,
}
HISTORICAL_TIMEOUT_SECONDS = 90.0  # the historical endpoints are much slower than the live ones
UNKNOWN_ENDPOINT_COST = 25  # assume the dearest tier for an endpoint not in the table


class BacktestClient(NansenClient):
    def __init__(
        self, settings: Settings, *, max_credits: int, cache_dir: Path = CACHE_DIR
    ) -> None:
        # The daily budget is replaced by the run cap below; the base guard must never trip first.
        super().__init__(
            settings.model_copy(update={"daily_credit_budget": 10**9, "nansen_mode": "live"}),
            timeout_seconds=HISTORICAL_TIMEOUT_SECONDS,
        )
        self.cache_dir = cache_dir
        self.max_credits = max_credits
        self.run_credits = 0
        self.live_calls = 0
        self.cache_hits = 0

    def _record_credits(self, headers: httpx.Headers) -> None:
        used = headers.get("X-Nansen-Credits-Used")
        if used is not None:
            self.run_credits += int(used)
        super()._record_credits(headers)

    async def post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        cache_ttl_seconds: float | None = None,
    ) -> dict[str, Any]:
        path = fixture_path(endpoint, body, self.cache_dir)
        if path.exists():
            self.cache_hits += 1
            cached: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return cached
        cost = COSTS.get(endpoint, UNKNOWN_ENDPOINT_COST)
        if self.run_credits + cost > self.max_credits:
            raise BudgetExceeded(self.run_credits, self.max_credits)
        data = await super().post(endpoint, body, cache_ttl_seconds=cache_ttl_seconds)
        self.live_calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return data
