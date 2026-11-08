import json

import pytest

from app.ai.analyzer import (
    adjust_for_source, extract_json, extractive_analysis, parse_batch_response, parse_takeaways, remove_advice,
    validate_item,
)
from tests.conftest import make_article


def item(**kw):
    base = {"id": "a1", "headline": "RBI cuts rate", "summary": "The RBI cut the repo rate.", "why_it_matters": "Loans may get cheaper.",
            "affected": "Borrowers, banks", "india_market_impact": "Rate-sensitive sectors may react.", "global_market_impact": "",
            "importance": 9, "category": "india_market", "region": "india", "sentiment": "positive", "is_breaking": True}
    base.update(kw)
    return base


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! Here it is: {"a": 3} hope that helps') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("not json at all")


def test_validate_clamps_and_defaults():
    a = validate_item(item(importance=42, category="sports", sentiment="bullish", region="mars"), "tech", "global")
    assert a.importance == 10 and a.category == "tech" and a.sentiment == "uncertain" and a.region == "global"
    b = validate_item(item(importance="7.6", is_breaking=True), "tech", "global")
    assert b.importance == 8 and b.is_breaking is False  # breaking requires >= 9
    with pytest.raises(ValueError):
        validate_item(item(summary=""), "tech", "global")


def test_never_gives_investment_advice():
    text = "Profits rose 10%. Investors should buy the stock now. The target price is 500. Demand stayed strong."
    cleaned = remove_advice(text)
    assert "should buy" not in cleaned and "target price" not in cleaned.lower()
    assert "Profits rose 10%." in cleaned and "Demand stayed strong." in cleaned
    a = validate_item(item(india_market_impact="We recommend buying bank stocks."), "india_market", "india")
    assert "recommend" not in a.india_impact.lower()
    assert a.india_impact == "Unclear at this stage."


def test_markdown_stripped():
    a = validate_item(item(summary="**Big** news _here_"), "india_market", "india")
    assert a.summary == "Big news here"


def test_parse_batch_response_handles_bad_ids():
    arts = [make_article(1), make_article(2)]
    payload = {"articles": [item(id="a1"), item(id="WRONG", headline="Second")]}
    results, failed = parse_batch_response(json.dumps(payload), arts)
    assert set(results) == {"a1", "a2"} and failed == []  # order-preserving recovery
    payload = {"articles": [item(id="a1")]}
    results, failed = parse_batch_response(json.dumps(payload), arts)
    assert set(results) == {"a1"} and failed == ["a2"]


def test_adjust_for_official_and_coverage():
    a = validate_item(item(importance=7), "india_market", "india")
    assert adjust_for_source(a, official=True, coverage=4).importance == 9
    low = validate_item(item(importance=3), "india_market", "india")
    assert adjust_for_source(low, official=True, coverage=5).importance == 3


def test_extractive_fallback_is_labelled_and_factual():
    art = make_article(1, description="First sentence. Second sentence. Third. Fourth.", pre_score=6.4)
    a = extractive_analysis(art)
    assert a.ai_generated is False and a.why_it_matters == "" and a.sentiment == "uncertain"
    assert a.summary == "First sentence. Second sentence. Third."
    assert a.importance == 6
    empty = extractive_analysis(make_article(2, description=""))
    assert "Only the headline" in empty.summary


def test_takeaways_parsing():
    assert parse_takeaways('{"takeaways": ["a", "b", "c", "d"]}') == ["a", "b", "c"]
    with pytest.raises(ValueError):
        parse_takeaways('{"takeaways": []}')
