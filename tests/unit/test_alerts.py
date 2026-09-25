import json
import logging

import httpx
import pytest

from app.alerts.telegram import (
    LogNotifier,
    TelegramNotifier,
    build_notifier,
    format_alert,
)
from app.config import Settings

TOKEN = "123456:SECRET-bot-token"


def message(**overrides: object) -> str:
    args: dict[str, object] = {
        "chain": "solana",
        "symbol": "TOK",
        "token_address": "Addr123",
        "verdict": "GREEN",
        "detail": "new GREEN candidate",
        "reasons": ["r1", "r2", "r3", "r4"],
        "dashboard_base_url": "https://radar.example.com/",
    }
    args.update(overrides)
    return format_alert(**args)  # type: ignore[arg-type]


def test_message_has_the_required_parts_and_only_three_reasons() -> None:
    text = message()

    assert "<b>GREEN</b> TOK on solana" in text
    assert "new GREEN candidate" in text
    assert "<code>Addr123</code>" in text
    assert "- r1" in text and "- r3" in text and "r4" not in text
    assert 'href="https://radar.example.com/token/solana/Addr123"' in text


def test_attacker_controlled_names_are_escaped() -> None:
    text = message(symbol='<script>alert("x")</script>&', reasons=["<b>bold</b>"])

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;bold&lt;/b&gt;" in text


def test_the_link_is_omitted_without_a_dashboard_url() -> None:
    assert "href" not in message(dashboard_base_url="")


def test_a_hostile_address_cannot_break_out_of_the_link() -> None:
    text = message(token_address='"><script>x</script>')

    assert "<script>" not in text
    assert '"><' not in text


def test_the_message_is_length_capped() -> None:
    text = message(reasons=["x" * 5000] * 3, symbol="S" * 5000)

    assert len(text) <= 4000


@pytest.mark.asyncio
async def test_telegram_notifier_posts_html_and_returns_true() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        TOKEN, "42", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        assert await notifier.send("hello <b>x</b>") is True
    finally:
        await notifier.aclose()

    body = json.loads(seen[0].content)
    assert body == {
        "chat_id": "42",
        "text": "hello <b>x</b>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"


@pytest.mark.asyncio
async def test_a_telegram_error_status_returns_false_without_leaking_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False})

    notifier = TelegramNotifier(
        TOKEN, "42", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        caplog.set_level(logging.WARNING, logger="httpx")  # what configure_logging does
        with caplog.at_level(logging.DEBUG):
            assert await notifier.send("x") is False
    finally:
        await notifier.aclose()

    assert "SECRET" not in caplog.text
    assert [r.status for r in caplog.records if hasattr(r, "status")] == [401]


@pytest.mark.asyncio
async def test_a_network_error_returns_false_without_leaking_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    notifier = TelegramNotifier(
        TOKEN, "42", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        caplog.set_level(logging.WARNING, logger="httpx")  # what configure_logging does
        with caplog.at_level(logging.DEBUG):
            assert await notifier.send("x") is False
    finally:
        await notifier.aclose()

    assert "SECRET" not in caplog.text
    assert [r.error_type for r in caplog.records if hasattr(r, "error_type")] == ["ConnectError"]


@pytest.mark.asyncio
async def test_log_notifier_always_succeeds() -> None:
    assert await LogNotifier().send("x") is True


def test_build_notifier_needs_both_token_and_chat_id() -> None:
    def settings(**kwargs: str) -> Settings:
        return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]

    assert isinstance(build_notifier(settings()), LogNotifier)
    assert isinstance(build_notifier(settings(telegram_bot_token="t")), LogNotifier)
    assert isinstance(
        build_notifier(settings(telegram_bot_token="t", telegram_chat_id="1")), TelegramNotifier
    )
