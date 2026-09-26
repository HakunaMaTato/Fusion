import json
from pathlib import Path

import httpx
import pytest
import respx

from app.config import Settings
from app.nansen.client import NANSEN_BASE_URL, NansenClient, fixture_path
from app.nansen.exceptions import BudgetExceeded, MissingFixture, NansenAPIError

ENDPOINT = "/api/v1/token-screener"
BODY = {"chains": ["solana"], "timeframe": "1h"}
SUCCESS_PAYLOAD = {"data": [], "pagination": {"page": 1, "per_page": 10, "is_last_page": True}}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "nansen_api_key": "test-key",
        "nansen_mode": "live",
        "daily_credit_budget": 100,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.nansen.client.asyncio.sleep", _sleep)


@pytest.mark.asyncio
async def test_success_tracks_credits(tmp_path: Path) -> None:
    with respx.mock:
        respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(
                200, json=SUCCESS_PAYLOAD, headers={"X-Nansen-Credits-Used": "1"}
            )
        )
        client = NansenClient(_settings(), fixtures_dir=tmp_path)
        try:
            data = await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert data == SUCCESS_PAYLOAD
    assert client.credits_used_today == 1


@pytest.mark.asyncio
async def test_429_honours_retry_after_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_sleep(monkeypatch)
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}")
        route.side_effect = [
            httpx.Response(
                429,
                json={
                    "error": "Too Many Requests",
                    "message": "slow down",
                    "code": "rate_limit_exceeded",
                    "status": 429,
                    "request_id": "r1",
                    "doc_url": "https://docs.nansen.ai/x",
                    "retry_after": 2,
                },
                headers={"Retry-After": "2"},
            ),
            httpx.Response(200, json=SUCCESS_PAYLOAD),
        ]
        client = NansenClient(_settings(), fixtures_dir=tmp_path)
        try:
            data = await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert data == SUCCESS_PAYLOAD
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_5xx_retries_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}")
        unavailable_body = {
            "error": "e",
            "message": "down",
            "code": "upstream_unavailable",
            "status": 503,
            "request_id": None,
            "doc_url": "x",
        }
        route.side_effect = [
            httpx.Response(503, json=unavailable_body),
            httpx.Response(200, json=SUCCESS_PAYLOAD),
        ]
        client = NansenClient(_settings(), fixtures_dir=tmp_path)
        try:
            data = await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert data == SUCCESS_PAYLOAD
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_exhausted_retries_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    with respx.mock:
        respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(
                500,
                json={
                    "error": "e",
                    "message": "boom",
                    "code": "internal_error",
                    "status": 500,
                    "request_id": "r2",
                    "doc_url": "x",
                },
            )
        )
        client = NansenClient(_settings(), fixtures_dir=tmp_path, max_attempts=3)
        try:
            with pytest.raises(NansenAPIError) as exc_info:
                await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert exc_info.value.status == 500
    assert exc_info.value.code == "internal_error"


@pytest.mark.asyncio
async def test_budget_exceeded_blocks_call_without_network(tmp_path: Path) -> None:
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}")
        client = NansenClient(_settings(daily_credit_budget=0), fixtures_dir=tmp_path)
        try:
            with pytest.raises(BudgetExceeded):
                await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_cache_hit_skips_second_network_call(tmp_path: Path) -> None:
    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(200, json=SUCCESS_PAYLOAD)
        )
        client = NansenClient(_settings(), fixtures_dir=tmp_path)
        try:
            first = await client.post(ENDPOINT, BODY, cache_ttl_seconds=60)
            second = await client.post(ENDPOINT, BODY, cache_ttl_seconds=60)
        finally:
            await client.aclose()

    assert first == second == SUCCESS_PAYLOAD
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_replay_mode_returns_fixture_without_network(tmp_path: Path) -> None:
    path = fixture_path(ENDPOINT, BODY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(SUCCESS_PAYLOAD))

    with respx.mock:
        route = respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}")
        client = NansenClient(_settings(nansen_mode="replay"), fixtures_dir=tmp_path)
        try:
            data = await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()

    assert data == SUCCESS_PAYLOAD
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_malformed_json_response_raises_clearly(tmp_path: Path) -> None:
    with respx.mock:
        respx.post(f"{NANSEN_BASE_URL}{ENDPOINT}").mock(
            return_value=httpx.Response(
                200, content=b"not json", headers={"Content-Type": "application/json"}
            )
        )
        client = NansenClient(_settings(), fixtures_dir=tmp_path)
        try:
            with pytest.raises(NansenAPIError):
                await client.post(ENDPOINT, BODY)
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_replay_mode_missing_fixture_raises_clearly(tmp_path: Path) -> None:
    client = NansenClient(_settings(nansen_mode="replay"), fixtures_dir=tmp_path)
    try:
        with pytest.raises(MissingFixture):
            await client.post(ENDPOINT, BODY)
    finally:
        await client.aclose()
