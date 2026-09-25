import json
import logging

from app.logging_config import JsonFormatter, configure_logging, redact


def record(message: str, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("app.test", logging.WARNING, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_logs_are_json_with_extra_fields() -> None:
    line = JsonFormatter().format(record("hello %s", chain="solana", credits=3))
    data = json.loads(line)

    assert data["level"] == "WARNING"
    assert data["logger"] == "app.test"
    assert data["message"] == "hello %s"
    assert data["chain"] == "solana"
    assert data["credits"] == 3
    assert data["time"].endswith("+00:00")


def test_exceptions_are_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())

    data = json.loads(JsonFormatter().format(rec))

    assert "ValueError: boom" in data["exception"]


def test_a_telegram_bot_token_in_a_logged_url_is_redacted() -> None:
    url = "HTTP Request: POST https://api.telegram.org/bot123456:AAE-secret_Token/sendMessage"

    line = JsonFormatter().format(record(url))

    assert "secret" not in line
    assert "bot<redacted>/sendMessage" in line
    assert redact("nothing to hide") == "nothing to hide"


def test_configure_logging_silences_httpx_request_lines() -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level, logging.getLogger("httpx").level)
    try:
        configure_logging("info")

        assert logging.getLogger("httpx").level == logging.WARNING
        assert root.level == logging.INFO
    finally:
        root.handlers[:], root.level = saved[0], saved[1]
        logging.getLogger("httpx").setLevel(saved[2])
