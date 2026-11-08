"""Entry point.

    python -m app.main                  run the bot 24/7 (polling + scheduler)
    python -m app.main --test-mode      same, with sample news / fake AI (no keys needed except Telegram)
    python -m app.main --preview [slot] print a briefing in the terminal (no Telegram needed)
    python -m app.main --check-config   validate .env
    python -m app.main --check-feeds    test every news feed and show which ones work
    python -m app.main --check-ai       send one small test request to the configured AI provider(s)
    python -m app.main --run-slot KEY   one-shot: refresh + send one briefing, then exit
                                        (KEY = morning|premarket|midday|closing|evening|auto; add --force to resend)
    python -m app.main --send VIEW      one-shot: send now|today|india|global|market|tech|trending, then exit
"""
from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone

from app.config import BASE_DIR, load_settings, parse_hhmm
from app.database.models import UserPreference
from app.utils.logger import setup_logging

log = logging.getLogger("app")


def _console_text(msg) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", msg.text))
    for label, url in msg.buttons:
        text += f"\n   [{label}] {url}"
    return text


def _ensure_users(services) -> None:
    for cid in services.settings.allowed_chat_ids:
        services.db.get_or_create_user(cid, default_watchlist=services.settings.default_watchlist)


async def cmd_preview(settings, slot_key: str) -> int:
    from app.services.container import create_services

    services = create_services(settings)
    try:
        print("Collecting and analysing news… (this can take a minute)\n")
        stats = await services.news.refresh()
        print(f"Pipeline: {stats.summary()}\n")
        if stats.errors:
            print("Errors:", *stats.errors, sep="\n  ")
        user = UserPreference(chat_id=0, message_format="cards", disabled_slots="", disabled_categories="", watchlist="")
        now = datetime.now(timezone.utc)
        if slot_key == "today":
            b = await services.briefing.today(user, now)
        else:
            slot = settings.slot(slot_key)
            if slot is None:
                print(f"Unknown slot {slot_key!r}. Use: today, " + ", ".join(s.key for s in settings.slots))
                return 2
            b = await services.briefing.for_slot(slot, user, now)
        if b.skipped:
            print(f"(This briefing would be skipped: {b.skip_reason})")
        for m in b.messages:
            print(_console_text(m))
            print()
        return 0
    finally:
        await services.close()


async def cmd_check_feeds(settings) -> int:
    from app.collectors.rss_collector import build_feed_specs, collect_all
    from app.config import load_sources
    from app.utils.http import make_client

    feeds = build_feed_specs(load_sources(settings.sources_file))
    async with make_client(settings.http_user_agent) as client:
        results = await collect_all(client, feeds, max_age_hours=settings.article_max_age_hours)
    ok = 0
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        detail = f"{len(r.articles)} recent items" if r.ok else r.error
        print(f"[{mark}] {r.feed.name[:55]:<55} {detail}")
        ok += r.ok
    print(f"\n{ok}/{len(results)} feeds working. Failing feeds are skipped automatically; edit config/sources.yaml to change them.")
    return 0 if ok else 1


async def cmd_check_ai(settings) -> int:
    from app.ai.providers import build_providers
    from app.utils.http import make_client

    async with make_client(settings.http_user_agent) as client:
        providers = build_providers(settings, client)
        if not providers:
            print("No AI provider configured. The bot will still work using feed text only.\n"
                  "For free AI summaries set GEMINI_API_KEY (https://aistudio.google.com/apikey) and/or GROQ_API_KEY.")
            return 1
        ok = 0
        for p in providers:
            try:
                out = await p.complete_json('Reply with JSON only.', 'Return {"ok": true}')
                print(f"[OK  ] {p.name}: {out.strip()[:80]}")
                ok += 1
            except Exception as e:
                print(f"[FAIL] {p.name}: {e}")
        return 0 if ok else 1


def cmd_check_config(settings) -> int:
    problems = settings.validate(require_telegram=True)
    keys = {"Gemini (free)": settings.gemini_api_key, "Groq (free)": settings.groq_api_key,
            "Anthropic Claude (paid)": settings.anthropic_api_key}
    print("Configuration")
    print(f"  Timezone        : {settings.timezone}")
    print(f"  Database        : {settings.database_url}")
    print(f"  Telegram token  : {'set' if settings.telegram_bot_token else 'MISSING'}")
    print(f"  Allowed chats   : {settings.allowed_chat_ids or 'NONE'}")
    print(f"  LLM provider    : {settings.llm_provider}")
    for k, v in keys.items():
        print(f"    {k:<24}: {'key set' if v else '-'}")
    print("  Schedules       : " + ", ".join(f"{s.key}@{s.time}{'' if s.enabled_by_default else '(off)'}" for s in settings.slots))
    print(f"  Test mode       : {settings.test_mode}")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll good.")
    return 0


