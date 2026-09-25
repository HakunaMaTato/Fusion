import html
import logging
from typing import Protocol
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_REASONS = 3
MAX_REASON_CHARS = 200
MAX_MESSAGE_CHARS = 4000


class Notifier(Protocol):
    async def send(self, text: str) -> bool: ...


def format_alert(
    *,
    chain: str,
    symbol: str,
    token_address: str,
    verdict: str,
    detail: str,
    reasons: list[str],
    dashboard_base_url: str,
) -> str:
    """HTML message for Telegram. Token names and symbols are attacker-controlled, so escape all."""
    lines = [
        f"<b>{html.escape(verdict)}</b> {html.escape(symbol)[:64]} on {html.escape(chain)}",
        html.escape(detail),
        f"<code>{html.escape(token_address)}</code>",
    ]
    lines.extend(f"- {html.escape(reason[:MAX_REASON_CHARS])}" for reason in reasons[:MAX_REASONS])
    if dashboard_base_url:
        url = f"{dashboard_base_url.rstrip('/')}/token/{quote(chain)}/{quote(token_address)}"
        lines.append(f'<a href="{html.escape(url, quote=True)}">Open in dashboard</a>')
    return "\n".join(lines)[:MAX_MESSAGE_CHARS]


class LogNotifier:
    """Used when Telegram is not configured: the alert is logged instead of sent."""

    async def send(self, text: str) -> bool:
        logger.info("alert (telegram not configured)", extra={"alert": text})
        return True


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, http: httpx.AsyncClient | None = None) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._http = http or httpx.AsyncClient(timeout=10.0)

    async def send(self, text: str) -> bool:
        # Never log the URL or the exception text: both can contain the bot token.
        try:
            response = await self._http.post(
                f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("telegram send failed", extra={"error_type": type(exc).__name__})
            return False
        if response.status_code != 200:
            logger.warning("telegram send failed", extra={"status": response.status_code})
            return False
        return True

    async def aclose(self) -> None:
        await self._http.aclose()


def build_notifier(settings: Settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return LogNotifier()
