"""Application configuration.

All settings come from environment variables (loaded from a `.env` file via
python-dotenv). Nothing secret is ever hard-coded here.

The list of news sources lives in `config/sources.yaml` so you can add or
remove feeds without touching Python code.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CATEGORIES = ("india_market", "global_market", "tech", "india_world")

CATEGORY_TITLES = {
    "india_market": "📈 INDIAN STOCK MARKET",
    "global_market": "🌍 GLOBAL MARKET UPDATES",
    "tech": "🤖 TECHNOLOGY & AI",
    "india_world": "🇮🇳 INDIA & WORLD NEWS",
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}")


def _env_int_list(name: str) -> list[int]:
    raw = _env(name)
    out: list[int] = []
    for part in re.split(r"[,\s]+", raw):
        if part:
            try:
                out.append(int(part))
            except ValueError:
                raise ValueError(f"{name} must be a comma-separated list of numeric chat IDs")
    return out


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> tuple[int, int]:
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM (24-hour), e.g. 07:00")
    return int(m.group(1)), int(m.group(2))


@dataclass(frozen=True)
class SlotConfig:
    """One scheduled delivery (e.g. the 7:00 AM morning briefing)."""

    key: str
    time: str  # "HH:MM" in the configured timezone
    title: str
    categories: tuple[str, ...]
    market_snapshot: str  # "none" | "global" | "india" | "closing" | "full"
    market_day_only: bool  # skip on weekends
    min_importance: int  # skip items below this score
    skip_if_empty: bool  # don't send anything if there's nothing new
    enabled_by_default: bool = True


DEFAULT_SLOTS: tuple[SlotConfig, ...] = (
    SlotConfig("morning", "07:00", "🌅 GOOD MORNING | MARKET INTELLIGENCE",
               CATEGORIES, "global", False, 4, False),
    SlotConfig("premarket", "09:00", "🔔 INDIAN PRE-MARKET BRIEFING",
               ("india_market", "global_market"), "global", True, 5, False),
    SlotConfig("midday", "13:00", "⏱️ MIDDAY MARKET DEVELOPMENTS",
               ("india_market", "global_market"), "india", True, 6, True),
    SlotConfig("closing", "16:00", "🔔 INDIAN MARKET CLOSING SUMMARY",
               ("india_market",), "closing", True, 5, False),
    SlotConfig("evening", "20:00", "🌙 EVENING DIGEST | TECH, BUSINESS & WORLD",
               ("tech", "india_world", "global_market"), "none", False, 4, False),
)


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str = ""
    allowed_chat_ids: list[int] = field(default_factory=list)
    admin_chat_ids: list[int] = field(default_factory=list)

    # AI
    llm_provider: str = "auto"  # auto | gemini | groq | anthropic | none
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    ai_batch_size: int = 8
    ai_max_articles_per_run: int = 24
    ai_timeout_seconds: int = 60
    ai_min_interval_seconds: float = 6.0  # spacing between LLM calls (free-tier friendly)

    # Pipeline
    timezone: str = "Asia/Kolkata"
    database_url: str = ""
    collect_interval_minutes: int = 30
    article_max_age_hours: int = 36
    breaking_alerts_enabled: bool = True
    breaking_min_importance: int = 9
    breaking_max_per_day: int = 4
    quiet_hours: str = "23:00-06:30"
    items_per_category: int = 4
    market_data_enabled: bool = True
    default_watchlist: list[str] = field(default_factory=list)

    # Schedule
    slots: tuple[SlotConfig, ...] = DEFAULT_SLOTS
    catch_up_minutes: int = 45

    # Ops
    test_mode: bool = False
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    health_port: int = 0  # 0 = disabled
    sources_file: Path = BASE_DIR / "config" / "sources.yaml"
    http_user_agent: str = "NewsIntelligenceBot/1.0 (personal RSS reader)"

    @property
    def tz(self):
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.timezone)

    def slot(self, key: str) -> SlotConfig | None:
        return next((s for s in self.slots if s.key == key), None)

    def validate(self, require_telegram: bool = True) -> list[str]:
        """Return a list of human-readable problems (empty list = OK)."""
        problems: list[str] = []
        if require_telegram and not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set (get one from @BotFather).")
        if require_telegram and not self.allowed_chat_ids:
            problems.append(
                "ALLOWED_CHAT_IDS is empty. Start the bot, send /start, and it will tell you your chat ID."
            )
        if self.llm_provider not in ("auto", "gemini", "groq", "anthropic", "none"):
            problems.append("LLM_PROVIDER must be one of: auto, gemini, groq, anthropic, none.")
        try:
            self.tz
        except Exception:
            problems.append(f"TIMEZONE {self.timezone!r} is not a valid IANA timezone.")
        for s in self.slots:
            try:
                parse_hhmm(s.time)
            except ValueError as e:
                problems.append(f"Schedule '{s.key}': {e}")
        return problems


def _load_slots() -> tuple[SlotConfig, ...]:
    slots = []
    for s in DEFAULT_SLOTS:
        t = _env(f"SCHEDULE_{s.key.upper()}", s.time)
        enabled = _env_bool(f"SCHEDULE_{s.key.upper()}_ENABLED", s.enabled_by_default)
        parse_hhmm(t)  # fail fast on bad values
        slots.append(SlotConfig(**{**s.__dict__, "time": t, "enabled_by_default": enabled}))
    return tuple(slots)


def load_settings() -> Settings:
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    db_url = _env("DATABASE_URL") or f"sqlite:///{(data_dir / 'news.db').as_posix()}"
    watch = [w.strip().upper() for w in _env("DEFAULT_WATCHLIST", "RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS").split(",") if w.strip()]
    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids=_env_int_list("ALLOWED_CHAT_IDS"),
        admin_chat_ids=_env_int_list("ADMIN_CHAT_IDS") or _env_int_list("ALLOWED_CHAT_IDS"),
        llm_provider=_env("LLM_PROVIDER", "auto").lower(),
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-flash-latest"),
        groq_api_key=_env("GROQ_API_KEY"),
        groq_model=_env("GROQ_MODEL", "llama-3.3-70b-versatile"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-opus-5-5"),
        ai_batch_size=max(1, min(12, _env_int("AI_BATCH_SIZE", 8))),
        ai_max_articles_per_run=max(1, _env_int("AI_MAX_ARTICLES_PER_RUN", 24)),
        ai_timeout_seconds=_env_int("AI_TIMEOUT_SECONDS", 60),
        timezone=_env("TIMEZONE", "Asia/Kolkata"),
        database_url=db_url,
        collect_interval_minutes=max(5, _env_int("COLLECT_INTERVAL_MINUTES", 30)),
        article_max_age_hours=_env_int("ARTICLE_MAX_AGE_HOURS", 36),
        breaking_alerts_enabled=_env_bool("BREAKING_ALERTS_ENABLED", True),
        breaking_min_importance=_env_int("BREAKING_MIN_IMPORTANCE", 9),
        breaking_max_per_day=_env_int("BREAKING_MAX_PER_DAY", 4),
        quiet_hours=_env("QUIET_HOURS", "23:00-06:30"),
        items_per_category=max(1, _env_int("ITEMS_PER_CATEGORY", 4)),
        market_data_enabled=_env_bool("MARKET_DATA_ENABLED", True),
        default_watchlist=watch,
        slots=_load_slots(),
        catch_up_minutes=_env_int("CATCH_UP_MINUTES", 45),
        test_mode=_env_bool("TEST_MODE", False),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_format=_env("LOG_FORMAT", "text").lower(),
        health_port=_env_int("HEALTH_PORT", 0) or _env_int("PORT", 0),
    )


def load_sources(path: Path) -> dict[str, Any]:
    """Load feeds + official sources + watchlist aliases from YAML."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("feeds", [])
    data.setdefault("google_news_queries", [])
    data.setdefault("company_aliases", {})
    data.setdefault("market_symbols", {})
    for feed in data["feeds"]:
        if feed.get("category") not in CATEGORIES:
            raise ValueError(f"Feed {feed.get('name')!r} has invalid category {feed.get('category')!r}")
    return data
