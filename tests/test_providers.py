import json

import httpx
import pytest
import respx

from app.ai.providers import (
    GeminiProvider, GroqProvider, ProviderAuthError, ProviderError, ProviderRateLimited, build_providers,
)


@respx.mock
async def test_gemini_request_and_parse():
    route = respx.post(GeminiProvider.URL.format(model="gemini-flash-latest")).mock(return_value=httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"ok": true}'}]}}]}))
    async with httpx.AsyncClient() as c:
        p = GeminiProvider("KEY", "gemini-flash-latest", c, min_interval=0)
        assert await p.complete_json("sys", "user") == '{"ok": true}'
    req = route.calls[0].request
    assert req.headers["x-goog-api-key"] == "KEY" and "KEY" not in str(req.url)
    body = json.loads(req.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"


@respx.mock
@pytest.mark.parametrize("status,exc", [(429, ProviderRateLimited), (401, ProviderAuthError), (404, ProviderAuthError), (500, ProviderError)])
async def test_gemini_errors(status, exc):
    respx.post(GeminiProvider.URL.format(model="m")).mock(return_value=httpx.Response(status, headers={"retry-after": "30"}, text="err"))
    async with httpx.AsyncClient() as c:
        with pytest.raises(exc) as ei:
            await GeminiProvider("K", "m", c, min_interval=0).complete_json("s", "u")
    if status == 429:
        assert ei.value.retry_after == 30


@respx.mock
async def test_gemini_blocked_and_timeout():
    respx.post(GeminiProvider.URL.format(model="m")).mock(return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    async with httpx.AsyncClient() as c:
        with pytest.raises(ProviderError):
            await GeminiProvider("K", "m", c, min_interval=0).complete_json("s", "u")
    respx.post(GeminiProvider.URL.format(model="t")).mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as c:
        with pytest.raises(ProviderError, match="timeout"):
            await GeminiProvider("K", "t", c, min_interval=0).complete_json("s", "u")


@respx.mock
async def test_groq_request_and_parse():
    route = respx.post(GroqProvider.URL).mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"articles": []}'}}]}))
    async with httpx.AsyncClient() as c:
        out = await GroqProvider("GK", "llama-3.3-70b-versatile", c, min_interval=0).complete_json("s", "u")
    assert out == '{"articles": []}'
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer GK"
    assert json.loads(req.content)["response_format"] == {"type": "json_object"}


async def test_build_providers_order(settings):
    from dataclasses import replace

    async with httpx.AsyncClient() as c:
        assert build_providers(settings, c) == []  # no keys -> extractive mode
        s = replace(settings, gemini_api_key="g", groq_api_key="q", anthropic_api_key="a")
        assert [p.name for p in build_providers(s, c)] == ["gemini", "groq", "anthropic"]
        s2 = replace(s, llm_provider="groq")
        assert [p.name for p in build_providers(s2, c)] == ["groq"]
        assert build_providers(replace(s, llm_provider="none"), c) == []
