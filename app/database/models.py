"""Database tables (SQLAlchemy 2.0). Works with SQLite and PostgreSQL.

All datetimes are stored as *naive UTC* so SQLite and PostgreSQL behave the same.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(canonical_url)[:20]
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_fp: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200))
    feed_name: Mapped[str] = mapped_column(String(200), default="")
    source_weight: Mapped[int] = mapped_column(Integer, default=1)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(32), index=True)
    region: Mapped[str] = mapped_column(String(16), default="global")
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Pipeline state
    processing_status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    # new | processed | skipped (low value) | duplicate | failed
    delivery_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | delivered
    duplicate_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    coverage_count: Mapped[int] = mapped_column(Integer, default=1)  # how many sources ran the story
    pre_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_symbols: Mapped[str] = mapped_column(Text, default="")  # comma-separated watchlist symbols
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # AI output
    simple_headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected: Mapped[str | None] = mapped_column(Text, nullable=True)
    india_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    global_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    importance_level: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HIGH | MEDIUM | LOW
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_articles_canonical", "canonical_url", unique=False),
        Index("ix_articles_status_published", "processing_status", "published_at"),
    )

    @property
    def display_headline(self) -> str:
        return self.simple_headline or self.title


class UserPreference(Base):
    __tablename__ = "user_preferences"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(200), default="")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    breaking_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_slots: Mapped[str] = mapped_column(Text, default="")  # comma-separated slot keys
    disabled_categories: Mapped[str] = mapped_column(Text, default="")
    message_format: Mapped[str] = mapped_column(String(16), default="cards")  # cards | compact
    watchlist: Mapped[str] = mapped_column(Text, default="")  # comma-separated symbols
    last_now_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @staticmethod
    def _split(v: str) -> list[str]:
        return [x for x in (v or "").split(",") if x]

    @property
    def disabled_slot_set(self) -> set[str]:
        return set(self._split(self.disabled_slots))

    @property
    def disabled_category_set(self) -> set[str]:
        return set(self._split(self.disabled_categories))

    @property
    def watchlist_list(self) -> list[str]:
        return self._split(self.watchlist)


class Delivery(Base):
    """Which article was sent to which chat (prevents repeats)."""

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # slot key | breaking | command
    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "article_id", "kind", name="uq_delivery"),)


class SlotRun(Base):
    """One row per (chat, schedule, local date). The unique constraint is what
    guarantees a briefing is never sent twice, even after restarts."""

    __tablename__ = "slot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    slot_key: Mapped[str] = mapped_column(String(32))
    run_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD in the bot timezone
    status: Mapped[str] = mapped_column(String(16), default="started")  # started | sent | skipped | failed
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "slot_key", "run_date", name="uq_slot_run"),)


class FeedStatus(Base):
    __tablename__ = "feed_status"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)


class KeyValue(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