def _auto_slot(settings, now: datetime) -> str | None:
    local = now.astimezone(settings.tz)
    best, best_delta = None, None
    for s in settings.slots:
        if not s.enabled_by_default:
            continue
        h, m = parse_hhmm(s.time)
        delta = abs((local - local.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds()) / 60
        if delta <= 50 and (best_delta is None or delta < best_delta):
            best, best_delta = s.key, delta
    return best


async def cmd_send_view(settings, view: str) -> int:
    """One-shot: refresh news and send one on-demand view (india, tech, ...) to every allowed chat."""
    from telegram import Bot

    from app.services.container import create_services
    from app.services.telegram_service import TelegramSender, UserBlocked
    from app.services.views import VIEWS, build_view

    if view not in VIEWS:
        print(f"Unknown view {view!r}. Choose from: {', '.join(VIEWS)}")
        return 2
    services = create_services(settings)
    _ensure_users(services)
    try:
        async with Bot(settings.telegram_bot_token) as bot:
            sender = TelegramSender(bot)
            await services.news.refresh()
            for user in services.db.active_users(settings.allowed_chat_ids):
                v = await build_view(services, view, user)
                try:
                    sent = await sender.send(user.chat_id, v.messages)
                except UserBlocked:
                    print(f"{user.chat_id}: blocked the bot (send /start to it)")
                    continue
                if sent and v.article_ids:
                    services.db.record_deliveries(user.chat_id, v.article_ids, v.kind)
                print(f"{view} -> {user.chat_id}: {sent}/{len(v.messages)} messages")
        return 0
    finally:
        await services.close()


async def cmd_run_slot(settings, key: str, force: bool = False) -> int:
    from telegram import Bot

    from app.services.container import create_services
    from app.services.scheduler import DeliveryScheduler
    from app.services.telegram_service import TelegramSender

    if key == "auto":
        key = _auto_slot(settings, datetime.now(timezone.utc)) or ""
        if not key:
            print("No briefing is due right now; only collecting news and checking breaking alerts.")
    services = create_services(settings)
    _ensure_users(services)
    try:
        async with Bot(settings.telegram_bot_token) as bot:
            sched = DeliveryScheduler(settings, services.db, services.news, services.briefing, TelegramSender(bot))
            await services.news.refresh()
            await sched.breaking_check(datetime.now(timezone.utc))
            if key:
                print(f"{key}: {await sched.run_slot(key, force=force)}")
        return 0
    finally:
        await services.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.main", description="AI News Intelligence Telegram Bot")
    ap.add_argument("--test-mode", action="store_true", help="sample news + fake AI + sample prices")
    ap.add_argument("--preview", nargs="?", const="morning", metavar="SLOT", help="print a briefing to the console")
    ap.add_argument("--check-config", action="store_true")
    ap.add_argument("--check-feeds", action="store_true")
    ap.add_argument("--check-ai", action="store_true")
    ap.add_argument("--run-slot", metavar="KEY", help="send one briefing and exit (for cron / GitHub Actions)")
    ap.add_argument("--force", action="store_true", help="with --run-slot: send even if already sent today")
    ap.add_argument("--send", metavar="VIEW", help="send one view and exit: now|today|india|global|market|tech|trending")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    if args.test_mode:
        settings = replace(settings, test_mode=True)
    setup_logging(settings.log_level, settings.log_format, BASE_DIR / "logs",
                  secrets=[settings.telegram_bot_token, settings.gemini_api_key, settings.groq_api_key, settings.anthropic_api_key])

    if args.check_config:
        return cmd_check_config(settings)
    if args.check_feeds:
        return asyncio.run(cmd_check_feeds(settings))
    if args.check_ai:
        return asyncio.run(cmd_check_ai(settings))
    if args.preview:
        return asyncio.run(cmd_preview(settings, args.preview))

    problems = settings.validate(require_telegram=True)
    token_missing = not settings.telegram_bot_token
    if token_missing:
        print("TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and fill it in "
              "(or run `python -m app.main --preview --test-mode` to try it without Telegram).", file=sys.stderr)
        return 2
    for p in problems:
        log.warning(p)

    if args.run_slot:
        return asyncio.run(cmd_run_slot(settings, args.run_slot, args.force))
    if args.send:
        return asyncio.run(cmd_send_view(settings, args.send.lower()))

    from app.bot.telegram_bot import run_bot
    from app.services.container import create_services

    def make_services():
        services = create_services(settings)
        _ensure_users(services)
        return services

    try:
        run_bot(make_services)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
