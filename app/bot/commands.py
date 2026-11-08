"""Telegram command handlers.

Security: only chat IDs listed in ALLOWED_CHAT_IDS can use the bot; admin-only
commands (/status, /preview) additionally require ADMIN_CHAT_IDS. Everyone
else gets a polite refusal that shows their chat ID (so the owner can add it).
"""
from __future__ import annotations

import functools
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.ai.analyzer import extractive_analysis
from app.config import CATEGORIES, CATEGORY_TITLES
from app.database.models import Article, utcnow
from app.services.container import Services
from app.services.formatter import (
    DISCLAIMER, OutMessage, build_list, esc, fmt_local, format_card, format_quote, format_snapshot,
)
from app.services.sample_data import sample_articles
from app.services.scheduler import DeliveryScheduler
from app.services.telegram_service import TelegramSender, UserBlocked
from app.services.views import build_view

log = logging.getLogger(__name__)

NOW_COOLDOWN = timedelta(minutes=3)
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.^=&\-]{0,19}$")
MAX_WATCHLIST = 25

HELP_TEXT = """<b>🤖 AI News Intelligence Bot</b>

I collect news from trusted sources, remove duplicates and junk, use AI to explain what matters, and send you clean briefings.

<b>📬 News</b>
/now - latest stories you haven't seen yet
/today - today's complete briefing
/market - Indian &amp; global market news + snapshot
/india - Indian news
/global - international news
/tech - technology &amp; AI news
/trending - most important stories right now

<b>⚙️ Personal</b>
/watchlist - your companies (add/remove: <code>/watchlist add TCS.NS</code>)
/settings - schedules, alerts, categories, layout
/stop - pause automatic delivery
/resume - resume automatic delivery
/help - this message

<b>🕒 Automatic schedule ({tz})</b>
{schedule}

Breaking alerts are sent only for genuinely major developments (importance ≥ {breaking}/10).

<i>Not financial advice. I never tell you to buy or sell anything.</i>"""


def _schedule_lines(settings) -> str:
    return "\n".join(f"• {s.time} - {esc(s.title)}" + ("" if s.enabled_by_default else " <i>(disabled)</i>")
                     for s in settings.slots)


