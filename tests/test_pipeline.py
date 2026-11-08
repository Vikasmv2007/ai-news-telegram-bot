"""End-to-end pipeline tests (feeds mocked with respx; no real network)."""
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.ai.providers import FakeProvider, ProviderRateLimited
from app.ai.summarizer import Summarizer
from app.collectors.base import FeedSpec
from app.services.container import create_services
from app.services.health import health_report
from app.services.news_service import NewsService
from tests.test_summarizer import good_response


async def test_test_mode_refresh_is_idempotent(settings):
    services = create_services(replace(settings, test_mode=True))
    s1 = await services.news.refresh()
    assert s1.new == 11 and s1.low_value == 1 and s1.analyzed_ai == 11 and not s1.errors
    s2 = await services.news.refresh()
    assert s2.new == 0 and s2.analyzed_ai == 0  # nothing processed or stored twice
    code, body = health_report(services)
    assert code == 200 and body["database"] == "ok" and body["test_mode"] is True
    await services.close()


@respx.mock
async def test_real_feed_pipeline_with_dedup_and_fallback(settings, db, sources, fixture_bytes, monkeypatch):
    # Pin "now" close to the fixture dates so the age filter keeps them.
    fixed_now = datetime(2026, 11, 8, 6, 0)
    monkeypatch.setattr("app.collectors.rss_collector.datetime", _FrozenDT(fixed_now))
    monkeypatch.setattr("app.services.news_service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.services.deduplication.utcnow", lambda: fixed_now)
    feeds = [FeedSpec("ET Markets", "https://et.example/rss", "india_market", "india", 2),
             FeedSpec("Google News: RBI", "https://news.google.com/rss/search?q=rbi", "india_market", "india", 2, aggregator=True),
             FeedSpec("Broken", "https://broken.example/rss", "tech", "global", 1)]
    respx.get(feeds[0].url).mock(return_value=httpx.Response(200, content=fixture_bytes("et_markets.xml")))
    respx.get(feeds[1].url).mock(return_value=httpx.Response(200, content=fixture_bytes("google_news.xml")))
    respx.get(feeds[2].url).mock(return_value=httpx.Response(200, content=fixture_bytes("broken.xml")))

    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        raise ProviderRateLimited("quota", retry_after=10)

    async with httpx.AsyncClient() as client:
        news = NewsService(settings, db, client, Summarizer([FakeProvider(flaky)]), sources, feeds, market=None)
        stats = await news.collect()
        assert stats.feeds_ok == 2 and stats.feeds_failed == 1  # broken feed isolated
        assert stats.low_value == 1  # "Top 5 stocks to buy" dropped
        assert stats.duplicates == 1  # Reuters (via Google News) copy of the RBI story
        rbi = [a for a in db.processed_articles(since=datetime(2000, 1, 1), limit=50)]
        assert rbi == []  # nothing processed yet
        statuses = {f.name: f.consecutive_failures for f in db.feed_statuses()}
        assert statuses["Broken"] == 1 and statuses["ET Markets"] == 0

        # AI unavailable: items stay pending and are retried, then fall back to feed text
        await news.process()
        assert db.processed_articles(since=datetime(2000, 1, 1), limit=50) == []
        news.summarizer = Summarizer([FakeProvider(good_response)])
        stats = await news.process()
        stored = db.processed_articles(since=datetime(2000, 1, 1), limit=50)
        assert stats.analyzed_ai == len(stored) == 3
        original = next(a for a in stored if "repo rate" in a.title)
        assert original.coverage_count == 2
        tcs = next(a for a in stored if a.title.startswith("TCS"))
        assert "TCS.NS" in tcs.matched_symbols and "INFY.NS" in tcs.matched_symbols


@respx.mock
async def test_no_ai_key_uses_feed_text(settings, db, sources, fixture_bytes, monkeypatch):
    fixed_now = datetime(2026, 11, 8, 6, 0)
    monkeypatch.setattr("app.collectors.rss_collector.datetime", _FrozenDT(fixed_now))
    monkeypatch.setattr("app.services.news_service.utcnow", lambda: fixed_now)
    feed = FeedSpec("ET Markets", "https://et.example/rss", "india_market", "india", 2)
    respx.get(feed.url).mock(return_value=httpx.Response(200, content=fixture_bytes("et_markets.xml")))
    async with httpx.AsyncClient() as client:
        news = NewsService(settings, db, client, Summarizer([]), sources, [feed], market=None)
        stats = await news.refresh()
    assert stats.analyzed_fallback == 2 and stats.analyzed_ai == 0
    rows = db.processed_articles(since=datetime(2000, 1, 1), limit=10)
    assert all(r.ai_provider == "none" and r.why_it_matters == "" for r in rows)


