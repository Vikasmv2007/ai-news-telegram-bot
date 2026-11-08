"""Builds and runs the Telegram bot (long polling - no public URL needed)."""
from __future__ import annotations

import asyncio
import logging
import time

from telegram import BotCommand, Update
from telegram.error import Conflict, InvalidToken, NetworkError, TimedOut
from telegram.ext import (
    AIORateLimiter, Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from app.bot.commands import BotCommands
from app.services.container import Services
from app.services.health import start_health_server
from app.services.scheduler import DeliveryScheduler
from app.services.telegram_service import TelegramSender

log = logging.getLogger(__name__)

MENU = [
    ("now", "Latest news you haven't seen"),
    ("today", "Today's complete briefing"),
    ("market", "Indian & global market news"),
    ("india", "Indian news"),
    ("global", "International news"),
    ("tech", "Technology & AI news"),
    ("trending", "Most important stories"),
    ("watchlist", "Your company watchlist"),
    ("settings", "Schedules, alerts, layout"),
    ("stop", "Pause automatic delivery"),
    ("resume", "Resume automatic delivery"),
    ("help", "Help"),
]


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        log.error("Another copy of this bot is running with the same token. Stop the other one.")
        return
    log.error("unhandled telegram error: %s", err, exc_info=err)


def build_application(services: Services) -> Application:
    settings = services.settings
    builder = ApplicationBuilder().token(settings.telegram_bot_token).concurrent_updates(True)
    try:
        builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
    except RuntimeError:
        pass  # optional extra not installed
    app = builder.build()

    sender = TelegramSender(app.bot)
    scheduler = DeliveryScheduler(settings, services.db, services.news, services.briefing, sender)
    cmds = BotCommands(services, sender, scheduler)
    app.bot_data.update(services=services, scheduler=scheduler)

    handlers = {
        "start": cmds.start, "help": cmds.help, "now": cmds.now, "today": cmds.today, "market": cmds.market,
        "india": cmds.india, "global": cmds.global_, "tech": cmds.tech, "trending": cmds.trending,
        "watchlist": cmds.watchlist, "settings": cmds.settings_cmd, "stop": cmds.stop, "resume": cmds.resume,
        "status": cmds.status, "preview": cmds.preview, "test": cmds.test,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(cmds.settings_callback, pattern=r"^set:"))
    app.add_handler(MessageHandler(filters.COMMAND, cmds.unknown))
    app.add_error_handler(_error_handler)

    async def post_init(application: Application) -> None:
        me = await application.bot.get_me()
        log.info("logged in as @%s", me.username)
        await application.bot.set_my_commands([BotCommand(c, d) for c, d in MENU])
        if application.job_queue is None:
            raise RuntimeError('JobQueue missing: install with pip install "python-telegram-bot[job-queue]"')
        scheduler.register(application.job_queue)
        if settings.health_port:
            application.bot_data["health_server"] = await start_health_server(services, settings.health_port)

    async def post_shutdown(application: Application) -> None:
        srv = application.bot_data.get("health_server")
        if srv:
            srv.close()
        await services.close()

    app.post_init = post_init
    app.post_shutdown = post_shutdown
    return app


def run_bot(make_services) -> None:
    """Run forever. If Telegram can't be reached at startup (no internet yet,
    Wi-Fi reconnecting, Telegram outage) wait and retry instead of crashing.
    Once running, python-telegram-bot itself retries polling on network errors."""
    delay = 10
    while True:
        services: Services = make_services()
        app = build_application(services)
        log.info("bot starting (press Ctrl+C to stop)")
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False, close_loop=False)
            return  # normal shutdown (Ctrl+C / SIGTERM)
        except InvalidToken:
            log.error("Telegram rejected TELEGRAM_BOT_TOKEN. Copy it again from @BotFather.")
            raise SystemExit(2)
        except (NetworkError, TimedOut) as e:
            log.error("cannot reach Telegram (%s); retrying in %ds", e, delay)
        try:
            asyncio.get_event_loop().run_until_complete(services.close())
        except Exception:
            pass
        time.sleep(delay)
        delay = min(300, delay * 2)
