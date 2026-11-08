"""Collects articles from RSS/Atom feeds (publisher feeds + Google News search).

One broken feed never stops the others: every feed is fetched independently
and failures are returned as `FeedResult(error=...)`.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote_plus

import feedparser
import httpx

from app.collectors.base import FeedResult, FeedSpec, RawArticle
from app.utils.http import get_with_retry
from app.utils.text import clean_title, strip_html, truncate

log = logging.getLogger(__name__)

GOOGLE_NEWS_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def google_news_url(query: str) -> str:
    return GOOGLE_NEWS_SEARCH.format(q=quote_plus(query))


def build_feed_specs(sources: dict[str, Any]) -> list[FeedSpec]:
    specs: list[FeedSpec] = []
    for f in sources.get("feeds", []):
        specs.append(FeedSpec(
            name=f["name"], url=f["url"], category=f["category"], region=f.get("region", "global"),
            weight=int(f.get("weight", 1)), official=bool(f.get("official", False)),
        ))
    for g in sources.get("google_news_queries", []):
        specs.append(FeedSpec(
            name=f"Google News: {g['query']}", url=google_news_url(g["query"]), category=g["category"],
            region=g.get("region", "global"), weight=int(g.get("weight", 1)), aggregator=True,
        ))
    return specs


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).replace(tzinfo=None)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def parse_feed(content: bytes | str, feed: FeedSpec, *, now: datetime | None = None,
               max_age_hours: int = 36) -> list[RawArticle]:
    """Parse feed XML into RawArticles. Raises ValueError for unusable feeds."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"invalid feed: {type(parsed.bozo_exception).__name__}: {parsed.bozo_exception}")
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    oldest = now - timedelta(hours=max_age_hours)
    out: list[RawArticle] = []
    for e in parsed.entries:
        link = (e.get("link") or "").strip()
        raw_title = e.get("title") or ""
        if not link or not raw_title.strip() or not link.startswith(("http://", "https://")):
            continue
        published = _entry_datetime(e)
        if published is None:
            if not feed.official:
                continue  # we never invent publication times
            published = now
        if published > now + timedelta(hours=1):  # clock skew / bogus dates
            published = now
        if published < oldest:
            continue
        source = feed.name
        if feed.aggregator:
            src = e.get("source")
            source = (src.get("title") if isinstance(src, dict) else None) or "Google News"
        title = clean_title(raw_title, source)
        desc = strip_html(e.get("summary") or e.get("description") or "")
        if feed.aggregator:
            # Google News descriptions just repeat the headline + publisher
            desc = "" if desc.startswith(title[:40]) else desc
        if desc.lower() == title.lower():
            desc = ""
        out.append(RawArticle(
            title=truncate(title, 300), url=link, source=source, published_at=published,
            category=feed.category, region=feed.region, description=truncate(desc, 1200),
            feed_name=feed.name, weight=feed.weight, official=feed.official,
        ))
    return out


async def fetch_feed(client: httpx.AsyncClient, feed: FeedSpec, max_age_hours: int = 36) -> FeedResult:
    try:
        resp = await get_with_retry(client, feed.url, attempts=3)
        articles = parse_feed(resp.content, feed, max_age_hours=max_age_hours)
        return FeedResult(feed, articles)
    except httpx.HTTPStatusError as e:
        return FeedResult(feed, error=f"HTTP {e.response.status_code}")
    except Exception as e:  # network errors after retries, parse errors, ...
        return FeedResult(feed, error=f"{type(e).__name__}: {e}"[:300])


async def collect_all(client: httpx.AsyncClient, feeds: Iterable[FeedSpec], *, concurrency: int = 8,
                      max_age_hours: int = 36) -> list[FeedResult]:
    sem = asyncio.Semaphore(concurrency)

    async def guarded(f: FeedSpec) -> FeedResult:
        async with sem:
            return await fetch_feed(client, f, max_age_hours)

    results = await asyncio.gather(*(guarded(f) for f in feeds), return_exceptions=True)
    out: list[FeedResult] = []
    for f, r in zip(feeds, results):
        if isinstance(r, BaseException):
            out.append(FeedResult(f, error=f"unexpected: {r!r}"[:300]))
        else:
            out.append(r)
    ok = sum(1 for r in out if r.ok)
    log.info("collected feeds", extra={"feeds_ok": ok, "feeds_failed": len(out) - ok,
                                       "items": sum(len(r.articles) for r in out)})
    return out
