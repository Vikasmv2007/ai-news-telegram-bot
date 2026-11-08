from datetime import datetime

import httpx
import pytest
import respx

from app.collectors.base import FeedSpec
from app.collectors.official_sources import is_official_url, tag_official
from app.collectors.rss_collector import build_feed_specs, collect_all, google_news_url, parse_feed

NOW = datetime(2026, 11, 8, 6, 0, 0)
ET = FeedSpec("ET Markets", "https://et.example/rss", "india_market", "india", 2)
GN = FeedSpec("Google News: RBI", "https://news.google.com/rss/search?q=rbi", "india_market", "india", 2, aggregator=True)


def test_parse_publisher_feed(fixture_bytes):
    items = parse_feed(fixture_bytes("et_markets.xml"), ET, now=NOW, max_age_hours=36)
    titles = [a.title for a in items]
    assert "Sensex jumps 800 points as RBI cuts repo rate by 25 bps" in titles
    assert "Old story about markets last month" not in titles  # too old
    assert "Story without a date" not in titles  # never invent a publish time
    first = items[0]
    assert first.source == "ET Markets"
    assert first.category == "india_market"
    assert "<b>" not in first.description and "Sensex rose 800 points" in first.description.replace("  ", " ")
    assert first.published_at == datetime(2026, 11, 8, 1, 0, 0)  # 06:30 IST -> 01:00 UTC
    assert first.url.startswith("https://")


def test_parse_google_news_uses_real_publisher(fixture_bytes):
    items = parse_feed(fixture_bytes("google_news.xml"), GN, now=NOW, max_age_hours=36)
    assert [a.source for a in items] == ["Reuters", "CNBC"]
    assert items[0].title == "RBI cuts repo rate by 25 bps; Sensex jumps 800 points"  # " - Reuters" stripped
    assert items[0].description == ""  # GN description only repeats the headline


def test_invalid_feed_raises(fixture_bytes):
    with pytest.raises(ValueError):
        parse_feed(fixture_bytes("broken.xml"), ET, now=NOW)


def test_official_undated_items_kept():
    xml = b"<rss><channel><item><title>RBI press release</title><link>https://www.rbi.org.in/x</link></item></channel></rss>"
    spec = FeedSpec("RBI", "https://www.rbi.org.in/rss", "india_market", "india", 3, official=True)
    items = parse_feed(xml, spec, now=NOW)
    assert len(items) == 1 and items[0].published_at == NOW and items[0].official


def test_build_feed_specs(sources):
    specs = build_feed_specs(sources)
    assert any(s.official for s in specs)
    gn = [s for s in specs if s.aggregator]
    assert gn and gn[0].url.startswith("https://news.google.com/rss/search?q=")
    assert "+" in google_news_url("a b")


def test_official_tagging():
    from app.collectors.base import RawArticle

    assert is_official_url("https://www.sebi.gov.in/legal/circulars/x.html")
    assert not is_official_url("https://sebi.gov.in.evil.example.com/x")
    r = RawArticle("t", "https://rbi.org.in/a", "Src", NOW, "india_world", "global")
    tag_official(r)
    assert r.official and r.category == "india_market" and r.region == "india"


@respx.mock
async def test_one_failing_feed_does_not_stop_others(fixture_bytes, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    good = FeedSpec("Good", "https://good.example/rss", "india_market", "india", 2)
    bad = FeedSpec("Bad", "https://bad.example/rss", "tech", "global", 2)
    notfound = FeedSpec("Gone", "https://gone.example/rss", "tech", "global", 2)
    timeout = FeedSpec("Slow", "https://slow.example/rss", "tech", "global", 2)
    respx.get(good.url).mock(return_value=httpx.Response(200, content=fixture_bytes("et_markets.xml")))
    bad_route = respx.get(bad.url).mock(return_value=httpx.Response(503))
    respx.get(notfound.url).mock(return_value=httpx.Response(404))
    respx.get(timeout.url).mock(side_effect=httpx.ConnectTimeout("t"))
    async with httpx.AsyncClient() as client:
        results = await collect_all(client, [good, bad, notfound, timeout], max_age_hours=24 * 365 * 5)
    by = {r.feed.name: r for r in results}
    assert by["Good"].ok and len(by["Good"].articles) >= 3
    assert not by["Bad"].ok and bad_route.call_count == 3  # retried with backoff
    assert by["Gone"].error == "HTTP 404"
    assert "ConnectTimeout" in by["Slow"].error or "network" in by["Slow"].error


async def _no_sleep(*_a, **_k):
    return None
