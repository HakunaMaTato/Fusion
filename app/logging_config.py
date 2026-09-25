import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


# Telegram puts the bot token in the URL path; redact it from anything that gets logged.
_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    return _BOT_TOKEN.sub("bot<redacted>", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO, which would include the Telegram bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
