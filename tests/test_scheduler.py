from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.container import create_services
from app.services.scheduler import DeliveryScheduler, in_quiet_hours
from tests.conftest import FakeSender

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
async def env(settings):
    s = replace(settings, test_mode=True)
    services = create_services(s)
    for cid in s.allowed_chat_ids:
        services.db.get_or_create_user(cid)
    sender = FakeSender()
    sched = DeliveryScheduler(s, services.db, services.news, services.briefing, sender)
    yield services, sched, sender
    await services.close()


def weekday_now():
    now = datetime.now(timezone.utc)
    while now.astimezone(IST).weekday() >= 5:
        now -= timedelta(days=1)
    return now


async def test_slot_sent_once_even_if_triggered_twice(env):
    services, sched, sender = env
    now = datetime.now(timezone.utc)
    first = await sched.run_slot("morning", now)
    assert first == {111: "sent", 222: "sent"}
    msgs = sender.for_chat(111)
    assert "GOOD MORNING" in msgs[0].text and "Disclaimer" in msgs[-1].text
    second = await sched.run_slot("morning", now)  # e.g. restart + catch-up
    assert second == {111: "already-sent", 222: "already-sent"}
    assert len(sender.for_chat(111)) == len(msgs)


async def test_articles_not_repeated_across_briefings(env):
    services, sched, sender = env
    now = datetime.now(timezone.utc)
    await sched.run_slot("morning", now)
    morning_urls = {b[1] for m in sender.for_chat(111) for b in m.buttons}
    sender.sent.clear()
    await sched.run_slot("evening", now)
    evening_urls = {b[1] for m in sender.for_chat(111) for b in m.buttons}
    assert morning_urls and not (morning_urls & evening_urls)


async def test_market_slots_skip_weekends(env):
    services, sched, sender = env
    saturday = datetime(2026, 11, 7, 3, 30, tzinfo=timezone.utc)  # 09:00 IST Saturday
    assert await sched.run_slot("premarket", saturday) == {}
    assert sender.sent == []


async def test_skip_if_empty_and_user_preferences(env):
    services, sched, sender = env
    now = weekday_now()
    services.db.update_user(222, disabled_slots="midday")
    await services.news.refresh()
    services.db.record_deliveries(111, [a.id for a in services.db.processed_articles(since=datetime(2000, 1, 1), limit=100)], "morning")
    res = await sched.run_slot("midday", now)
    assert res == {111: "skipped"}  # nothing new -> no notification; 222 opted out
    assert sender.sent == []


async def test_paused_and_blocked_users(env):
    services, sched, _ = env
    services.db.update_user(222, is_paused=True)
    sched.sender = FakeSender(block={111})
    res = await sched.run_slot("morning", datetime.now(timezone.utc))
    assert res == {111: "blocked"}
    assert services.db.get_user(111).is_paused is True


async def test_failed_delivery_releases_claim_for_retry(env):
    services, sched, _ = env
    sched.sender = FakeSender(fail_for={111})
    now = datetime.now(timezone.utc)
    res = await sched.run_slot("evening", now)
    assert res[111] == "failed" and res[222] == "sent"
    sched.sender = FakeSender()
    res2 = await sched.run_slot("evening", now)
    assert res2 == {111: "sent", 222: "already-sent"}


async def test_breaking_alerts_once_and_limited(env):
    services, sched, sender = env
    await services.news.refresh()
    now = datetime.now(timezone.utc)
    n = await sched.breaking_check(now)
    assert n == 1  # only the importance>=9 sample story
    assert "BREAKING" in sender.for_chat(111)[0].text
    assert await sched.breaking_check(now) == 0  # never re-alerted
    sched.settings = replace(sched.settings, breaking_alerts_enabled=False)
    assert await sched.breaking_check(now) == 0


async def test_breaking_respects_quiet_hours(env):
    services, sched, sender = env
    await services.news.refresh()
    sched.settings = replace(sched.settings, quiet_hours="00:00-23:59")
    assert await sched.breaking_check(datetime.now(timezone.utc)) == 0 and sender.sent == []


def test_quiet_hours_parsing():
    late = datetime(2026, 11, 8, 23, 30, tzinfo=IST)
    noon = datetime(2026, 11, 8, 12, 0, tzinfo=IST)
    early = datetime(2026, 11, 8, 5, 0, tzinfo=IST)
    assert in_quiet_hours(late, "23:00-06:30") and in_quiet_hours(early, "23:00-06:30")
    assert not in_quiet_hours(noon, "23:00-06:30") and not in_quiet_hours(late, "")


async def test_catch_up_runs_recently_missed_slot(env):
    services, sched, sender = env
    now = datetime.now(IST).replace(hour=20, minute=20, second=0, microsecond=0).astimezone(timezone.utc)
    ran = await sched.catch_up(now)
    assert ran == ["evening"]
    later = datetime.now(IST).replace(hour=22, minute=0).astimezone(timezone.utc)
    assert await sched.catch_up(later) == []  # too late, don't send stale briefings


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, cb, when, **kw):
        self.jobs.append(("once", kw.get("name"), when))

    def run_repeating(self, cb, interval, first, **kw):
        self.jobs.append(("repeating", kw.get("name"), interval))

    def run_daily(self, cb, time, **kw):
        self.jobs.append(("daily", kw.get("name"), time))


async def test_register_jobs_in_ist(env):
    services, sched, _ = env
    sched.settings = replace(sched.settings, slots=tuple(replace(s, enabled_by_default=(s.key != "midday")) for s in sched.settings.slots))
    jq = FakeJobQueue()
    sched.register(jq)
    daily = {name: t for kind, name, t in jq.jobs if kind == "daily"}
    assert set(daily) == {"slot:morning", "slot:premarket", "slot:closing", "slot:evening", "purge"}
    assert daily["slot:morning"].hour == 7 and str(daily["slot:morning"].tzinfo) == "Asia/Kolkata"
    assert any(k == "repeating" and n == "collect" and i == 30 * 60 for k, n, i in jq.jobs)
