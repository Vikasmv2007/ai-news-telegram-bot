"""The news pipeline: collect -> filter -> dedupe -> store -> AI -> query.

    collect()   fetch all feeds, drop junk, dedupe, store as 'new'
    process()   send the highest-priority 'new' articles to the AI
    refresh()   collect() + process(), protected by a lock so a scheduled run
                and a /now command never run the pipeline at the same time
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

import httpx

from app.ai.analyzer import adjust_for_source, extractive_analysis
from app.ai.classifier import is_low_value, match_watchlist, pre_score, refine_category
from app.ai.summarizer import Summarizer
from app.collectors.base import FeedSpec, RawArticle
from app.collectors.market_collector import MarketDataProvider, Quote
from app.collectors.official_sources import tag_official
from app.collectors.rss_collector import collect_all
from app.config import CATEGORIES, Settings
from app.database.db import Database
from app.database.models import Article, utcnow
from app.services.deduplication import Deduplicator
from app.services.sample_data import sample_articles

log = logging.getLogger(__name__)

MIN_PRE_SCORE_FOR_AI = 2.5


@dataclass
class RunStats:
    feeds_ok: int = 0
    feeds_failed: int = 0
    fetched: int = 0
    low_value: int = 0
    duplicates: int = 0
    new: int = 0
    analyzed_ai: int = 0
    analyzed_fallback: int = 0
    ai_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"feeds ok {self.feeds_ok}/{self.feeds_ok + self.feeds_failed}, fetched {self.fetched}, "
                f"new {self.new}, duplicates {self.duplicates}, filtered {self.low_value}, "
                f"AI {self.analyzed_ai}, fallback {self.analyzed_fallback}, AI-failed {self.ai_failed}")


def balance_by_category(articles: Sequence[Article], limit: int) -> list[Article]:
    """Pick up to `limit` articles round-robin across categories (each list
    already sorted by priority) so one busy category can't use the whole AI budget."""
    buckets: dict[str, list[Article]] = {}
    for a in articles:
        buckets.setdefault(a.category, []).append(a)
    out: list[Article] = []
    while len(out) < limit and any(buckets.values()):
        for cat in list(buckets):
            if buckets[cat] and len(out) < limit:
                out.append(buckets[cat].pop(0))
    return out