def test_cli_check_config_and_preview(monkeypatch, capsys, tmp_path):
    from app import main as m

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    assert m.main(["--check-config"]) == 1
    assert "TELEGRAM_BOT_TOKEN is not set" in capsys.readouterr().out
    assert m.main(["--preview", "morning", "--test-mode"]) == 0
    out = capsys.readouterr().out
    assert "GOOD MORNING" in out and "Read Full Article" in out and "Disclaimer" in out
    assert m.main([]) == 2  # refuses to start without a token


def test_invalid_env_values(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("SCHEDULE_MORNING", "25:99")
    with pytest.raises(ValueError):
        load_settings()
    monkeypatch.setenv("SCHEDULE_MORNING", "07:15")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "12, 34")
    s = load_settings()
    assert s.slot("morning").time == "07:15" and s.allowed_chat_ids == [12, 34] and s.admin_chat_ids == [12, 34]


class _FrozenDT:
    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now.replace(tzinfo=timezone.utc) if tz else self._now

    def __getattr__(self, name):
        return getattr(datetime, name)


class _FakeBot:
    sent: list = []

    def __init__(self, token):
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, **kw):
        _FakeBot.sent.append(kw)


async def test_run_slot_one_shot_mode(settings, monkeypatch):
    """--run-slot (cron / GitHub Actions): sends once, second run is a no-op."""
    import telegram

    from app import main as m

    monkeypatch.setattr(telegram, "Bot", _FakeBot)
    s = replace(settings, test_mode=True)
    _FakeBot.sent = []
    assert await m.cmd_run_slot(s, "evening") == 0
    first = len(_FakeBot.sent)
    assert first > 3
    assert {k["chat_id"] for k in _FakeBot.sent} == {111, 222}
    texts = [k["text"] for k in _FakeBot.sent]
    assert any("EVENING DIGEST" in t for t in texts) and any("BREAKING" in t for t in texts)
    assert all(k["parse_mode"] == "HTML" for k in _FakeBot.sent)
    assert any(k["reply_markup"] is not None for k in _FakeBot.sent)
    assert await m.cmd_run_slot(s, "evening") == 0
    assert len(_FakeBot.sent) == first  # restart-safe: nothing sent twice


def test_ai_budget_is_balanced_across_categories():
    from app.services.news_service import balance_by_category
    from tests.conftest import make_article

    arts = [make_article(i, category="india_market") for i in range(10)] + [make_article(100 + i, category="tech") for i in range(2)]
    picked = balance_by_category(arts, 6)
    assert len(picked) == 6 and sum(a.category == "tech" for a in picked) == 2


@pytest.mark.parametrize("view,needle", [("india", "INDIAN NEWS"), ("global", "INTERNATIONAL NEWS"), ("tech", "TECHNOLOGY"),
                                          ("market", "MARKET SNAPSHOT"), ("trending", "TRENDING"), ("today", "TODAY'S COMPLETE"),
                                          ("now", "LATEST NEWS")])
async def test_send_view_one_shot(settings, monkeypatch, view, needle):
    """--send VIEW (GitHub "Run workflow" menu) delivers that view to every allowed chat."""
    import telegram

    from app import main as m

    monkeypatch.setattr(telegram, "Bot", _FakeBot)
    _FakeBot.sent = []
    assert await m.cmd_send_view(replace(settings, test_mode=True), view) == 0
    texts = "\n".join(k["text"] for k in _FakeBot.sent)
    assert needle in texts and {k["chat_id"] for k in _FakeBot.sent} == {111, 222}


async def test_send_unknown_view(settings):
    from app import main as m

    assert await m.cmd_send_view(replace(settings, test_mode=True), "sports") == 2


async def test_manual_force_resends_briefing(settings, monkeypatch):
    import telegram

    from app import main as m

    monkeypatch.setattr(telegram, "Bot", _FakeBot)
    s = replace(settings, test_mode=True)
    _FakeBot.sent = []
    await m.cmd_run_slot(s, "morning")
    first = len(_FakeBot.sent)
    await m.cmd_run_slot(s, "morning")  # scheduled duplicate -> nothing
    assert len(_FakeBot.sent) == first
    await m.cmd_run_slot(s, "morning", force=True)  # manual "Run workflow" -> sent again
    assert len(_FakeBot.sent) > first
    assert "GOOD MORNING" in _FakeBot.sent[first]["text"]
