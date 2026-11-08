from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import BASE_DIR, Settings, load_sources
from app.database.db import Database
from app.database.models import Article, utcnow
from app.services.formatter import OutMessage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="123456:TEST-TOKEN-abcdefghijklmnop",
        allowed_chat_ids=[111, 222],
        admin_chat_ids=[111],
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        sources_file=BASE_DIR / "config" / "sources.yaml",
        quiet_hours="",
        default_watchlist=["TCS.NS"],
    )


@pytest.fixture
def db(settings) -> Database:
    d = Database(settings.database_url)
    d.init()
    return d


@pytest.fixture
def sources(settings):
    return load_sources(settings.sources_file)


class FakeSender:
    def __init__(self, fail_for: set[int] | None = None, block: set[int] | None = None):
        self.sent: list[tuple[int, list[OutMessage]]] = []
        self.fail_for = fail_for or set()
        self.block = block or set()

    async def send(self, chat_id, messages):
        from app.services.telegram_service import UserBlocked

        if chat_id in self.block:
            raise UserBlocked("blocked")
        if chat_id in self.fail_for:
            return 0
        self.sent.append((chat_id, list(messages)))
        return len(messages)

    def for_chat(self, chat_id):
        return [m for c, msgs in self.sent if c == chat_id for m in msgs]


@pytest.fixture
def fake_sender():
    return FakeSender()


def make_article(i: int = 1, **kw) -> Article:
    now = utcnow()
    base = dict(
        id=f"a{i}", url=f"https://example.com/{i}", canonical_url=f"https://example.com/{i}",
        title=f"Story number {i} about markets", title_fp=f"fp{i}", description="Some description.",
        source="Test Source", feed_name="Test", source_weight=2, is_official=False, category="india_market",
        region="india", published_at=now - timedelta(minutes=30), collected_at=now, processing_status="processed",
        pre_score=5.0, coverage_count=1, importance=7, importance_level="MEDIUM", simple_headline=f"Headline {i}",
        summary=f"Summary {i}.", why_it_matters="It matters.", affected="Banks.", india_impact="May affect banks.",
        global_impact="", sentiment="mixed", is_breaking=False, ai_provider="fake", matched_symbols="",
    )
    base.update(kw)
    return Article(**base)


@pytest.fixture
def article_factory():
    return make_article