class BotCommands:
    def __init__(self, services: Services, sender: TelegramSender, scheduler: DeliveryScheduler):
        self.s = services
        self.sender = sender
        self.scheduler = scheduler

    # ------------------------------------------------------------ helpers
    @property
    def settings(self):
        return self.s.settings

    def _allowed(self, chat_id: int) -> bool:
        return chat_id in self.settings.allowed_chat_ids

    def _admin(self, chat_id: int) -> bool:
        return chat_id in self.settings.admin_chat_ids

    async def _deny(self, update: Update) -> None:
        cid = update.effective_chat.id
        log.warning("unauthorised access attempt", extra={"chat": cid})
        if update.effective_message:
            await update.effective_message.reply_text(
                f"🔒 This is a private bot.\n\nYour chat ID is <code>{cid}</code>.\n"
                "If this is your bot, add this number to <code>ALLOWED_CHAT_IDS</code> in the .env file and restart it.",
                parse_mode=ParseMode.HTML,
            )

    def _user(self, update: Update):
        u = update.effective_user
        return self.s.db.get_or_create_user(update.effective_chat.id, (u.username or u.first_name or "") if u else "",
                                            self.settings.default_watchlist)

    async def _send(self, update: Update, msgs: list[OutMessage], kind: str | None = None,
                    article_ids: list[str] | None = None) -> None:
        chat_id = update.effective_chat.id
        try:
            sent = await self.sender.send(chat_id, msgs)
        except UserBlocked:
            return
        if sent and kind and article_ids:
            self.s.db.record_deliveries(chat_id, article_ids, kind)

    def _since(self, hours: int | None = None) -> datetime:
        return utcnow() - timedelta(hours=hours or self.settings.article_max_age_hours)

    async def _ensure_some_news(self, update: Update) -> None:
        """First use after install: make sure there's something in the DB."""
        if self.s.news.last_run_at is None and not self.s.news.busy:
            await update.effective_message.reply_text("⏳ Collecting news for the first time, this takes about a minute…")
            await self.s.news.refresh()

    # ----------------------------------------------------------- commands
    @staticmethod
    def guarded(admin: bool = False):
        def deco(fn):
            @functools.wraps(fn)
            async def wrapper(self: "BotCommands", update: Update, context: ContextTypes.DEFAULT_TYPE):
                if update.effective_chat is None:
                    return
                cid = update.effective_chat.id
                if not self._allowed(cid) or (admin and not self._admin(cid)):
                    return await self._deny(update)
                try:
                    return await fn(self, update, context)
                except Exception:
                    log.exception("command %s failed", fn.__name__)
                    if update.effective_message:
                        await update.effective_message.reply_text("⚠️ Something went wrong. It has been logged; please try again shortly.")
            return wrapper
        return deco

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        if not self._allowed(update.effective_chat.id):
            return await self._deny(update)
        self._user(update)
        await update.effective_message.reply_text(
            HELP_TEXT.format(tz=esc(self.settings.timezone), schedule=_schedule_lines(self.settings),
                             breaking=self.settings.breaking_min_importance),
            parse_mode=ParseMode.HTML,
        )

    @guarded()
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            HELP_TEXT.format(tz=esc(self.settings.timezone), schedule=_schedule_lines(self.settings),
                             breaking=self.settings.breaking_min_importance),
            parse_mode=ParseMode.HTML,
        )

    @guarded()
    async def now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        last = user.last_now_at
        if last and utcnow() - last < NOW_COOLDOWN and not self.s.news.busy:
            wait = int((NOW_COOLDOWN - (utcnow() - last)).total_seconds())
            await update.effective_message.reply_text(f"⏱️ Just refreshed - showing stored news (fresh fetch available in {wait}s).")
        else:
            self.s.db.update_user(user.chat_id, last_now_at=utcnow())
            await update.effective_message.reply_text("⏳ Fetching the latest news… (up to a minute)")
            await self.scheduler.ensure_fresh() if self.s.news.busy else await self.s.news.refresh()
        b = await self.s.briefing.latest(user, datetime.now(timezone.utc))
        if b.skipped:
            await update.effective_message.reply_text("✅ You're up to date - no important new stories since your last update.\nTry /today for today's full briefing.")
            return
        await self._send(update, b.messages, "now", b.article_ids)

    @guarded()
    async def today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        await self._ensure_some_news(update)
        b = await self.s.briefing.today(user, datetime.now(timezone.utc))
        await self._send(update, b.messages, "command", b.article_ids)

    async def _view_cmd(self, update: Update, name: str) -> None:
        await self._ensure_some_news(update)
        v = await build_view(self.s, name, self._user(update))
        await self._send(update, v.messages, v.kind, v.article_ids)

    @guarded()
    async def market(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._view_cmd(update, "market")

    @guarded()
    async def india(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._view_cmd(update, "india")

    @guarded()
    async def global_(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._view_cmd(update, "global")

    @guarded()
    async def tech(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._view_cmd(update, "tech")

    @guarded()
    async def trending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._view_cmd(update, "trending")

    @guarded()
    async def watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        args = [a.strip() for a in (context.args or [])]
        current = user.watchlist_list
        if args:
            action = args[0].lower()
            symbols = [a.upper() for a in args[1:]]
            if action not in ("add", "remove", "rm", "del") or not symbols:
                await update.effective_message.reply_text(
                    "Usage:\n/watchlist - show\n/watchlist add RELIANCE.NS TCS.NS\n/watchlist remove TCS.NS\n\n"
                    "Use Yahoo Finance symbols: NSE stocks end with .NS, BSE with .BO, US stocks have no suffix (e.g. NVDA).")
                return
            bad = [x for x in symbols if not SYMBOL_RE.match(x)]
            if bad:
                await update.effective_message.reply_text(f"❌ Invalid symbol(s): {', '.join(bad)}")
                return
            if action == "add":
                current = list(dict.fromkeys(current + symbols))[:MAX_WATCHLIST]
            else:
                current = [c for c in current if c not in symbols]
            self.s.db.update_user(user.chat_id, watchlist=",".join(current))
            await update.effective_message.reply_text(f"✅ Watchlist updated: {', '.join(current) or '(empty)'}\n"
                                                      "News matching is applied to newly collected articles.")
            return
        if not current:
            await update.effective_message.reply_text("Your watchlist is empty. Add companies with /watchlist add RELIANCE.NS")
            return
        quotes = await self.s.news.watchlist_quotes(current)
        lines = ["👀 <b>YOUR WATCHLIST</b>", ""]
        for sym in current:
            q = quotes.get(sym)
            names = self.s.sources.get("company_aliases", {}).get(sym) or [sym]
            if q:
                lines.append(format_quote(q, self.settings.tz).replace(f"<b>{esc(q.name)}</b>", f"<b>{esc(names[0])}</b> ({esc(sym)})"))
            else:
                lines.append(f"• <b>{esc(names[0])}</b> ({esc(sym)}): <i>price unavailable</i>")
        if quotes:
            provider = next(iter(quotes.values())).provider
            lines.append(f"\n🕒 <i>Source: {esc(provider)}. Prices may be delayed; not real-time.</i>")
        msgs = [OutMessage("\n".join(lines), silent=False)]
        news = self.s.news.watchlist_news(current, self._since(48), 6)
        if news:
            msgs += build_list("📰 WATCHLIST NEWS (last 48h)", news, self.settings.tz, user.message_format)
        else:
            msgs.append(OutMessage("<i>No recent news matched your watchlist companies.</i>\n\n" + DISCLAIMER))
        await self._send(update, msgs, "command", [a.id for a in news])

    # -------------------------------------------------------------- settings
    def _settings_keyboard(self, user) -> InlineKeyboardMarkup:
        rows = []
        for slot in self.settings.slots:
            if not slot.enabled_by_default:
                continue
            on = slot.key not in user.disabled_slot_set
            rows.append([InlineKeyboardButton(f"{'✅' if on else '❌'} {slot.time} {slot.key.title()}", callback_data=f"set:slot:{slot.key}")])
        for cat in CATEGORIES:
            on = cat not in user.disabled_category_set
            rows.append([InlineKeyboardButton(f"{'✅' if on else '❌'} {CATEGORY_TITLES[cat]}", callback_data=f"set:cat:{cat}")])
        rows.append([InlineKeyboardButton(f"{'🔔' if user.breaking_alerts else '🔕'} Breaking alerts: {'ON' if user.breaking_alerts else 'OFF'}", callback_data="set:alerts")])
        rows.append([InlineKeyboardButton(f"🗂️ Layout: {'Cards (1 msg/story)' if user.message_format != 'compact' else 'Compact (1 msg/section)'}", callback_data="set:format")])
        rows.append([InlineKeyboardButton("✔️ Done", callback_data="set:done")])
        return InlineKeyboardMarkup(rows)

    def _settings_text(self, user) -> str:
        state = "⏸️ PAUSED (use /resume)" if user.is_paused else "▶️ Active"
        return (f"<b>⚙️ Settings</b>\nAutomatic delivery: {state}\n\n"
                "Tap to toggle schedules, categories, breaking alerts and layout:")

    @guarded()
    async def settings_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        await update.effective_message.reply_text(self._settings_text(user), parse_mode=ParseMode.HTML,
                                                  reply_markup=self._settings_keyboard(user))

    async def settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q is None or update.effective_chat is None:
            return
        if not self._allowed(update.effective_chat.id):
            await q.answer("Not authorised", show_alert=True)
            return
        user = self._user(update)
        parts = (q.data or "").split(":")
        if len(parts) < 2 or parts[0] != "set":
            await q.answer()
            return
        kind = parts[1]
        valid_slots = {s.key for s in self.settings.slots}
        if kind == "slot" and len(parts) == 3 and parts[2] in valid_slots:
            s = user.disabled_slot_set ^ {parts[2]}
            user = self.s.db.update_user(user.chat_id, disabled_slots=",".join(sorted(s)))
        elif kind == "cat" and len(parts) == 3 and parts[2] in CATEGORIES:
            s = user.disabled_category_set ^ {parts[2]}
            user = self.s.db.update_user(user.chat_id, disabled_categories=",".join(sorted(s)))
        elif kind == "alerts":
            user = self.s.db.update_user(user.chat_id, breaking_alerts=not user.breaking_alerts)
        elif kind == "format":
            user = self.s.db.update_user(user.chat_id, message_format="compact" if user.message_format != "compact" else "cards")
        elif kind == "done":
            await q.answer("Saved ✅")
            await q.edit_message_reply_markup(reply_markup=None)
            return
        else:
            await q.answer("Unknown option")
            return
        await q.answer("Updated")
        await q.edit_message_text(self._settings_text(user), parse_mode=ParseMode.HTML, reply_markup=self._settings_keyboard(user))

    @guarded()
    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        self.s.db.update_user(user.chat_id, is_paused=True)
        await update.effective_message.reply_text("⏸️ Automatic delivery paused. Commands like /now still work.\nUse /resume to turn briefings back on.")

    @guarded()
    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = self._user(update)
        self.s.db.update_user(user.chat_id, is_paused=False)
        await update.effective_message.reply_text("▶️ Automatic delivery resumed. Next briefings:\n" + "\n".join(
            f"• {s.time} {s.key}" for s in self.settings.slots if s.enabled_by_default and s.key not in user.disabled_slot_set))

    # ---------------------------------------------------------------- admin
    @guarded(admin=True)
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        db = self.s.db
        feeds = db.feed_statuses()
        failing = [f for f in feeds if f.consecutive_failures > 0]
        stats = db.stats(utcnow() - timedelta(hours=24))
        last = db.kv_get("last_refresh_at")
        last_txt = fmt_local(datetime.fromisoformat(last), self.settings.tz) if last else "never"
        ai = self.s.summarizer.status() or {"none": "no API key set (feed-text mode)"}
        lines = [
            "<b>🩺 Bot status</b>",
            f"Database: {'✅ OK' if db.ping() else '❌ ERROR'}",
            f"Last refresh: {last_txt}",
            f"Last run: {esc(db.kv_get('last_refresh_stats', '-'))}",
            f"AI providers: {esc(', '.join(f'{k}={v}' for k, v in ai.items()))}",
            f"Last AI error: {esc((self.s.summarizer.last_error or '-')[:200])}",
            f"Articles (24h): {esc(str(stats))}",
            f"Feeds: {len(feeds) - len(failing)}/{len(feeds)} OK",
            f"Test mode: {'ON' if self.settings.test_mode else 'off'}",
            f"Last job error: {esc(self.scheduler.last_job_error or '-')}",
        ]
        if failing:
            lines.append("\n<b>Failing feeds:</b>")
            lines += [f"• {esc(f.name)}: {esc((f.last_error or '')[:80])} (×{f.consecutive_failures})" for f in failing[:12]]
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    @guarded(admin=True)
    async def preview(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/preview <slot> - show what a scheduled briefing would look like (doesn't mark anything as sent)."""
        key = (context.args or ["morning"])[0].lower()
        slot = self.settings.slot(key)
        if slot is None:
            await update.effective_message.reply_text("Usage: /preview " + "|".join(s.key for s in self.settings.slots))
            return
        user = self._user(update)
        await self._ensure_some_news(update)
        b = await self.s.briefing.for_slot(slot, user, datetime.now(timezone.utc))
        if b.skipped:
            await update.effective_message.reply_text(f"This briefing would be skipped: {b.skip_reason}")
            return
        await self._send(update, b.messages)

    @guarded(admin=True)
    async def test(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/test - send one sample card (no network, nothing stored) to check formatting."""
        raw = sample_articles()[0]
        a = Article(id="sample", url=raw.url, canonical_url=raw.url, title=raw.title, title_fp="", description=raw.description,
                    source=raw.source, category=raw.category, region=raw.region, published_at=raw.published_at,
                    coverage_count=2, is_official=True, pre_score=7)
        an = extractive_analysis(a)
        for k, v in an.as_db_fields().items():
            setattr(a, k, v)
        a.ai_provider, a.why_it_matters = "test", "[SAMPLE] This shows where the AI's explanation appears."
        a.india_impact, a.importance, a.importance_level = "[SAMPLE] Impact analysis appears here.", 9, "HIGH"
        await self._send(update, [format_card(a, 1, self.settings.tz, section_header="🧪 FORMAT TEST"), OutMessage(DISCLAIMER)])

    async def unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and self._allowed(update.effective_chat.id):
            await update.effective_message.reply_text("I don't know that command. Send /help to see what I can do.")
