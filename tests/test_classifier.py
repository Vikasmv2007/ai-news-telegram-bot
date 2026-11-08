from datetime import datetime, timedelta

from app.ai.classifier import importance_level, is_low_value, match_watchlist, pre_score, refine_category
from app.collectors.base import RawArticle

NOW = datetime(2026, 11, 8, 6, 0)


def raw(title, cat="india_world", region="india", weight=2, official=False, age_h=1, desc=""):
    return RawArticle(title, "https://x.example/a", "Src", NOW - timedelta(hours=age_h), cat, region, desc, weight=weight, official=official)


def test_low_value_filter():
    assert is_low_value("Top 5 stocks to buy this week")
    assert is_low_value("Sponsored: Best credit card deals")
    assert is_low_value("Photos: Diwali celebrations across India")
    assert is_low_value("Is this a multibagger? Target price raised")
    assert not is_low_value("RBI cuts repo rate by 25 basis points")
    assert not is_low_value("Software exports rise 12% in Q2")


def test_refine_category():
    assert refine_category(raw("Sensex, Nifty hit record high as FII inflows surge")) == "india_market"
    assert refine_category(raw("Cabinet approves new railway projects")) == "india_world"
    # market/tech feeds are trusted as-is
    assert refine_category(raw("Nvidia stock falls", cat="tech", region="global")) == "tech"


def test_pre_score_prioritises_official_and_impact():
    official = pre_score(raw("RBI announces repo rate decision", cat="india_market", weight=3, official=True), NOW)
    big = pre_score(raw("Sensex crashes 1,200 points as inflation surges"), NOW)
    minor = pre_score(raw("Opinion: how to plan your weekend"), NOW)
    old = pre_score(raw("Sensex crashes 1,200 points as inflation surges", age_h=30), NOW)
    assert official > minor and big > minor and big > old
    assert 0 <= minor <= 10 and 0 <= official <= 10


def test_keywords_match_whole_words():
    # "war" must not match "software"/"award"
    a = pre_score(raw("Company wins software award"), NOW)
    b = pre_score(raw("Company wins software prize"), NOW)
    assert a == b


def test_watchlist_matching():
    aliases = {"TCS.NS": ["Tata Consultancy Services", "TCS"], "ITC.NS": ["ITC"], "RELIANCE.NS": ["Reliance"]}
    syms = list(aliases)
    assert match_watchlist("TCS shares rise after Q2", syms, aliases) == ["TCS.NS"]
    assert match_watchlist("New switch for itchy fingers", syms, aliases) == []  # ITC/tcs case-sensitive
    assert match_watchlist("reliance industries posts profit", syms, aliases) == ["RELIANCE.NS"]
    assert match_watchlist("Wipro news", ["WIPRO.NS"], {}) == ["WIPRO.NS"]  # symbol base fallback


def test_importance_levels():
    assert importance_level(9) == "HIGH" and importance_level(8) == "HIGH"
    assert importance_level(6) == "MEDIUM" and importance_level(5) == "MEDIUM"
    assert importance_level(3) == "LOW" and importance_level(None) == "LOW"
