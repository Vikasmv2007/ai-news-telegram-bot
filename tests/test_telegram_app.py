"""Builds the real python-telegram-bot Application (no network) and checks wiring."""
from dataclasses import replace

from telegram.ext import CallbackQueryHandler, CommandHandler

from app.bot.telegram_bot import MENU, build_application
from app.services.container import create_services


async def test_application_wiring_and_real_jobqueue(settings):
    services = create_services(replace(settings, test_mode=True))
    app = build_application(services)
    commands = {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    required = {"start", "help", "now", "today", "market", "india", "global", "tech", "trending", "watchlist",
                "settings", "stop", "resume"}
    assert required <= commands and {c for c, _ in MENU} <= commands
    assert any(isinstance(h, CallbackQueryHandler) for h in app.handlers[0])
    assert app.job_queue is not None

    # Register on PTB's real JobQueue (APScheduler) and inspect next run times.
    sched = app.bot_data["scheduler"]
    app.job_queue.set_application(app)
    await app.job_queue.start()
    try:
        sched.register(app.job_queue)
        jobs = {j.name: j for j in app.job_queue.jobs()}
        assert {"slot:morning", "slot:premarket", "slot:midday", "slot:closing", "slot:evening", "collect", "startup", "purge"} <= set(jobs)
        nxt = jobs["slot:morning"].next_t.astimezone(services.settings.tz)
        assert (nxt.hour, nxt.minute) == (7, 0)
        assert (jobs["slot:closing"].next_t.astimezone(services.settings.tz).hour) == 16
    finally:
        await app.job_queue.stop(wait=False)
        await services.close()
