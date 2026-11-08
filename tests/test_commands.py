from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.bot.commands import BotCommands
from app.services.container import create_services
from app.services.scheduler import DeliveryScheduler
from tests.conftest import FakeSender


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))


class FakeQuery:
    def __init__(self, data):
        self.data, self.answers, self.edits = data, [], []

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw))

    async def edit_message_reply_markup(self, **kw):
        self.edits.append(("markup", kw))


def update(chat_id=111, query=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_user=SimpleNamespace(username="tester", first_name="T"),
                           effective_message=FakeMessage(), callback_query=query)


def ctx(*args):
    return SimpleNamespace(args=list(args))


@pytest.fixture
async def bot(settings):
    s = replace(settings, test_mode=True)
    services = create_services(s)
    sender = FakeSender()
    sched = DeliveryScheduler(s, services.db, services.news, services.briefing, sender)
    yield BotCommands(services, sender, sched), services, sender
    await services.close()


async def test_unauthorised_users_are_refused(bot):
    cmds, services, sender = bot
    for handler in (cmds.start, cmds.now, cmds.today, cmds.status, cmds.watchlist):
        u = update(chat_id=999)
        await handler(u, ctx())
        assert "private bot" in u.effective_message.replies[0][0] and "999" in u.effective_message.replies[0][0]
    assert sender.sent == [] and services.db.get_user(999) is None


async def test_admin_only_commands(bot):
    cmds, _, _ = bot
    u = update(chat_id=222)  # allowed, but not admin
    await cmds.status(u, ctx())
    assert "private bot" in u.effective_message.replies[0][0]
    u = update(chat_id=111)
    await cmds.status(u, ctx())
    assert "Bot status" in u.effective_message.replies[0][0]


async def test_start_and_help(bot):
    cmds, services, _ = bot
    u = update()
    await cmds.start(u, ctx())
    text = u.effective_message.replies[0][0]
    assert "/market" in text and "07:00" in text and "Not financial advice" in text
    assert services.db.get_user(111) is not None


async def test_now_never_repeats_then_up_to_date(bot):
    cmds, _, sender = bot
    await cmds.now(update(), ctx())
    first = sender.for_chat(111)
    assert first and "LATEST NEWS" in first[0].text
    seen: list[str] = []
    for _ in range(5):
        before = len(sender.sent)
        u = update()
        await cmds.now(u, ctx())
        if len(sender.sent) == before:
            assert any("up to date" in r[0] for r in u.effective_message.replies)
            break
    else:
        pytest.fail("never reached 'up to date'")
    urls = [b[1] for m in sender.for_chat(111) for b in m.buttons]
    assert len(urls) == len(set(urls))  # no story sent twice


async def test_category_commands(bot):
    cmds, _, sender = bot
    await cmds.today(update(), ctx())
    assert "TODAY'S COMPLETE BRIEFING" in sender.for_chat(111)[0].text
    for handler, needle in ((cmds.market, "MARKET SNAPSHOT"), (cmds.tech, "TECHNOLOGY"), (cmds.india, "INDIAN NEWS"),
                            (cmds.global_, "INTERNATIONAL NEWS"), (cmds.trending, "TRENDING")):
        sender.sent.clear()
        await handler(update(), ctx())
        assert needle in "\n".join(m.text for m in sender.for_chat(111)), needle


async def test_watchlist_validation_and_update(bot):
    cmds, services, sender = bot
    u = update()
    await cmds.watchlist(u, ctx("add", "INFY.NS", "bad;symbol"))
    assert "Invalid symbol" in u.effective_message.replies[0][0]
    u = update()
    await cmds.watchlist(u, ctx("add", "infy.ns", "WIPRO.NS"))
    assert services.db.get_user(111).watchlist_list == ["TCS.NS", "INFY.NS", "WIPRO.NS"]
    await cmds.watchlist(update(), ctx("remove", "TCS.NS"))
    assert services.db.get_user(111).watchlist_list == ["INFY.NS", "WIPRO.NS"]
    await cmds.watchlist(update(), ctx())
    assert "YOUR WATCHLIST" in sender.for_chat(111)[0].text
    u = update()
    await cmds.watchlist(u, ctx("explode"))
    assert "Usage" in u.effective_message.replies[0][0]


async def test_stop_resume(bot):
    cmds, services, _ = bot
    await cmds.stop(update(), ctx())
    assert services.db.get_user(111).is_paused
    await cmds.resume(update(), ctx())
    assert not services.db.get_user(111).is_paused


async def test_settings_toggles(bot):
    cmds, services, _ = bot
    u = update()
    await cmds.settings_cmd(u, ctx())
    assert u.effective_message.replies[0][1]["reply_markup"] is not None
    for data in ("set:slot:midday", "set:cat:tech", "set:alerts", "set:format"):
        q = FakeQuery(data)
        await cmds.settings_callback(update(query=q), ctx())
        assert q.answers == ["Updated"]
    user = services.db.get_user(111)
    assert user.disabled_slot_set == {"midday"} and user.disabled_category_set == {"tech"}
    assert user.breaking_alerts is False and user.message_format == "compact"
    q = FakeQuery("set:slot:notaslot")
    await cmds.settings_callback(update(query=q), ctx())
    assert q.answers == ["Unknown option"]
    q = FakeQuery("set:alerts")
    await cmds.settings_callback(update(chat_id=999, query=q), ctx())
    assert q.answers == ["Not authorised"]


async def test_preview_and_format_test(bot):
    cmds, _, sender = bot
    await cmds.preview(update(), ctx("evening"))
    assert "EVENING DIGEST" in sender.for_chat(111)[0].text
    sender.sent.clear()
    await cmds.test(update(), ctx())
    assert "FORMAT TEST" in sender.for_chat(111)[0].text


async def test_command_errors_are_caught(bot, monkeypatch):
    cmds, services, _ = bot

    async def boom(*a, **k):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(services.briefing, "today", boom)
    u = update()
    await cmds.today(u, ctx())
    assert "Something went wrong" in u.effective_message.replies[-1][0]
