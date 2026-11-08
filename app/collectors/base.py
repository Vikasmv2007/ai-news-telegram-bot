"""Shared types for collectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str
    category: str
    region: str = "global"
    weight: int = 1
    official: bool = False
    aggregator: bool = False  # Google News: real publisher is per-item


@dataclass
class RawArticle:
    """An item exactly as read from a feed (before filtering/AI)."""

    title: str
    url: str
    source: str
    published_at: datetime  # naive UTC
    category: str
    region: str
    description: str = ""
    feed_name: str = ""
    weight: int = 1
    official: bool = False


@dataclass
class FeedResult:
    feed: FeedSpec
    articles: list[RawArticle] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
