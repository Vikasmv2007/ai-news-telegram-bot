import json

from app.ai.providers import FakeProvider, ProviderAuthError, ProviderRateLimited
from app.ai.summarizer import Summarizer
from tests.conftest import make_article


def good_response(system, user):
    items = json.loads(user[user.index("["):])
    return {"articles": [{"id": it["id"], "headline": it["headline"], "summary": "Fact.", "why_it_matters": "W.",
                          "affected": "X", "india_market_impact": "May.", "global_market_impact": "", "importance": 6,
                          "category": "tech", "region": "global", "sentiment": "mixed", "is_breaking": False} for it in items]}


class Failing:
    def __init__(self, name, exc):
        self.name, self.exc, self.calls = name, exc, 0
        self.cooldown = None

    def cool_down(self, s):
        self.cooldown = s

    @property
    def cooling_down(self):
        return self.cooldown is not None

    async def complete_json(self, *a, **k):
        self.calls += 1
        raise self.exc


async def test_success_path():
    s = Summarizer([FakeProvider(good_response)])
    res, failed, prov = await s.analyze([make_article(1), make_article(2)])
    assert set(res) == {"a1", "a2"} and failed == [] and prov == "fake"


async def test_falls_back_to_next_provider_on_rate_limit():
    limited = Failing("gemini", ProviderRateLimited("quota", retry_after=3600))
    s = Summarizer([limited, FakeProvider(good_response)])
    res, failed, prov = await s.analyze([make_article(1)])
    assert prov == "fake" and set(res) == {"a1"}
    assert limited.calls == 1 and limited.cooldown >= 1800  # daily quota -> long cool-down
    await s.analyze([make_article(2)])
    assert limited.calls == 1  # skipped while cooling down


async def test_auth_error_disables_provider():
    bad = Failing("groq", ProviderAuthError("bad key"))
    s = Summarizer([bad, FakeProvider(good_response)])
    await s.analyze([make_article(1)])
    await s.analyze([make_article(2)])
    assert bad.calls == 1 and s.status()["groq"] == "disabled"


async def test_invalid_json_retried_then_all_fail(monkeypatch):
    async def no_sleep(*a, **k):
        pass
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    junk = FakeProvider(lambda s, u: "I cannot produce JSON today")
    s = Summarizer([junk])
    res, failed, prov = await s.analyze([make_article(1)])
    assert res == {} and failed == ["a1"] and prov is None
    assert len(junk.calls) == 2 and s.last_error


async def test_takeaways_fallback_none_without_providers():
    assert await Summarizer([]).takeaways([make_article(1)]) is None
    s = Summarizer([FakeProvider(lambda sy, u: {"takeaways": ["one", "two", "three"]})])
    assert await s.takeaways([make_article(1)]) == ["one", "two", "three"]
