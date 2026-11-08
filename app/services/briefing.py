"""Assembles briefings (scheduled and on-demand) from stored, analysed news."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Sequence

from app.config import CATEGORIES, Settings, SlotConfig
from app.database.models import Article, UserPreference
from app.services.formatter import OutMessage, build_briefing, format_snapshot
from app.services.news_service import NewsService

log = logging.getLogger(__name__)


class Briefing:
    def __init__(self, messages: list[OutMessage], article_ids: list[str], skip_reason: str | None = None):
        self.messages = messages
        self.article_ids = article_ids
        self.skip_reason = skip_reason

    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


class BriefingService:
    def __init__(self, settings: Settings, news: NewsService):
        self.settings = settings
        self.news = news

    @property
    def tz(self):
        return self.settings.tz

    async def _assemble(self, *, title: str, now: datetime, grouped: dict[str, list[Article]],
                        snapshot_kind: str, user: UserPreference) -> Briefing:
        snapshot = ""
        if snapshot_kind != "none":
            groups = await self.news.market_snapshot(snapshot_kind)
            provider = getattr(self.news.market, "name", "market data")
            snapshot = format_snapshot(groups, self.tz, provider)
        flat = [a for items in grouped.values() for a in items]
        takeaways = await self.news.takeaways(flat) if flat else []
        msgs = build_briefing(title=title, now=now, tz=self.tz, grouped=grouped, takeaways=takeaways,
                              snapshot=snapshot, fmt=user.message_format or "cards")
        return Briefing(msgs, [a.id for a in flat])

    async def for_slot(self, slot: SlotConfig, user: UserPreference, now: datetime) -> Briefing:
        since = _naive_utc(now) - timedelta(hours=self.settings.article_max_age_hours)
        grouped = self.news.items_by_category(
            since=since, categories=slot.categories, min_importance=slot.min_importance,
            per_category=self.settings.items_per_category, exclude_delivered_to=user.chat_id,
            disabled=user.disabled_category_set,
        )
        if not grouped and slot.skip_if_empty:
            return Briefing([], [], skip_reason="nothing new and important")
        return await self._assemble(title=slot.title, now=now, grouped=grouped,
                                    snapshot_kind=slot.market_snapshot, user=user)

    async def today(self, user: UserPreference, now: datetime) -> Briefing:
        local = now.astimezone(self.tz)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        since = _naive_utc(midnight) if local.hour >= 6 else _naive_utc(midnight - timedelta(hours=12))
        grouped = self.news.items_by_category(
            since=since, categories=CATEGORIES, min_importance=4,
            per_category=self.settings.items_per_category, disabled=user.disabled_category_set,
        )
        return await self._assemble(title="📰 TODAY'S COMPLETE BRIEFING", now=now, grouped=grouped,
                                    snapshot_kind="full", user=user)

    async def latest(self, user: UserPreference, now: datetime, categories: Sequence[str] = CATEGORIES) -> Briefing:
        """/now: newest stories this chat hasn't been sent yet."""
        since = _naive_utc(now) - timedelta(hours=self.settings.article_max_age_hours)
        grouped = self.news.items_by_category(
            since=since, categories=categories, min_importance=3, per_category=3,
            exclude_delivered_to=user.chat_id, disabled=user.disabled_category_set,
        )
        if not grouped:
            return Briefing([], [], skip_reason="no new stories")
        return await self._assemble(title="⚡ LATEST NEWS", now=now, grouped=grouped, snapshot_kind="none", user=user)
