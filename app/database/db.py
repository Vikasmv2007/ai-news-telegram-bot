"""Database access layer.

`Database` owns the SQLAlchemy engine and exposes small, focused methods so
the rest of the app never writes SQL directly. Swap SQLite for PostgreSQL by
changing DATABASE_URL only.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterable, Iterator, Sequence

from sqlalchemy import and_, create_engine, delete, event, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Article, Base, Delivery, FeedStatus, KeyValue, SlotRun, UserPreference, utcnow

log = logging.getLogger(__name__)


class DatabaseError(RuntimeError):
    pass


class Database:
    def __init__(self, url: str):
        self.url = url
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        elif url.startswith("postgres://"):
            # Many hosts hand out postgres:// URLs; SQLAlchemy needs postgresql://
            url = "postgresql://" + url[len("postgres://"):]
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        self._Session = sessionmaker(self.engine, expire_on_commit=False)

    def init(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except IntegrityError:
            s.rollback()
            raise
        except SQLAlchemyError as e:
            s.rollback()
            log.error("database error: %s", e)
            raise DatabaseError(str(e)) from e
        finally:
            s.close()

    def ping(self) -> bool:
        try:
            with self.session() as s:
                s.execute(select(1))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ users
    def get_or_create_user(self, chat_id: int, username: str = "", default_watchlist: Sequence[str] = ()) -> UserPreference:
        with self.session() as s:
            user = s.get(UserPreference, chat_id)
            if user is None:
                user = UserPreference(chat_id=chat_id, username=username or "", watchlist=",".join(default_watchlist))
                s.add(user)
            elif username and user.username != username:
                user.username = username
            s.flush()
            return user

    def get_user(self, chat_id: int) -> UserPreference | None:
        with self.session() as s:
            return s.get(UserPreference, chat_id)

    def update_user(self, chat_id: int, **fields) -> UserPreference:
        with self.session() as s:
            user = s.get(UserPreference, chat_id)
            if user is None:
                user = UserPreference(chat_id=chat_id)
                s.add(user)
            for k, v in fields.items():
                setattr(user, k, v)
            s.flush()
            return user

    def active_users(self, allowed_ids: Iterable[int]) -> list[UserPreference]:
        ids = list(allowed_ids)
        if not ids:
            return []
        with self.session() as s:
            rows = s.scalars(select(UserPreference).where(UserPreference.chat_id.in_(ids), UserPreference.is_paused.is_(False)))
            return list(rows)

    # --------------------------------------------------------------- articles
    def get_article(self, article_id: str) -> Article | None:
        with self.session() as s:
            return s.get(Article, article_id)

    def existing_ids(self, ids: Iterable[str]) -> set[str]:
        ids = list(set(ids))
        found: set[str] = set()
        with self.session() as s:
            for i in range(0, len(ids), 500):
                found.update(s.scalars(select(Article.id).where(Article.id.in_(ids[i:i + 500]))))
        return found

    def recent_titles(self, since: datetime) -> list[tuple[str, str, str]]:
        """(id, title_fp, title) of non-duplicate articles collected since `since`."""
        with self.session() as s:
            rows = s.execute(
                select(Article.id, Article.title_fp, Article.title).where(
                    Article.collected_at >= since, Article.processing_status != "duplicate"
                )
            )
            return [tuple(r) for r in rows]

    def add_articles(self, articles: Iterable[Article]) -> int:
        n = 0
        for a in articles:
            try:
                with self.session() as s:
                    s.add(a)
                n += 1
            except IntegrityError:
                pass  # already stored by a concurrent run
        return n

    def bump_coverage(self, article_id: str, by: int = 1, official: bool = False, weight: int = 0) -> None:
        with self.session() as s:
            a = s.get(Article, article_id)
            if a:
                a.coverage_count = (a.coverage_count or 1) + by
                if official:
                    a.is_official = True
                if weight > (a.source_weight or 0):
                    a.source_weight = weight

    def pending_for_ai(self, since: datetime, limit: int, max_attempts: int = 3) -> list[Article]:
        with self.session() as s:
            q = (
                select(Article)
                .where(
                    Article.processing_status.in_(("new", "failed")),
                    Article.published_at >= since,
                    Article.attempts < max_attempts,
                )
                .order_by(Article.pre_score.desc(), Article.published_at.desc())
                .limit(limit)
            )
            return list(s.scalars(q))

    def save_analysis(self, article_id: str, *, provider: str, **fields) -> None:
        with self.session() as s:
            a = s.get(Article, article_id)
            if not a:
                return
            for k, v in fields.items():
                setattr(a, k, v)
            a.ai_provider = provider
            a.processing_status = "processed"
            a.processed_at = utcnow()
            a.ai_error = None
            a.attempts = (a.attempts or 0) + 1

    def mark_failed(self, article_ids: Iterable[str], error: str) -> None:
        ids = list(article_ids)
        if not ids:
            return
        with self.session() as s:
            s.execute(
                update(Article)
                .where(Article.id.in_(ids))
                .values(processing_status="failed", ai_error=error[:500], attempts=Article.attempts + 1)
            )

    def mark_status(self, article_ids: Iterable[str], status: str) -> None:
        ids = list(article_ids)
        if ids:
            with self.session() as s:
                s.execute(update(Article).where(Article.id.in_(ids)).values(processing_status=status))

    def processed_articles(
        self,
        *,
        since: datetime,
        categories: Sequence[str] | None = None,
        region: str | None = None,
        min_importance: int = 1,
        exclude_delivered_to: int | None = None,
        limit: int = 50,
        symbols: Sequence[str] | None = None,
    ) -> list[Article]:
        with self.session() as s:
            conds = [
                Article.processing_status == "processed",
                Article.published_at >= since,
                func.coalesce(Article.importance, 0) >= min_importance,
            ]
            if categories:
                conds.append(Article.category.in_(list(categories)))
            if region:
                conds.append(Article.region == region)
            if exclude_delivered_to is not None:
                delivered = select(Delivery.article_id).where(
                    Delivery.chat_id == exclude_delivered_to, Delivery.kind != "command"
                )
                conds.append(Article.id.not_in(delivered))
            q = select(Article).where(and_(*conds))
            rows = list(s.scalars(q.order_by(Article.importance.desc(), Article.coverage_count.desc(), Article.published_at.desc()).limit(limit * 4 if symbols else limit)))
            if symbols:
                wanted = set(symbols)
                rows = [r for r in rows if wanted & set(filter(None, (r.matched_symbols or "").split(",")))][:limit]
            return rows

    def breaking_candidates(self, since: datetime, min_importance: int) -> list[Article]:
        with self.session() as s:
            q = select(Article).where(
                Article.processing_status == "processed",
                Article.alerted_at.is_(None),
                Article.published_at >= since,
                Article.importance >= min_importance,
                Article.is_breaking.is_(True),
            ).order_by(Article.importance.desc(), Article.published_at.desc())
            return list(s.scalars(q))

    def mark_alerted(self, article_id: str) -> None:
        with self.session() as s:
            s.execute(update(Article).where(Article.id == article_id).values(alerted_at=utcnow()))

    def alerts_since(self, since: datetime) -> int:
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(Article).where(Article.alerted_at >= since)) or 0

    # -------------------------------------------------------------- deliveries
    def record_deliveries(self, chat_id: int, article_ids: Iterable[str], kind: str) -> None:
        ids = list(dict.fromkeys(article_ids))
        for aid in ids:
            try:
                with self.session() as s:
                    s.add(Delivery(chat_id=chat_id, article_id=aid, kind=kind))
            except IntegrityError:
                pass
        if ids:
            with self.session() as s:
                s.execute(update(Article).where(Article.id.in_(ids)).values(delivery_status="delivered"))

    def was_delivered(self, chat_id: int, article_id: str) -> bool:
        with self.session() as s:
            return s.scalar(
                select(func.count()).select_from(Delivery).where(
                    Delivery.chat_id == chat_id, Delivery.article_id == article_id, Delivery.kind != "command"
                )
            ) > 0

    def claim_slot_run(self, chat_id: int, slot_key: str, run_date: str) -> bool:
        """Atomically reserve a scheduled delivery. Returns False if it already ran."""
        try:
            with self.session() as s:
                s.add(SlotRun(chat_id=chat_id, slot_key=slot_key, run_date=run_date))
            return True
        except IntegrityError:
            return False

    def finish_slot_run(self, chat_id: int, slot_key: str, run_date: str, status: str, detail: str = "") -> None:
        with self.session() as s:
            s.execute(
                update(SlotRun)
                .where(SlotRun.chat_id == chat_id, SlotRun.slot_key == slot_key, SlotRun.run_date == run_date)
                .values(status=status, detail=detail[:500])
            )

    def release_slot_run(self, chat_id: int, slot_key: str, run_date: str) -> None:
        """Delete a failed claim so a later retry/catch-up can try again."""
        with self.session() as s:
            s.execute(delete(SlotRun).where(SlotRun.chat_id == chat_id, SlotRun.slot_key == slot_key, SlotRun.run_date == run_date))

    def slot_run_status(self, chat_id: int, slot_key: str, run_date: str) -> str | None:
        with self.session() as s:
            return s.scalar(select(SlotRun.status).where(SlotRun.chat_id == chat_id, SlotRun.slot_key == slot_key, SlotRun.run_date == run_date))

    # ------------------------------------------------------------ feed health
    def record_feed_result(self, name: str, url: str, ok: bool, count: int = 0, error: str | None = None) -> None:
        with self.session() as s:
            fs = s.get(FeedStatus, name)
            if fs is None:
                fs = FeedStatus(name=name, url=url, consecutive_failures=0, last_item_count=0)
                s.add(fs)
            fs.url = url
            if ok:
                fs.last_ok_at = utcnow()
                fs.consecutive_failures = 0
                fs.last_item_count = count
            else:
                fs.last_error = (error or "")[:500]
                fs.last_error_at = utcnow()
                fs.consecutive_failures = (fs.consecutive_failures or 0) + 1

    def feed_statuses(self) -> list[FeedStatus]:
        with self.session() as s:
            return list(s.scalars(select(FeedStatus).order_by(FeedStatus.name)))

    # --------------------------------------------------------------------- kv
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self.session() as s:
            row = s.get(KeyValue, key)
            return row.value if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self.session() as s:
            row = s.get(KeyValue, key)
            if row:
                row.value = value
                row.updated_at = utcnow()
            else:
                s.add(KeyValue(key=key, value=value))

    # ------------------------------------------------------------------ stats
    def stats(self, since: datetime) -> dict[str, int]:
        with self.session() as s:
            rows = s.execute(
                select(Article.processing_status, func.count()).where(Article.collected_at >= since).group_by(Article.processing_status)
            )
            out = {k: v for k, v in rows}
            out["total_all_time"] = s.scalar(select(func.count()).select_from(Article)) or 0
            return out

    def purge_older_than(self, days: int = 30) -> int:
        cutoff = utcnow() - timedelta(days=days)
        with self.session() as s:
            old_ids = select(Article.id).where(Article.collected_at < cutoff)
            s.execute(delete(Delivery).where(Delivery.article_id.in_(old_ids)))
            res = s.execute(delete(Article).where(Article.collected_at < cutoff))
            s.execute(delete(SlotRun).where(SlotRun.created_at < cutoff))
            return res.rowcount or 0
