"""LLM providers behind one tiny interface: `complete_json(system, user) -> str`.

    gemini     Google Gemini API  - FREE tier (key from aistudio.google.com)
    groq       Groq API           - FREE tier (key from console.groq.com)
    anthropic  Claude API         - PAID (pay-as-you-go credits required)

With LLM_PROVIDER=auto the bot tries every provider that has a key, in the
order gemini -> groq -> anthropic, and falls back to the next one on quota or
network errors. If none work, `analyzer.extractive_analysis` is used so the
bot keeps delivering (clearly labelled as "AI analysis unavailable").
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Generic provider failure (try the next provider)."""


class ProviderRateLimited(ProviderError):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    """Bad/missing key or model: don't keep hammering this provider."""


class LLMProvider(Protocol):
    name: str

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> str: ...


class _Paced:
    """Keeps a minimum gap between calls (free tiers limit requests/minute) and
    lets a provider 'cool down' after a rate-limit response."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()
        self.cooldown_until = 0.0

    async def wait(self) -> None:
        async with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()

    @property
    def cooling_down(self) -> bool:
        return time.monotonic() < self.cooldown_until

    def cool_down(self, seconds: float) -> None:
        self.cooldown_until = time.monotonic() + seconds


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


def _raise_for_status(name: str, resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:300]
    if resp.status_code == 429:
        raise ProviderRateLimited(f"{name}: rate limited / quota exceeded: {body}", _retry_after(resp))
    if resp.status_code in (401, 403):
        raise ProviderAuthError(f"{name}: authentication failed (check API key): {body}")
    if resp.status_code == 404:
        raise ProviderAuthError(f"{name}: model not found (check model name): {body}")
    if resp.status_code == 400:
        raise ProviderError(f"{name}: bad request: {body}")
    raise ProviderError(f"{name}: HTTP {resp.status_code}: {body}")


class GeminiProvider(_Paced):
    """Google Gemini via REST. Free tier: limited requests/min and /day."""

    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient, timeout: float = 60, min_interval: float = 6.0):
        super().__init__(min_interval)
        self.api_key, self.model, self.client, self.timeout = api_key, model, client, timeout

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> str:
        await self.wait()
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 8192},
        }
        try:
            resp = await self.client.post(self.URL.format(model=self.model), json=body, timeout=self.timeout,
                                          headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        except httpx.TimeoutException as e:
            raise ProviderError("gemini: timeout") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini: network error {type(e).__name__}") from e
        _raise_for_status(self.name, resp)
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            raise ProviderError(f"gemini: no candidates (blocked?): {str(data.get('promptFeedback'))[:200]}")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise ProviderError(f"gemini: empty response (finishReason={cands[0].get('finishReason')})")
        return text


class GroqProvider(_Paced):
    """Groq (OpenAI-compatible REST). Free tier: limited requests & tokens/day."""

    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient, timeout: float = 60, min_interval: float = 3.0):
        super().__init__(min_interval)
        self.api_key, self.model, self.client, self.timeout = api_key, model, client, timeout

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> str:
        await self.wait()
        body = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": 6000,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            resp = await self.client.post(self.URL, json=body, timeout=self.timeout,
                                          headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.TimeoutException as e:
            raise ProviderError("groq: timeout") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"groq: network error {type(e).__name__}") from e
        _raise_for_status(self.name, resp)
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError("groq: unexpected response shape") from e


class AnthropicProvider(_Paced):
    """Claude via the official `anthropic` SDK. PAID - requires API credits."""

    name = "anthropic"
    # Models that accept the server-side refusal fallback (`fallbacks: "default"`).
    _FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

    def __init__(self, api_key: str, model: str, timeout: float = 60, min_interval: float = 1.0):
        super().__init__(min_interval)
        import anthropic  # imported lazily so the package is optional at runtime

        self._anthropic = anthropic
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> str:
        await self.wait()
        a = self._anthropic
        base = dict(model=self.model, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}])
        output_config: dict = {"effort": "low"}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        try:
            try:
                if self.model in self._FALLBACK_MODELS:
                    resp = await self.client.beta.messages.create(
                        **base, output_config=output_config,
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                    )
                else:
                    resp = await self.client.messages.create(**base, output_config=output_config)
            except a.BadRequestError as e:
                # Older model / account without these features: retry with a plain request.
                log.warning("anthropic: advanced options rejected (%s); retrying plain request", e.message[:120])
                resp = await self.client.messages.create(**base)
        except a.AuthenticationError as e:
            raise ProviderAuthError("anthropic: invalid API key") from e
        except a.PermissionDeniedError as e:
            raise ProviderAuthError("anthropic: key lacks permission / no credits") from e
        except a.NotFoundError as e:
            raise ProviderAuthError(f"anthropic: model {self.model!r} not found") from e
        except a.RateLimitError as e:
            ra = None
            try:
                ra = float(e.response.headers.get("retry-after", ""))
            except (ValueError, AttributeError):
                pass
            raise ProviderRateLimited("anthropic: rate limited", ra) from e
        except a.APIStatusError as e:
            raise ProviderError(f"anthropic: HTTP {e.status_code}") from e
        except a.APIConnectionError as e:
            raise ProviderError("anthropic: network error / timeout") from e
        if resp.stop_reason == "refusal":
            raise ProviderError("anthropic: request declined by safety system")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if not text.strip():
            raise ProviderError(f"anthropic: empty response (stop_reason={resp.stop_reason})")
        return text


class FakeProvider:
    """Deterministic provider for tests / TEST_MODE: echoes canned JSON."""

    name = "fake"

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> str:
        self.calls.append((system, user))
        out = self.responder(system, user)
        return out if isinstance(out, str) else json.dumps(out)


def build_providers(settings, client: httpx.AsyncClient) -> list:
    """Create the provider chain from settings. Empty list = no AI (extractive mode)."""
    order = {"auto": ["gemini", "groq", "anthropic"], "none": []}.get(settings.llm_provider, [settings.llm_provider])
    providers = []
    for name in order:
        try:
            if name == "gemini" and settings.gemini_api_key:
                providers.append(GeminiProvider(settings.gemini_api_key, settings.gemini_model, client,
                                                settings.ai_timeout_seconds, settings.ai_min_interval_seconds))
            elif name == "groq" and settings.groq_api_key:
                providers.append(GroqProvider(settings.groq_api_key, settings.groq_model, client,
                                              settings.ai_timeout_seconds))
            elif name == "anthropic" and settings.anthropic_api_key:
                providers.append(AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model,
                                                   settings.ai_timeout_seconds))
            elif settings.llm_provider == name:
                log.warning("LLM_PROVIDER=%s but its API key is not set", name)
        except ImportError:
            log.warning("provider %s unavailable: package not installed", name)
    return providers
