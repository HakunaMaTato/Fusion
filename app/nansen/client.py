import asyncio
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.nansen.cache import TTLCache
from app.nansen.exceptions import BudgetExceeded, MissingFixture, NansenAPIError

NANSEN_BASE_URL = "https://api.nansen.ai"


def _stable_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def fixture_path(endpoint: str, body: dict[str, Any], fixtures_dir: Path) -> Path:
    slug = re.sub(r"^/api/v1(beta1)?/", "", endpoint)
    return fixtures_dir / slug / f"{_stable_hash(body)}.json"


class _RateLimiter:
    def __init__(self, max_per_second: int, max_per_minute: int) -> None:
        self._max_per_second = max_per_second
        self._max_per_minute = max_per_minute
        self._second_window: deque[float] = deque()
        self._minute_window: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._second_window and now - self._second_window[0] >= 1:
                    self._second_window.popleft()
                while self._minute_window and now - self._minute_window[0] >= 60:
                    self._minute_window.popleft()

                if (
                    len(self._second_window) < self._max_per_second
                    and len(self._minute_window) < self._max_per_minute
                ):
                    self._second_window.append(now)
                    self._minute_window.append(now)
                    return

                wait = 0.05
                if len(self._second_window) >= self._max_per_second:
                    wait = max(wait, 1 - (now - self._second_window[0]))
                if len(self._minute_window) >= self._max_per_minute:
                    wait = max(wait, 60 - (now - self._minute_window[0]))
                await asyncio.sleep(wait)


class NansenClient:
    def __init__(
        self,
        settings: Settings,
        *,
        fixtures_dir: Path = Path("tests/fixtures"),
        max_per_second: int = 10,
        max_per_minute: int = 250,
        max_attempts: int = 5,
        timeout_seconds: float = 15.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fixtures_dir = fixtures_dir
        self._max_attempts = max_attempts
        self._http = httpx.AsyncClient(
            base_url=NANSEN_BASE_URL,
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
        )
        self._rate_limiter = _RateLimiter(max_per_second, max_per_minute)
        self._cache = TTLCache()
        self._credits_used_today = 0
        self._credits_reset_date: date = self._clock().date()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "NansenClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @property
    def credits_used_today(self) -> int:
        self._roll_credits_if_new_day()
        return self._credits_used_today

    def restore_credits_used_today(self, value: int) -> None:
        """Seed today's total from storage after a restart."""
        self._roll_credits_if_new_day()
        self._credits_used_today = max(self._credits_used_today, value)

    def _roll_credits_if_new_day(self) -> None:
        today = self._clock().date()
        if today != self._credits_reset_date:
            self._credits_reset_date = today
            self._credits_used_today = 0

    def _check_budget(self) -> None:
        self._roll_credits_if_new_day()
        if self._credits_used_today >= self._settings.daily_credit_budget:
            raise BudgetExceeded(self._credits_used_today, self._settings.daily_credit_budget)

    def _record_credits(self, headers: httpx.Headers) -> None:
        used = headers.get("X-Nansen-Credits-Used")
        if used is None:
            return
        self._roll_credits_if_new_day()
        self._credits_used_today += int(used)

    def _raise_for_error(self, response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise NansenAPIError(
            status=response.status_code,
            message=body.get("message", response.text[:500]),
            code=body.get("code"),
            request_id=body.get("request_id") or response.headers.get("X-Request-Id"),
            body=body,
        )

    def _load_fixture(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        path = fixture_path(endpoint, body, self._fixtures_dir)
        if not path.exists():
            raise MissingFixture(str(path))
        data: dict[str, Any] = json.loads(path.read_text())
        return data

    async def post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        cache_ttl_seconds: float | None = None,
    ) -> dict[str, Any]:
        if self._settings.nansen_mode == "replay":
            return self._load_fixture(endpoint, body)

        cache_key = f"{endpoint}:{_stable_hash(body)}"
        if cache_ttl_seconds is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached  # type: ignore[no-any-return]

        self._check_budget()
        headers = {"apikey": self._settings.nansen_api_key}

        backoff = 1.0
        for attempt in range(1, self._max_attempts + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._http.post(endpoint, json=body, headers=headers)
            except httpx.TransportError:
                if attempt == self._max_attempts:
                    raise
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            self._record_credits(response.headers)

            if response.status_code == 429:
                if attempt == self._max_attempts:
                    self._raise_for_error(response)
                retry_after = float(response.headers.get("Retry-After", backoff))
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                if attempt == self._max_attempts:
                    self._raise_for_error(response)
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if response.status_code >= 400:
                self._raise_for_error(response)

            try:
                data: dict[str, Any] = response.json()
            except ValueError as exc:
                raise NansenAPIError(
                    status=response.status_code,
                    message=f"malformed JSON response: {exc}",
                    request_id=response.headers.get("X-Request-Id"),
                ) from exc
            if cache_ttl_seconds is not None:
                self._cache.set(cache_key, data, cache_ttl_seconds)
            return data

        raise NansenAPIError(status=0, message="exhausted retries without a response")
