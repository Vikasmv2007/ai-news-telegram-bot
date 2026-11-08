"""Tiny HTTP health endpoint (no extra dependencies).

GET /health -> 200 {"status": "ok", ...} or 503 if the database is down or
news hasn't refreshed for 3 collection intervals. Useful for Docker
HEALTHCHECK, uptime monitors and cloud hosts that require an open port.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.database.models import utcnow

log = logging.getLogger(__name__)


def health_report(services) -> tuple[int, dict]:
    db_ok = services.db.ping()
    last = services.db.kv_get("last_refresh_at") if db_ok else None
    stale = True
    if last:
        stale = utcnow() - datetime.fromisoformat(last) > timedelta(minutes=services.settings.collect_interval_minutes * 3 + 10)
    status = "ok" if db_ok and (not stale or last is None) else "degraded"
    body = {
        "status": status,
        "database": "ok" if db_ok else "error",
        "last_refresh_at": last,
        "news_stale": bool(last) and stale,
        "ai": services.summarizer.status(),
        "test_mode": services.settings.test_mode,
    }
    return (200 if status == "ok" else 503), body


async def start_health_server(services, port: int) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), 5)).decode(errors="ignore")
            while (await asyncio.wait_for(reader.readline(), 5)) not in (b"\r\n", b"\n", b""):
                pass
            path = request_line.split(" ")[1] if " " in request_line else "/"
            if path.startswith("/health") or path == "/":
                code, body = health_report(services)
            else:
                code, body = 404, {"error": "not found"}
            payload = json.dumps(body).encode()
            reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[code]
            writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        except Exception as e:
            log.debug("health request error: %s", e)
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host="0.0.0.0", port=port)
    log.info("health endpoint listening on :%d/health", port)
    return server
