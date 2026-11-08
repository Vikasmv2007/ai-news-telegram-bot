"""Prompting, validation and safety rules for AI news analysis.

The AI only sees what the feed published (headline + short description). It
is told to keep facts and analysis separate, never invent details, and never
give buy/sell advice. Its output is then *validated* here: bad JSON, missing
fields, out-of-range scores and advice-like sentences are all handled in code
rather than trusted blindly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.ai.classifier import importance_level
from app.config import CATEGORIES
from app.utils.text import first_sentences, truncate

SENTIMENTS = ("positive", "negative", "mixed", "uncertain")

SYSTEM_PROMPT = """You are a careful financial and technology news analyst writing for a busy reader in India.
You receive news items as JSON (id, headline, description, source, published, feed_category).

Rules - follow all of them:
1. Use ONLY the information in each item's headline and description. Never add facts, numbers, names, quotes or dates that are not in the text. If the description is short, write a shorter summary rather than padding it.
2. "summary" contains confirmed facts from the text only (3-5 short sentences when the text supports it, otherwise 1-2).
3. "why_it_matters", "affected", "india_market_impact" and "global_market_impact" are ANALYSIS. Use cautious wording ("could", "may", "is likely to be watched"). Mention uncertainty when the impact is unclear. Do not present speculation as fact.
4. Never recommend buying, selling, holding or accumulating any security. No price targets, no predictions of stock prices or returns.
5. "sentiment" is for the directly affected industry/company: positive, negative, mixed or uncertain. Use "uncertain" or "mixed" when it is not clear - do not force positive/negative.
6. "importance" 1-10: 9-10 = major market-moving/national/global development (central bank rate decision, major policy, crash, war escalation, landmark deal); 7-8 = significant for a sector or big company; 4-6 = notable; 1-3 = routine/minor.
7. "is_breaking" true only for genuinely urgent, major developments (importance >= 9) that happened just now.
8. "category": india_market | global_market | tech | india_world. "region": india | global.
9. "global_market_impact" may be "" if not relevant. Plain, simple English. No markdown.

Return a JSON object: {"articles": [ {...one object per input item, same id...} ]}"""

ITEM_SCHEMA_KEYS = (
    "id", "headline", "summary", "why_it_matters", "affected", "india_market_impact",
    "global_market_impact", "importance", "category", "region", "sentiment", "is_breaking",
)

JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["articles"],
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(ITEM_SCHEMA_KEYS),
                "properties": {
                    "id": {"type": "string"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "affected": {"type": "string"},
                    "india_market_impact": {"type": "string"},
                    "global_market_impact": {"type": "string"},
                    "importance": {"type": "integer"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "region": {"type": "string", "enum": ["india", "global"]},
                    "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
                    "is_breaking": {"type": "boolean"},
                },
            },
        }
    },
}

TAKEAWAY_SYSTEM = """You write the 'key takeaways' section of a daily news briefing.
Using ONLY the items provided, write exactly 3 short, plain-English points a busy reader should understand today.
Do not add facts that are not in the items. No investment advice, no buy/sell language, no predictions of prices.
Return JSON: {"takeaways": ["...", "...", "..."]}"""


@dataclass
class Analysis:
    headline: str
    summary: str
    why_it_matters: str
    affected: str
    india_impact: str
    global_impact: str
    importance: int
    category: str
    region: str
    sentiment: str
    is_breaking: bool
    ai_generated: bool = True

    @property
    def level(self) -> str:
        return importance_level(self.importance)

    def as_db_fields(self) -> dict[str, Any]:
        return {
            "simple_headline": self.headline,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "affected": self.affected,
            "india_impact": self.india_impact,
            "global_impact": self.global_impact,
            "importance": self.importance,
            "importance_level": self.level,
            "category": self.category,
            "region": self.region,
            "sentiment": self.sentiment,
            "is_breaking": self.is_breaking,
        }


def build_user_prompt(items: list[dict[str, Any]]) -> str:
    return "Analyse these news items:\n" + json.dumps(items, ensure_ascii=False, indent=1)


def article_to_prompt_item(a) -> dict[str, Any]:
    return {
        "id": a.id,
        "headline": a.title,
        "description": truncate(a.description or "", 900),
        "source": a.source,
        "published": a.published_at.strftime("%Y-%m-%d %H:%M UTC"),
        "feed_category": a.category,
    }


# ------------------------------------------------------------------ parsing
def extract_json(text: str) -> Any:
    """Parse JSON even if the model wrapped it in ```json fences or prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("model did not return valid JSON")


# ------------------------------------------------------------------- safety
_ADVICE_RE = re.compile(
    r"\b(you should|investors should|traders should|we recommend|i recommend|recommend(?:ed|s)? (?:buying|selling|holding)|"
    r"(?:strong )?buy (?:rating|call|signal)|sell (?:rating|call|signal)|good time to (?:buy|sell|invest)|"
    r"consider (?:buying|selling)|accumulate|book profits?|target price|price target|guaranteed returns?|"
    r"will (?:definitely|surely) (?:rise|fall|go up|go down))\b",
    re.IGNORECASE,
)


def remove_advice(text: str) -> str:
    """Drop any sentence that reads like investment advice."""
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if not _ADVICE_RE.search(s)]
    return " ".join(kept).strip()


def _clean_str(v: Any, limit: int) -> str:
    if v is None:
        return ""
    s = re.sub(r"[*_`#]+", "", str(v)).strip()  # strip markdown
    return truncate(re.sub(r"\s+", " ", s), limit)