class NewsService:
    def __init__(self, settings: Settings, db: Database, http: httpx.AsyncClient, summarizer: Summarizer,
                 sources: dict, feeds: Sequence[FeedSpec], market: MarketDataProvider | None):
        self.settings = settings
        self.db = db
        self.http = http
        self.summarizer = summarizer
        self.sources = sources
        self.feeds = list(feeds)
        self.market = market
        self.dedup = Deduplicator(db)
        self._lock = asyncio.Lock()
        self.last_run: RunStats | None = None
        self.last_run_at: datetime | None = None
        self._takeaway_cache: dict[tuple, tuple[datetime, list[str]]] = {}

    # ------------------------------------------------------------ helpers
    @property
    def aliases(self) -> dict:
        return self.sources.get("company_aliases", {})

    def _watch_symbols(self) -> list[str]:
        syms = set(self.aliases) | set(self.settings.default_watchlist)
        for u in self.db.active_users(self.settings.allowed_chat_ids):
            syms.update(u.watchlist_list)
        return sorted(syms)

    # ------------------------------------------------------------ collect
    async def _fetch(self, stats: RunStats) -> list[RawArticle]:
        if self.settings.test_mode:
            stats.feeds_ok = 1
            return sample_articles()
        results = await collect_all(self.http, self.feeds, max_age_hours=self.settings.article_max_age_hours)
        raws: list[RawArticle] = []
        for r in results:
            try:
                self.db.record_feed_result(r.feed.name, r.feed.url, r.ok, len(r.articles), r.error)
            except Exception as e:  # health bookkeeping must never break collection
                log.debug("feed status write failed: %s", e)
            if r.ok:
                stats.feeds_ok += 1
                raws.extend(r.articles)
            else:
                stats.feeds_failed += 1
                log.warning("feed failed: %s: %s", r.feed.name, r.error)
        return raws

    async def collect(self) -> RunStats:
        stats = RunStats()
        raws = await self._fetch(stats)
        stats.fetched = len(raws)
        now = utcnow()
        symbols = self._watch_symbols()

        kept: list[RawArticle] = []
        junk: list[RawArticle] = []
        for r in raws:
            tag_official(r)
            if is_low_value(r.title, r.description):
                junk.append(r)
                continue
            r.category = refine_category(r)
            kept.append(r)
        stats.low_value = len(junk)

        unique, dups = self.dedup.run(kept)
        junk_unique, _ = self.dedup.run(junk) if junk else ([], [])

        rows: list[Article] = []
        for c, status in [(c, "new") for c in unique] + [(c, "duplicate") for c in dups] + [(c, "skipped") for c in junk_unique]:
            r = c.raw
            text = f"{r.title} {r.description}"
            rows.append(Article(
                id=c.id, url=r.url, canonical_url=c.canonical, title=r.title, title_fp=c.fingerprint,
                description=r.description, source=r.source, feed_name=r.feed_name, source_weight=r.weight,
                is_official=r.official, category=r.category, region=r.region, published_at=r.published_at,
                collected_at=now, processing_status=status, duplicate_of=c.duplicate_of,
                pre_score=pre_score(r, now), matched_symbols=",".join(match_watchlist(text, symbols, self.aliases)),
            ))
        self.db.add_articles(rows)
        for c in dups:
            self.db.bump_coverage(c.duplicate_of, official=c.raw.official, weight=c.raw.weight)
        stats.new, stats.duplicates = len(unique), len(dups)
        return stats

    # ------------------------------------------------------------ process
    async def process(self, stats: RunStats | None = None) -> RunStats:
        stats = stats or RunStats()
        since = utcnow() - timedelta(hours=self.settings.article_max_age_hours)
        limit = self.settings.ai_max_articles_per_run
        pending = self.db.pending_for_ai(since, limit=500 if not self.summarizer.enabled else limit * 4)

        to_ai: list[Article] = []
        fallback: list[Article] = []
        ai_candidates: list[Article] = []
        for a in pending:
            if not self.summarizer.enabled or a.attempts >= 2:
                fallback.append(a)  # no AI configured, or AI failed twice -> don't starve
            elif a.pre_score >= MIN_PRE_SCORE_FOR_AI:
                ai_candidates.append(a)
        to_ai = balance_by_category(ai_candidates, limit)

        size = self.settings.ai_batch_size
        for i in range(0, len(to_ai), size):
            batch = to_ai[i:i + size]
            results, failed, provider = await self.summarizer.analyze(batch)
            by_id = {a.id: a for a in batch}
            for aid, analysis in results.items():
                art = by_id[aid]
                adjust_for_source(analysis, official=art.is_official, coverage=art.coverage_count or 1)
                self.db.save_analysis(aid, provider=provider or "ai", **analysis.as_db_fields())
            stats.analyzed_ai += len(results)
            if failed:
                stats.ai_failed += len(failed)
                self.db.mark_failed(failed, self.summarizer.last_error or "invalid AI output")
            if provider is None:
                break  # every provider is unavailable; try again next run

        for a in fallback:
            analysis = extractive_analysis(a)
            self.db.save_analysis(a.id, provider="none", **analysis.as_db_fields())
        stats.analyzed_fallback += len(fallback)
        return stats

    async def refresh(self) -> RunStats:
        async with self._lock:
            try:
                stats = await self.collect()
            except Exception as e:
                log.exception("collection failed")
                stats = RunStats(errors=[f"collect: {e}"])
            try:
                await self.process(stats)
            except Exception as e:
                log.exception("processing failed")
                stats.errors.append(f"process: {e}")
            self.last_run, self.last_run_at = stats, utcnow()
            self.db.kv_set("last_refresh_at", self.last_run_at.isoformat())
            self.db.kv_set("last_refresh_stats", stats.summary())
            log.info("refresh complete: %s", stats.summary())
            return stats

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ------------------------------------------------------------ queries
    def items_by_category(self, *, since: datetime, categories: Sequence[str] = CATEGORIES,
                          min_importance: int = 1, per_category: int = 4,
                          exclude_delivered_to: int | None = None,
                          disabled: set[str] | None = None) -> dict[str, list[Article]]:
        out: dict[str, list[Article]] = {}
        for cat in categories:
            if disabled and cat in disabled:
                continue
            rows = self.db.processed_articles(since=since, categories=[cat], min_importance=min_importance,
                                              exclude_delivered_to=exclude_delivered_to, limit=per_category)
            if rows:
                out[cat] = rows
        return out

    def items_by_region(self, region: str, since: datetime, limit: int = 8) -> list[Article]:
        return self.db.processed_articles(since=since, region=region, min_importance=3, limit=limit)

    def trending(self, since: datetime, limit: int = 8) -> list[Article]:
        rows = self.db.processed_articles(since=since, min_importance=4, limit=60)
        rows.sort(key=lambda a: ((a.importance or 0) + 0.7 * min(4, (a.coverage_count or 1) - 1)
                                 + (0.5 if a.is_official else 0)), reverse=True)
        return rows[:limit]

    def watchlist_news(self, symbols: Sequence[str], since: datetime, limit: int = 8) -> list[Article]:
        if not symbols:
            return []
        return self.db.processed_articles(since=since, symbols=symbols, limit=limit)

    async def takeaways(self, items: Sequence[Article]) -> list[str]:
        ranked = sorted(items, key=lambda a: a.importance or 0, reverse=True)
        key = tuple(a.id for a in ranked[:12])
        cached = self._takeaway_cache.get(key)
        if cached and utcnow() - cached[0] < timedelta(hours=2):
            return cached[1]
        result = await self.summarizer.takeaways(ranked) if ranked else None
        if result:
            self._takeaway_cache = {key: (utcnow(), result)}  # keep only the latest
            return result
        # No AI: list the top headlines - facts only, no invented analysis.
        return [a.display_headline for a in ranked[:3]]

    # ------------------------------------------------------------ market
    def _symbol_groups(self, kind: str) -> list[tuple[str, dict[str, str]]]:
        ms = self.sources.get("market_symbols", {})
        groups = {
            "india": [("🇮🇳 Indian Indices", ms.get("india", {}))],
            "global": [("🌍 Global Indices", ms.get("global", {})), ("🛢️ Commodities & FX", ms.get("commodities", {}))],
            "full": [("🇮🇳 Indian Indices", ms.get("india", {})), ("🏭 Indian Sectors", ms.get("india_sectors", {})),
                     ("🌍 Global Indices", ms.get("global", {})), ("🛢️ Commodities & FX", ms.get("commodities", {}))],
            "closing": [("🇮🇳 Indian Indices", ms.get("india", {})), ("🏭 Indian Sectors", ms.get("india_sectors", {}))],
        }
        return [(t, g) for t, g in groups.get(kind, []) if g]

    async def market_snapshot(self, kind: str) -> list[tuple[str, list[Quote]]]:
        if not self.settings.market_data_enabled or self.market is None or kind == "none":
            return []
        out = []
        for title, symbols in self._symbol_groups(kind):
            try:
                quotes = await self.market.get_quotes(symbols)
            except Exception as e:
                log.warning("market snapshot failed: %s", e)
                quotes = {}
            out.append((title, [quotes[s] for s in symbols if s in quotes]))
        return out

    async def watchlist_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if not self.settings.market_data_enabled or self.market is None or not symbols:
            return {}
        names = {s: (self.aliases.get(s) or [s])[0] for s in symbols}
        try:
            return await self.market.get_quotes(names)
        except Exception as e:
            log.warning("watchlist quotes failed: %s", e)
            return {}
