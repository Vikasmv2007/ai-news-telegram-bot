import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut

from app.services.formatter import OutMessage
from app.services.telegram_service import TelegramSender, UserBlocked


class Bot:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    async def send_message(self, **kw):
        self.calls.append(kw)
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(*a, **k):
        pass
    monkeypatch.setattr("asyncio.sleep", fast)


def msg(text="<b>Hi</b> &amp; bye", buttons=(("🔗 Read Full Article", "https://example.com/a"),)):
    return OutMessage(text, list(buttons))


async def test_happy_path_and_options():
    bot = Bot([None])
    assert await TelegramSender(bot).send(1, [msg()]) == 1
    kw = bot.calls[0]
    assert kw["parse_mode"] == "HTML" and kw["disable_notification"] is True
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://example.com/a"
    assert kw["link_preview_options"].is_disabled


async def test_flood_control_waits_and_retries():
    bot = Bot([RetryAfter(3), None])
    assert await TelegramSender(bot).send(1, [msg()]) == 1 and len(bot.calls) == 2


async def test_bad_html_falls_back_to_plain_text():
    bot = Bot([BadRequest("Can't parse entities: unclosed tag"), None])
    assert await TelegramSender(bot).send(1, [msg()]) == 1
    assert bot.calls[1]["parse_mode"] is None and bot.calls[1]["text"] == "Hi & bye"


async def test_network_retries_then_gives_up():
    bot = Bot([TimedOut(), None])
    assert await TelegramSender(bot).send(1, [msg(), msg()]) == 2
    bot = Bot([TimedOut()] * 10)
    assert await TelegramSender(bot, attempts=3).send(1, [msg(), msg()]) == 0  # stops the sequence, no crash


async def test_blocked_user_raises():
    with pytest.raises(UserBlocked):
        await TelegramSender(Bot([Forbidden("bot was blocked by the user")])).send(1, [msg()])


async def test_long_text_truncated_and_bad_urls_dropped():
    bot = Bot([None])
    await TelegramSender(bot).send(1, [msg("x" * 5000, buttons=(("bad", "javascript:alert(1)"),))])
    assert len(bot.calls[0]["text"]) <= 4096 and bot.calls[0]["reply_markup"] is None
