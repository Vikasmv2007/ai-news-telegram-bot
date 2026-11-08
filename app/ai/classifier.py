"""Rule-based classification that runs before (and without) the LLM.

Why rules first? Free LLM tiers allow only a few hundred calls per day. Rules
cheaply (1) drop promotional / tip-sheet content, (2) refine the category,
(3) give every article a priority score so only the most promising ones are
sent to the AI, and (4) tag watchlist companies.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable, Mapping, Sequence

from app.collectors.base import RawArticle

# ---------------------------------------------------------------- low value
_LOW_VALUE_PATTERNS = [
    r"\bsponsored\b", r"\bbrand ?post\b", r"\bpartner(ed)? content\b", r"\badvertorial\b", r"\bpromoted\b",
    r"\bwebinar\b", r"\bcoupon", r"\bpromo code\b", r"\bdeal of the day\b", r"\bbest deals?\b", r"\b\d+% off\b",
    r"\bhoroscope\b", r"\bastrology\b", r"\bquiz\b", r"\bcrossword\b", r"\bwordle\b",
    r"^(photos?|in pics|watch|video|gallery|podcast)\s*[:|]", r"\bweb stor(y|ies)\b",
    # tip sheets / trading calls (we never give or relay buy/sell advice)
    r"\bstocks? to buy\b", r"\bstocks? to (sell|watch|pick)\b", r"\bbuy or sell\b", r"\bbuy,? sell\b",
    r"\bmultibagger\b", r"\btarget price\b", r"\bstock picks?\b", r"\btrading (ideas?|calls?)\b",
    r"\bintraday (picks?|calls?|stocks?)\b", r"\btechnical picks?\b", r"\bshould you buy\b",
    r"\b(top|best) \d+ stocks\b", r"\bbuy (rating|call)\b", r"\bsell (rating|call)\b",
    r"\bgold (rate|price) today in\b", r"\bpetrol (and|&) diesel prices? today\b",
    r"\blottery\b", r"\bresult (date|time) (announced|out)\b",
]
_LOW_VALUE_RE = re.compile("|".join(_LOW_VALUE_PATTERNS), re.IGNORECASE)


def is_low_value(title: str, description: str = "") -> bool:
    return bool(_LOW_VALUE_RE.search(title)) or bool(re.search(r"\bsponsored\b|\bpaid partnership\b", description, re.I))


@lru_cache(maxsize=512)
def _kw(kw: str) -> re.Pattern:
    kw = kw.strip()
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")


def count_keywords(text_lower: str, keywords: Iterable[str]) -> int:
    return sum(1 for k in keywords if _kw(k).search(text_lower))


# ---------------------------------------------------------------- categories
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "india_market": [
        "sensex", "nifty", "bank nifty", "nse", "bse", "sebi", "rbi", "repo rate", "dalal street", "fii", "dii",
        "ipo", "q1 results", "q2 results", "q3 results", "q4 results", "quarterly results", "rupee", "india inc",
        "mutual fund", "sip inflows", "monetary policy committee", "mpc",
    ],
    "global_market": [
        "wall street", "s&p 500", "nasdaq", "dow jones", "federal reserve", "the fed", "fomc", "powell",
        "treasury yields", "us inflation", "cpi", "nonfarm payrolls", "jobs report", "ecb", "bank of japan",
        "nikkei", "hang seng", "ftse", "dax", "crude oil", "brent", "opec", "gold prices", "bitcoin", "crypto",
        "tariff", "recession", "imf", "world bank",
    ],
    "tech": [
        "openai", "chatgpt", "anthropic", "claude", "gemini", "deepmind", "nvidia", "microsoft", "apple", "meta",
        "amazon web services", "aws", "artificial intelligence", "ai", "ai model", "llm", "semiconductor", "chip",
        "startup", "cybersecurity", "data breach", "ransomware", "software", "open source", "github", "iphone",
        "android", "cloud",
    ],
}


def refine_category(article: RawArticle) -> str:
    """Keep the feed's category unless the text is clearly about another one.

    Only India & World news gets re-routed (e.g. a Hindu story about the
    Sensex becomes Indian market news); market/tech feeds are trusted.
    """
    if article.official:
        return article.category
    if article.category != "india_world":
        return article.category
    text = article.title.lower()
    scores = {cat: count_keywords(text, kws) for cat, kws in _CATEGORY_KEYWORDS.items()}
    best, n = max(scores.items(), key=lambda kv: kv[1])
    if n >= 1:
        if best == "india_market" or (best == "global_market" and article.region == "global"):
            return best
        if best == "tech" and n >= 2:
            return best
    return article.category


# ---------------------------------------------------------------- pre-score
_HIGH_IMPACT = [
    "repo rate", "rate cut", "rate hike", "interest rate", "monetary policy", "inflation", "gdp", "recession",
    "federal reserve", "fomc", "budget", "fiscal deficit", "sebi", "rbi", "tariff", "sanction", "war", "ceasefire",
    "election", "crash", "plunge", "surge", "record high", "all-time high", "lowest", "biggest", "merger",
    "acquisition", "acquire", "ipo", "earnings", "results", "profit", "loss", "layoff", "bankrupt", "default",
    "ban", "probe", "antitrust", "fine", "opec", "crude", "rupee", "fii", "outflow", "inflow", "downgrade",
    "upgrade", "launch", "unveils", "releases", "breach", "hack", "regulation", "policy", "approves", "cabinet",
]
_LOW_IMPACT = ["opinion", "column", "explained", "how to", "tips", "review", "hands-on", "rumour", "rumor"]


def pre_score(article: RawArticle, now: datetime | None = None) -> float:
    """Heuristic priority 0-10 used to choose what the AI processes first."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    t = article.title.lower()
    score = 2.0 + article.weight  # 3..5
    if article.official:
        score += 2.0
    score += min(3.0, 0.9 * count_keywords(t, _HIGH_IMPACT))
    score -= 1.5 * count_keywords(t, _LOW_IMPACT)
    age_h = max(0.0, (now - article.published_at).total_seconds() / 3600)
    score -= min(2.0, age_h / 8)  # freshness
    if re.search(r"\d", t):  # numbers usually mean concrete news (rates, results, %)
        score += 0.3
    return round(max(0.0, min(10.0, score)), 2)


# ---------------------------------------------------------------- watchlist
@lru_cache(maxsize=64)
def _alias_regex(alias: str) -> re.Pattern:
    flags = 0 if len(alias) <= 4 or alias.isupper() else re.IGNORECASE  # TCS/ITC/SBI: exact case only
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", flags)


def aliases_for(symbol: str, aliases: Mapping[str, Sequence[str]]) -> list[str]:
    names = list(aliases.get(symbol, []))
    if not names:
        base = symbol.split(".")[0].lstrip("^")
        if len(base) >= 3:
            # long symbols (WIPRO) match "Wipro" case-insensitively; short ones (TCS) stay exact
            names = [base.title()] if len(base) > 4 else [base]
    return names


def match_watchlist(text: str, symbols: Iterable[str], aliases: Mapping[str, Sequence[str]]) -> list[str]:
    hits = []
    for sym in symbols:
        if any(_alias_regex(a).search(text) for a in aliases_for(sym, aliases)):
            hits.append(sym)
    return hits


def importance_level(score: int | None) -> str:
    s = score or 0
    if s >= 8:
        return "HIGH"
    if s >= 5:
        return "MEDIUM"
    return "LOW"
