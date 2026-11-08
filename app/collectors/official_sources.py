"""Official regulator / exchange sources.

What is used (all public, free, and offered as RSS by the publisher):
  * RBI press releases and notifications  (rbi.org.in RSS)
  * SEBI updates                          (sebi.gov.in RSS)

What is deliberately NOT used:
  * NSE / BSE corporate-announcement pages. They have no public RSS/API for
    personal use and their terms restrict automated scraping. Company
    announcements still reach you through Economic Times, Moneycontrol, Mint,
    Business Standard and Google News, which report on exchange filings.

Official items get extra priority and are always classed as Indian market news.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from app.collectors.base import FeedSpec, RawArticle

OFFICIAL_DOMAINS = ("rbi.org.in", "sebi.gov.in", "nseindia.com", "bseindia.com", "pib.gov.in", "federalreserve.gov")


def is_official_url(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def official_feeds(specs: list[FeedSpec]) -> list[FeedSpec]:
    return [s for s in specs if s.official]


def tag_official(article: RawArticle) -> RawArticle:
    """Mark items linking to an official domain (e.g. via Google News) as official."""
    if not article.official and is_official_url(article.url):
        article.official = True
        article.weight = max(article.weight, 3)
    if article.official and ("rbi.org.in" in article.url or "sebi.gov.in" in article.url):
        article.category = "india_market"
        article.region = "india"
    return article
