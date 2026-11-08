"""Structured logging (plain text or JSON lines) to console + rotating file."""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

_CONFIGURED = False
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


class RedactTokenFilter(logging.Filter):
    """Never let the Telegram bot token leak into logs (httpx logs full URLs)."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) > 8]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            redacted = msg
            for s in self.secrets:
                redacted = redacted.replace(s, "***")
            if redacted != msg:
                record.msg, record.args = redacted, ()
        return True


def setup_logging(level: str = "INFO", fmt: str = "text", log_dir: Path | None = None,
                  secrets: list[str] | None = None) -> None:
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(level)
        return
    root.setLevel(level)
    formatter: logging.Formatter = (
        JsonFormatter() if fmt == "json" else TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    redact = RedactTokenFilter(secrets or [])
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redact)
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.addFilter(redact)
        root.addHandler(fh)
    # httpx logs every request URL at INFO (including the bot token) - keep it quiet.
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.ExtBot"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
