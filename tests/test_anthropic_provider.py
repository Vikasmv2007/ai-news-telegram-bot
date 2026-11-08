"""Claude provider tested against a fake SDK client (no network, no credits used)."""
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.ai.providers import AnthropicProvider, ProviderAuthError, ProviderError


def _resp(text="{\"ok\": true}", stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


def _err(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx2.Response(status, request=req), body=None)


class FakeMessages:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.behaviour(kw)
        if isinstance(r, Exception):
            raise r
        return r


def provider(model, beta_behaviour, plain_behaviour=lambda kw: _resp()):
    p = AnthropicProvider("sk-test", model, min_interval=0)
    p.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(beta_behaviour)), messages=FakeMessages(plain_behaviour))
    return p


async def test_uses_refusal_fallback_and_structured_output_on_opus():
    p = provider("claude-opus-5-5", lambda kw: _resp())
    assert await p.complete_json("sys", "user", schema={"type": "object"}) == '{"ok": true}'
    call = p.client.beta.messages.calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema" and call["output_config"]["effort"] == "low"
    assert "thinking" not in call and "temperature" not in call


async def test_bad_request_retries_plain():
    p = provider("claude-opus-5-5", lambda kw: _err(anthropic.BadRequestError, 400))
    assert await p.complete_json("s", "u") == '{"ok": true}'
    plain = p.client.messages.calls[0]
    assert "output_config" not in plain and "fallbacks" not in plain


async def test_other_models_use_plain_endpoint():
    p = provider("claude-haiku-5-5", lambda kw: pytest.fail("beta should not be used"))
    await p.complete_json("s", "u")
    assert p.client.messages.calls[0]["model"] == "claude-haiku-5-5"


async def test_errors_mapped():
    p = provider("claude-opus-5-5", lambda kw: _err(anthropic.AuthenticationError, 401))
    with pytest.raises(ProviderAuthError):
        await p.complete_json("s", "u")
    p = provider("claude-opus-5-5", lambda kw: _resp(stop="refusal"))
    with pytest.raises(ProviderError, match="declined"):
        await p.complete_json("s", "u")
