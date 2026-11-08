import json

import httpx
import pytest
import respx

from app.collectors.market_collector import YAHOO_CHART, FakeMarketProvider, YahooFinanceProvider, parse_chart


def test_parse_chart_previous_close(fixture_bytes):
    q = parse_chart(json.loads(fixture_bytes("yahoo_nsei.json")), "^NSEI", "NIFTY 50")
    assert q.price == 24500.5 and q.previous_close == 24250.0  # last close of the prior trading day
    assert round(q.change_pct, 2) == 1.03 and q.delayed and "delayed" in q.provider
    assert q.as_of.tzinfo is not None


def test_parse_chart_errors():
    with pytest.raises(ValueError):
        parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "X", "X")
    with pytest.raises(ValueError):
        parse_chart({"chart": {"result": [{"meta": {}}]}}, "X", "X")


@respx.mock
async def test_provider_skips_failures_and_caches(fixture_bytes):
    ok = respx.get(YAHOO_CHART.format(sym="%5ENSEI")).mock(return_value=httpx.Response(200, content=fixture_bytes("yahoo_nsei.json")))
    respx.get(YAHOO_CHART.format(sym="%5EBAD")).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as c:
        p = YahooFinanceProvider(c)
        quotes = await p.get_quotes({"^NSEI": "NIFTY 50", "^BAD": "Bad"})
        assert list(quotes) == ["^NSEI"]  # missing data is omitted, never invented
        await p.get_quotes({"^NSEI": "NIFTY 50"})
    assert ok.call_count == 1


async def test_fake_provider_is_labelled():
    q = await FakeMarketProvider().get_quotes({"A": "A"})
    assert "SAMPLE" in q["A"].provider
