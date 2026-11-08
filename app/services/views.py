"""On-demand news views (/india, /global, /tech, /market, /trending, /today, /now).

Used both by the Telegram commands and by `python -m app.main --send VIEW`
(GitHub Actions "Run workflow" menu), so both produce identical messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import CATEGORY_TITLES
from app.database.models import UserPreference, utcnow
from app.services.container import Services
from app.services.formatter import OutMessage, build_list, format_snapshot

VIEWS = ("now", "today", "india", "global", "market", "tech", "trending")


@dataclass
class View:
    messages: list[OutMessage]
    article_ids: list[str] = field(default_factory=list)
    kind: str = "command"  # delivery kind recorded in the database
    empty_reason: str | None = None


async def build_view(services: Services, name: str, user: UserPreference) -> View:
    s, news, db = services.settings, services.news, services.db
    since = utcnow() - timedelta(hours=s.article_max_age_hours)
    now = datetime.now(timezone.utc)
    fmt = user.message_format or "cards"

    if name == "now":
        b = await services.briefing.latest(user, now)
        if b.skipped:
            return View([OutMessage("✅ You're up to date - no important new stories since your last update.", silent=False)],
                        empty_reason=b.skip_reason)
        return View(b.messages, b.article_ids, kind="now")
    if name == "today":
        b = await services.briefing.today(user, now)
        return View(b.messages, b.article_ids)

    snapshot_kind = None
    if name == "india":
        title, items = "🇮🇳 INDIAN NEWS", news.items_by_region("india", since, 8)
    elif name == "global":
        title, items = "🌍 INTERNATIONAL NEWS", news.items_by_region("global", since, 8)
    elif name == "tech":
        title, items = CATEGORY_TITLES["tech"], db.processed_articles(since=since, categories=["tech"], min_importance=3, limit=8)
    elif name == "trending":
        title, items = "🔥 TRENDING NOW", news.trending(utcnow() - timedelta(hours=24), 8)
    elif name == "market":
        n = s.items_per_category
        items = (db.processed_articles(since=since, categories=["india_market"], min_importance=4, limit=n)
                 + db.processed_articles(since=since, categories=["global_market"], min_importance=4, limit=n))
        title, snapshot_kind = "📈 MARKET NEWS (INDIA + GLOBAL)", "full"
    else:
        raise ValueError(f"unknown view {name!r}; choose from {', '.join(VIEWS)}")

    msgs: list[OutMessage] = []
    if snapshot_kind:
        groups = await news.market_snapshot(snapshot_kind)
        snap = format_snapshot(groups, s.tz, getattr(news.market, "name", "market data"))
        if snap:
            msgs.append(OutMessage(snap, silent=False))
    msgs += build_list(title, items, s.tz, fmt)
    return View(msgs, [a.id for a in items])
