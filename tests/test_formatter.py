from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.collectors.market_collector import Quote
from app.services.formatter import (
    DISCLAIMER, TELEGRAM_LIMIT, build_breaking, build_briefing, format_card, format_compact, format_snapshot,
)
from tests.conftest import make_article

IST = ZoneInfo("Asia/Kolkata")


def test_card_has_all_required_fields():
    a = make_article(1, simple_headline="RBI cuts <rate> & more", importance=9, importance_level="HIGH", coverage_count=3, is_official=True)
    m = format_card(a, 1, IST, section_header="📈 INDIAN STOCK MARKET")
    t = m.text
    for needle in ("📈 INDIAN STOCK MARKET", "1. RBI cuts &lt;rate&gt; &amp; more", "📝 <b>Summary:</b>", "💡 <b>Why it matters</b>",
                   "📊 <b>Market Impact</b>", "🔥 <b>Importance:</b> 9/10 (HIGH)", "📰 <b>Source:</b> Test Source", "IST",
                   "(+2 other sources)", "Official", "🧭 <b>Sentiment:</b>"):
        assert needle in t, needle
    assert m.buttons == [("🔗 Read Full Article", a.url)]


def test_non_ai_items_are_labelled():
    a = make_article(1, ai_provider="none", why_it_matters="", india_impact="")
    t = format_card(a, 1, IST).text
    assert "AI analysis unavailable" in t and "Why it matters" not in t and "Market Impact" not in t


def test_briefing_structure_and_disclaimer():
    grouped = {"india_market": [make_article(1), make_article(2)], "tech": [make_article(3, category="tech")]}
    now = datetime(2026, 11, 8, 1, 30, tzinfo=timezone.utc)
    msgs = build_briefing(title="🌅 GOOD MORNING | MARKET INTELLIGENCE", now=now, tz=IST, grouped=grouped,
                          takeaways=["One", "Two", "Three"], snapshot="", fmt="cards")
    assert "📅 Date: Sunday, 08 Nov 2026 · 07:00 IST" in msgs[0].text
    assert msgs[0].silent is False and all(m.silent for m in msgs[1:])  # only the header buzzes
    assert len(msgs) == 1 + 3 + 1
    assert "🤖 TECHNOLOGY &amp; AI" in msgs[3].text or "🤖 TECHNOLOGY & AI" in msgs[3].text
    assert "TODAY'S KEY TAKEAWAY" in msgs[-1].text and DISCLAIMER in msgs[-1].text
    assert all(len(m.text) <= TELEGRAM_LIMIT for m in msgs)


def test_compact_splits_long_sections():
    items = [make_article(i, summary="x" * 800, why_it_matters="y" * 400) for i in range(1, 9)]
    msgs = format_compact("📈 INDIAN STOCK MARKET", items, IST)
    assert len(msgs) > 1 and all(len(m.text) <= TELEGRAM_LIMIT for m in msgs)
    labels = [b[0] for m in msgs for b in m.buttons]
    assert labels == [f"🔗 Read Full Article {i}" for i in range(1, 9)]


def test_snapshot_labels_delay_and_missing_data():
    q = Quote("^NSEI", "NIFTY 50", 24500.5, 24250.0, datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc), provider="Yahoo Finance (unofficial, delayed)")
    text = format_snapshot([("🇮🇳 Indian Indices", [q]), ("🌍 Global", [])], IST, q.provider)
    assert "NIFTY 50</b>: 24,500.50 ▲ +1.03%" in text and "prev close 24,250.00" in text
    assert "07 Nov 15:30 IST" in text and "not real-time" in text and "delayed" in text
    assert "Data currently unavailable" in text
    assert format_snapshot([], IST, "x") == ""


def test_breaking_alert():
    m = build_breaking(make_article(1, importance=10), IST)[0]
    assert m.text.startswith("🚨 <b>BREAKING") and not m.silent and DISCLAIMER in m.text
