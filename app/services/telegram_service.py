"""Reliable message delivery to Telegram.

Handles: flood control (RetryAfter), network timeouts (retry with backoff),
HTML formatting errors (falls back to plain text), invalid button URLs, and
users who blocked the bot (raised as UserBlocked so they can be paused).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from app.services.formatter import TELEGRAM_LIMIT, OutMessage

log = logging.getLogger(__name__)


class UserBlocked(Exception):
    pass


def _seconds(v) -> float:
    if isinstance(v, timedelta):
        return v.total_seconds()
    try:
        return float(v)
    except (TypeError, ValueError):
        return 5.0


def _plain(text: str) -> str:
    import html

    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _keyboard(buttons: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(label[:64], url=url)] for label, url in buttons if url.startswith(("http://", "https://"))]
    return InlineKeyboardMarkup(rows) if rows else None


class TelegramSender:
    def __init__(self, bot, pause_between: float = 0.4, attempts: int = 4):
        self.bot = bot
        self.pause_between = pause_between
        self.attempts = attempts

    async def _send_one(self, chat_id: int, msg: OutMessage) -> None:
        text = msg.text if len(msg.text) <= TELEGRAM_LIMIT else msg.text[: TELEGRAM_LIMIT - 20] + "\n…"
        markup = _keyboard(msg.buttons)
        parse_mode: str | None = ParseMode.HTML
        delay = 2.0
        for attempt in range(1, self.attempts + 1):
            try:
                await self.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=markup,
                    disable_notification=msg.silent, link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except RetryAfter as e:
                wait = _seconds(e.retry_after) + 1
                log.warning("telegram flood control: waiting %.0fs", wait)
                await asyncio.sleep(wait)
            except Forbidden as e:
                raise UserBlocked(str(e)) from e
            except BadRequest as e:
                err = str(e).lower()
                if "chat not found" in err:
                    raise UserBlocked(str(e)) from e
                if "parse" in err or "entities" in err or "tag" in err:
                    log.warning("HTML rejected by Telegram (%s); sending as plain text", e)
                    text, parse_mode = _plain(text)[:TELEGRAM_LIMIT], None
                elif "button" in err or "url" in err:
                    log.warning("button rejected (%s); sending without buttons", e)
                    markup = None
                else:
                    raise
            except (TimedOut, NetworkError) as e:
                if attempt == self.attempts:
                    raise
                log.warning("telegram network error (%s), retry %d in %.0fs", e, attempt, delay)
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
        raise NetworkError("telegram delivery failed after retries")

    async def send(self, chat_id: int, messages: Sequence[OutMessage]) -> int:
        """Send messages in order. Returns how many were delivered.
        Raises UserBlocked; other failures stop the sequence and are logged."""
        sent = 0
        for m in messages:
            try:
                await self._send_one(chat_id, m)
                sent += 1
            except UserBlocked:
                raise
            except Exception as e:
                log.error("failed to deliver message to %s: %s", chat_id, e)
                break
            await asyncio.sleep(self.pause_between)
        return sent
