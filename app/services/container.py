"""Wires all services together (one place to see how the app is assembled)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.ai.providers import FakeProvider, build_providers
from app.ai.summarizer import Summarizer
from app.collectors.market_collector import FakeMarketProvider, YahooFinanceProvider
from app.collectors.rss_collector import build_feed_specs
from app.config import Settings, load_sources
from app.database.db import Database
from app.services.briefing import BriefingService
from app.services.news_service import NewsService
from app.services.sample_data import fake_llm_responder
from app.utils.http import make_client

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    http: httpx.AsyncClient
    summarizer: Summarizer
    news: NewsService
    briefing: BriefingService
    sources: dict

    async def close(self) -> None:
        await self.http.aclose()


def create_services(settings: Settings, db: Database | None = None) -> Services:
    db = db or Database(settings.database_url)
    db.init()
    sources = load_sources(settings.sources_file)
    http = make_client(settings.http_user_agent)
    if settings.test_mode:
        providers = [FakeProvider(fake_llm_responder)]
        market = FakeMarketProvider()
        log.warning("TEST MODE: using sample news, fake AI and sample market data (nothing is real)")
    else:
        providers = build_providers(settings, http)
        market = YahooFinanceProvider(http) if settings.market_data_enabled else None
        if not providers:
            log.warning("No AI provider configured - using feed text only (set GEMINI_API_KEY for free AI summaries)")
        else:
            log.info("AI providers: %s", ", ".join(p.name for p in providers))
    summarizer = Summarizer(providers, timeout=settings.ai_timeout_seconds)
    feeds = build_feed_specs(sources)
    news = NewsService(settings, db, http, summarizer, sources, feeds, market)
    briefing = BriefingService(settings, news)
    return Services(settings, db, http, summarizer, news, briefing, sources)