def validate_item(raw: dict[str, Any], fallback_category: str, fallback_region: str) -> Analysis:
    try:
        importance = int(round(float(raw.get("importance", 3))))
    except (TypeError, ValueError):
        importance = 3
    importance = max(1, min(10, importance))
    category = raw.get("category") if raw.get("category") in CATEGORIES else fallback_category
    region = raw.get("region") if raw.get("region") in ("india", "global") else fallback_region
    sentiment = str(raw.get("sentiment", "uncertain")).lower()
    if sentiment not in SENTIMENTS:
        sentiment = "uncertain"
    headline = _clean_str(raw.get("headline"), 200)
    summary = remove_advice(_clean_str(raw.get("summary"), 900))
    if not headline or not summary:
        raise ValueError("missing headline/summary")
    breaking = raw.get("is_breaking") in (True, "true", "True", 1)
    return Analysis(
        headline=headline,
        summary=summary,
        why_it_matters=remove_advice(_clean_str(raw.get("why_it_matters"), 600)),
        affected=remove_advice(_clean_str(raw.get("affected"), 300)),
        india_impact=remove_advice(_clean_str(raw.get("india_market_impact"), 600)) or "Unclear at this stage.",
        global_impact=remove_advice(_clean_str(raw.get("global_market_impact"), 600)),
        importance=importance,
        category=category,
        region=region,
        sentiment=sentiment,
        is_breaking=bool(breaking and importance >= 9),
    )


def parse_batch_response(text: str, articles: list) -> tuple[dict[str, Analysis], list[str]]:
    """Returns ({article_id: Analysis}, [ids that failed validation])."""
    data = extract_json(text)
    items = data.get("articles") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("JSON has no 'articles' list")
    by_id = {a.id: a for a in articles}
    out: dict[str, Analysis] = {}
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        aid = str(raw.get("id", ""))
        if aid not in by_id and i < len(articles) and len(items) == len(articles):
            aid = articles[i].id  # model mangled the id but kept order
        art = by_id.get(aid)
        if art is None or aid in out:
            continue
        try:
            out[aid] = validate_item(raw, art.category, art.region)
        except ValueError:
            continue
    failed = [a.id for a in articles if a.id not in out]
    return out, failed


def adjust_for_source(analysis: Analysis, *, official: bool, coverage: int) -> Analysis:
    """Official announcements and widely covered stories get a small boost."""
    bonus = 0
    if official and analysis.importance >= 5:
        bonus += 1
    if coverage >= 3 and analysis.importance >= 5:
        bonus += 1
    analysis.importance = min(10, analysis.importance + bonus)
    return analysis


# --------------------------------------------------------------- fallback
def extractive_analysis(article) -> Analysis:
    """No-AI fallback: uses only the feed text, clearly labelled as such."""
    desc = article.description or ""
    summary = first_sentences(desc, 3) if desc else "Only the headline was published in the source feed."
    score = int(round(min(8.0, max(1.0, article.pre_score or 3.0))))
    return Analysis(
        headline=article.title,
        summary=remove_advice(summary) or "Only the headline was published in the source feed.",
        why_it_matters="",
        affected="",
        india_impact="",
        global_impact="",
        importance=score,
        category=article.category,
        region=article.region,
        sentiment="uncertain",
        is_breaking=False,
        ai_generated=False,
    )


def parse_takeaways(text: str) -> list[str]:
    data = extract_json(text)
    items = data.get("takeaways") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("no takeaways list")
    out = [remove_advice(_clean_str(x, 300)) for x in items if isinstance(x, (str, int, float))]
    out = [x for x in out if x]
    if not out:
        raise ValueError("empty takeaways")
    return out[:3]
