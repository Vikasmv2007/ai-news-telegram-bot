"""Automatic scheduling (IST by default).

Jobs registered on python-telegram-bot's JobQueue (APScheduler underneath):
  * collect     every COLLECT_INTERVAL_MINUTES: refresh news, then check for
                genuinely important breaking news
  * slot:<key>  daily briefings at the configured times (07:00, 09:00, ...)
  * purge       daily clean-up of data older than 30 days
  * catch-up    once at startup: if the bot was down at a briefing time
                (within CATCH_UP_MINUTES), send it now

Never-twice guarantee: before sending a briefing, a row (chat, slot, date) is
inserted into `slot_runs`, which has a UNIQUE constraint. A restart, a second
job firing, or catch-up all hit the same constraint, so only one wins.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, time as dtime, timedelta, timezone

from app.config import Settings, SlotConfig, parse_hhmm
from app.database.db import Database
from app.database.models import utcnow
from app.services.briefing import BriefingService
from app.services.formatter import build_breaking
from app.services.news_service import NewsService
from app.services.telegram_service import TelegramSender, UserBlocked

log = logging.getLogger(__name__)

FRESHNESS_MINUTES = 10
MAX_SLOT_RETRIES = 3


def parse_quiet_hours(spec: str) -> tuple[dtime, dtime] | None:
    spec = (spec or "").strip()
    if not spec or spec.lower() in ("none", "off"):
        return None
    start, end = spec.split("-", 1)
    sh, sm = parse_hhmm(start)
    eh, em = parse_hhmm(end)
    return dtime(sh, sm), dtime(eh, em)


def in_quiet_hours(local_now: datetime, spec: str) -> bool:
    rng = parse_quiet_hours(spec)
    if not rng:
        return False
    start, end = rng
    t = local_now.time()
    return (start <= t < end) if start <= end else (t >= start or t < end)


class DeliveryScheduler:
    def __init__(self, settings: Settings, db: Database, news: NewsService, briefing: BriefingService,
                 sender: TelegramSender):
        self.settings = settings
        self.db = db
        self.news = news
        self.briefing = briefing
        self.sender = sender
        self._retries: dict[tuple[str, str], int] = {}
        self.job_queue = None
        self.last_job_error: str | None = None

    @property
    def tz(self):
        return self.settings.tz

    def enabled_slots(self) -> list[SlotConfig]:
        return [s for s in self.settings.slots if s.enabled_by_default]

    # ---------------------------------------------------------------- jobs
    async def ensure_fresh(self) -> None:
        last = self.news.last_run_at
        if self.news.busy:
            async with self.news._lock:  # wait for the running refresh to finish
                return
        if last is None or utcnow() - last > timedelta(minutes=FRESHNESS_MINUTES):
            await self.news.refresh()

    async def collect_job(self) -> None:
        try:
            await self.news.refresh()
            await self.breaking_check(datetime.now(timezone.utc))
        except Exception as e:
            self.last_job_error = f"collect: {e}"
            log.exception("collect job failed")

    async def run_slot(self, key: str, now: datetime | None = None, force: bool = False) -> dict[int, str]:
        """Deliver one scheduled briefing to every eligible user. Returns {chat_id: status}.

        force=True (manual "Run workflow"): send even if this briefing already went
        out today, on weekends, or when the user disabled it."""
        now = now or datetime.now(timezone.utc)
        slot = self.settings.slot(key)
        results: dict[int, str] = {}
        if slot is None:
            return results
        local = now.astimezone(self.tz)
        run_date = local.date().isoformat()
        if slot.market_day_only and local.weekday() >= 5 and not force:
            log.info("slot %s skipped: weekend (market closed)", key)
            return results
        try:
            await self.ensure_fresh()
        except Exception:
            log.exception("refresh before slot %s failed; using stored news", key)

        for user in self.db.active_users(self.settings.allowed_chat_ids):
            if key in user.disabled_slot_set and not force:
                continue
            if force:
                pass  # manual run: no once-per-day reservation
            elif not self.db.claim_slot_run(user.chat_id, key, run_date):
                results[user.chat_id] = "already-sent"
                continue
            try:
                b = await self.briefing.for_slot(slot, user, now)
                if b.skipped and force:
                    b = await self.briefing.for_slot(replace(slot, skip_if_empty=False), user, now)
                if b.skipped:
                    self.db.finish_slot_run(user.chat_id, key, run_date, "skipped", b.skip_reason or "")
                    results[user.chat_id] = "skipped"
                    continue
                sent = await self.sender.send(user.chat_id, b.messages)
                if sent == 0:
                    raise RuntimeError("no messages could be delivered")
                self.db.record_deliveries(user.chat_id, b.article_ids, key)
                status = "sent" if sent == len(b.messages) else "partial"
                if not force:
                    self.db.finish_slot_run(user.chat_id, key, run_date, status, f"{sent}/{len(b.messages)} messages")
                results[user.chat_id] = status
                log.info("slot delivered", extra={"slot": key, "chat": user.chat_id, "messages": sent})
            except UserBlocked:
                self.db.update_user(user.chat_id, is_paused=True)
                self.db.finish_slot_run(user.chat_id, key, run_date, "failed", "user blocked the bot")
                results[user.chat_id] = "blocked"
            except Exception as e:
                log.exception("slot %s failed for %s", key, user.chat_id)
                results[user.chat_id] = "failed"
                if not force:
                    self.db.release_slot_run(user.chat_id, key, run_date)  # allow a retry
                    self._schedule_retry(key, run_date, str(e))
        return results

    def _schedule_retry(self, key: str, run_date: str, err: str) -> None:
        n = self._retries.get((key, run_date), 0)
        if self.job_queue is None or n >= MAX_SLOT_RETRIES:
            self.last_job_error = f"slot {key}: {err}"
            return
        self._retries[(key, run_date)] = n + 1
        delay = 300 * (n + 1)
        log.warning("retrying slot %s in %ds (retry %d/%d)", key, delay, n + 1, MAX_SLOT_RETRIES)
        self.job_queue.run_once(self._slot_callback, when=delay, data=key, name=f"retry:{key}")

    async def breaking_check(self, now: datetime) -> int:
        s = self.settings
        if not s.breaking_alerts_enabled:
            return 0
        local = now.astimezone(self.tz)
        if in_quiet_hours(local, s.quiet_hours):
            return 0
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        budget = s.breaking_max_per_day - self.db.alerts_since(day_start)
        if budget <= 0:
            return 0
        recent = now.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        candidates = self.db.breaking_candidates(recent, s.breaking_min_importance)[:budget]
        users = [u for u in self.db.active_users(s.allowed_chat_ids) if u.breaking_alerts]
        sent_alerts = 0
        for art in candidates:
            for u in users:
                if art.category in u.disabled_category_set or self.db.was_delivered(u.chat_id, art.id):
                    continue
                try:
                    if await self.sender.send(u.chat_id, build_breaking(art, self.tz)):
                        self.db.record_deliveries(u.chat_id, [art.id], "breaking")
                except UserBlocked:
                    self.db.update_user(u.chat_id, is_paused=True)
            self.db.mark_alerted(art.id)
            sent_alerts += 1
        return sent_alerts

    async def catch_up(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self.tz)
        ran = []
        for slot in self.enabled_slots():
            h, m = parse_hhmm(slot.time)
            slot_dt = local.replace(hour=h, minute=m, second=0, microsecond=0)
            delta = (local - slot_dt).total_seconds() / 60
            if 0 <= delta <= self.settings.catch_up_minutes:
                log.info("catch-up: running missed slot %s", slot.key)
                await self.run_slot(slot.key, now)
                ran.append(slot.key)
        return ran

    async def purge_job(self) -> None:
        try:
            n = self.db.purge_older_than(30)
            log.info("purged %d old articles", n)
        except Exception:
            log.exception("purge failed")

    # ----------------------------------------------------- PTB integration
    async def _collect_callback(self, context) -> None:
        await self.collect_job()

    async def _slot_callback(self, context) -> None:
        try:
            await self.run_slot(context.job.data)
        except Exception as e:
            self.last_job_error = f"slot {context.job.data}: {e}"
            log.exception("slot job crashed")

    async def _catch_up_callback(self, context) -> None:
        try:
            await self.collect_job()
            await self.catch_up()
        except Exception:
            log.exception("catch-up failed")

    async def _purge_callback(self, context) -> None:
        await self.purge_job()

    def register(self, job_queue) -> None:
        self.job_queue = job_queue
        tz = self.tz
        job_queue.run_once(self._catch_up_callback, when=10, name="startup")
        job_queue.run_repeating(self._collect_callback, interval=self.settings.collect_interval_minutes * 60,
                                first=self.settings.collect_interval_minutes * 60, name="collect")
        for slot in self.enabled_slots():
            h, m = parse_hhmm(slot.time)
            job_queue.run_daily(self._slot_callback, time=dtime(h, m, tzinfo=tz), data=slot.key, name=f"slot:{slot.key}")
            log.info("scheduled %s at %s %s", slot.key, slot.time, self.settings.timezone)
        job_queue.run_daily(self._purge_callback, time=dtime(3, 30, tzinfo=tz), name="purge")
