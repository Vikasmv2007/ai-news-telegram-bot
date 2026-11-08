"""Runs articles through the LLM provider chain in small batches.

Batching (default 6 articles per request) keeps us far inside free-tier daily
request quotas: ~24 articles every 30 min = ~4 requests per run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence

from app.ai.analyzer import (
    JSON_SCHEMA, SYSTEM_PROMPT, TAKEAWAY_SYSTEM, Analysis, article_to_prompt_item, build_user_prompt,
    parse_batch_response, parse_takeaways,
)
from app.ai.providers import ProviderAuthError, ProviderError, ProviderRateLimited

log = logging.getLogger(__name__)


class Summarizer:
    def __init__(self, providers: Sequence, timeout: float = 60.0):
        self.providers = list(providers)
        self.timeout = timeout
        self._disabled_until: dict[str, float] = {}
        self.last_error: str | None = None
        self.last_success_provider: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def _available(self):
        now = time.monotonic()
        for p in self.providers:
            if self._disabled_until.get(p.name, 0) > now:
                continue
            if getattr(p, "cooling_down", False):
                continue
            yield p

    def status(self) -> dict:
        now = time.monotonic()
        return {
            p.name: ("disabled" if self._disabled_until.get(p.name, 0) > now
                     else "cooling down" if getattr(p, "cooling_down", False) else "ready")
            for p in self.providers
        }

    async def _call(self, provider, system: str, user: str, schema: dict | None) -> str:
        return await asyncio.wait_for(provider.complete_json(system, user, schema), timeout=self.timeout + 15)

    async def _run_chain(self, system: str, user: str, schema: dict | None, parse):
        """Try each available provider (one retry each for transient errors)."""
        errors = []
        for provider in list(self._available()):
            for attempt in (1, 2):
                try:
                    text = await self._call(provider, system, user, schema)
                    result = parse(text)
                    self.last_success_provider = provider.name
                    return provider.name, result
                except ProviderRateLimited as e:
                    wait = e.retry_after or 60.0
                    # a long Retry-After usually means the daily quota is used up
                    cool = wait if wait <= 120 else max(wait, 1800)
                    if hasattr(provider, "cool_down"):
                        provider.cool_down(cool)
                    errors.append(str(e)[:160])
                    log.warning("%s rate-limited; cooling down %.0fs", provider.name, cool)
                    break
                except ProviderAuthError as e:
                    self._disabled_until[provider.name] = time.monotonic() + 6 * 3600
                    errors.append(str(e)[:160])
                    log.error("%s disabled for 6h: %s", provider.name, e)
                    break
                except (ProviderError, ValueError, asyncio.TimeoutError) as e:
                    errors.append(f"{provider.name}: {type(e).__name__}: {str(e)[:120]}")
                    log.warning("%s attempt %d failed: %s", provider.name, attempt, e)
                    if attempt == 1:
                        await asyncio.sleep(2.0)
        self.last_error = "; ".join(errors) or "no AI provider available"
        raise ProviderError(self.last_error)

    async def analyze(self, articles: Sequence) -> tuple[dict[str, Analysis], list[str], str | None]:
        """Analyse one batch. Returns (results, failed_ids, provider_name)."""
        if not articles:
            return {}, [], None
        user = build_user_prompt([article_to_prompt_item(a) for a in articles])
        try:
            name, (results, failed) = await self._run_chain(
                SYSTEM_PROMPT, user, JSON_SCHEMA, lambda t: parse_batch_response(t, list(articles))
            )
            return results, failed, name
        except ProviderError:
            return {}, [a.id for a in articles], None

    async def takeaways(self, articles: Sequence) -> list[str] | None:
        if not articles or not self.providers:
            return None
        lines = [
            {"headline": a.display_headline, "why_it_matters": a.why_it_matters or "", "category": a.category}
            for a in articles[:12]
        ]
        try:
            _, result = await self._run_chain(TAKEAWAY_SYSTEM, build_user_prompt(lines), None, parse_takeaways)
            return result
        except ProviderError:
            return None
