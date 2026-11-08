"""Market data (index levels, sectors, commodities, watchlist prices).

Provider: Yahoo Finance public chart endpoint (free, no API key).
  * It is UNOFFICIAL and data is typically DELAYED (~15 min for NSE/BSE).
  * Every number we show carries its timestamp and the provider name, and is
    labelled as delayed. If data can't be fetched we say so - we never show
    made-up or stale numbers as live.

To use a different provider, implement `MarketDataProvider.get_quotes`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.utils.http import get_with_retry

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
PROVIDER_NAME = "Yahoo Finance (unofficial, delayed)"


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    previous_close: float | None
    as_of: datetime  # timezone-aware
    currency: str = ""
    provider: str = PROVIDER_NAME
    delayed: bool = True

    @property
    def change(self) -> float | None:
        return None if self.previous_close in (None, 0) else self.price - self.previous_close

    @property
    def change_pct(self) -> float | None:
        if self.previous_close in (None, 0):
            return None
        return (self.price - self.previous_close) / self.previous_close * 100.0


class MarketDataProvider(Protocol):
    name: str

    async def get_quotes(self, symbols: dict[str, str]) -> dict[str, Quote]: ...


def parse_chart(payload: dict, symbol: str, name: str) -> Quote:
    """Turn a Yahoo chart JSON response into a Quote. Raises ValueError if unusable."""
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"provider error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError("no result")
    r = results[0]
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    if price is None or ts is None:
        raise ValueError("missing price/time")
    tzname = meta.get("exchangeTimezoneName") or "UTC"
    try:
        ex_tz = ZoneInfo(tzname)
    except Exception:
        ex_tz = ZoneInfo("UTC")
    as_of = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    as_of_day = as_of.astimezone(ex_tz).date()

    # previous close = last daily close from a trading day before as_of's day
    prev = None
    stamps = r.get("timestamp") or []
    closes = (((r.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    for t, c in zip(stamps, closes):
        if c is None:
            continue
        if datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(ex_tz).date() < as_of_day:
            prev = float(c)
    if prev is None:
        pc = meta.get("previousClose") or meta.get("chartPreviousClose")
        prev = float(pc) if pc else None
    return Quote(symbol=symbol, name=name, price=float(price), previous_close=prev, as_of=as_of,
                 currency=meta.get("currency") or "")


class YahooFinanceProvider:
    name = PROVIDER_NAME

    def __init__(self, client: httpx.AsyncClient, cache_seconds: int = 300):
        self.client = client
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Quote]] = {}

    async def _one(self, symbol: str, name: str) -> Quote | None:
        hit = self._cache.get(symbol)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        try:
            resp = await get_with_retry(self.client, YAHOO_CHART.format(sym=quote(symbol, safe="")), attempts=2,
                                        headers={"Accept": "application/json"})
            q = parse_chart(resp.json(), symbol, name)
            self._cache[symbol] = (time.monotonic(), q)
            return q
        except Exception as e:
            log.warning("market data unavailable for %s: %s", symbol, e)
            return None

    async def get_quotes(self, symbols: dict[str, str]) -> dict[str, Quote]:
        sem = asyncio.Semaphore(5)

        async def run(sym: str, nm: str):
            async with sem:
                return sym, await self._one(sym, nm)

        pairs = await asyncio.gather(*(run(s, n) for s, n in symbols.items()))
        return {s: q for s, q in pairs if q is not None}


class FakeMarketProvider:
    """Deterministic sample data used in TEST_MODE and tests. Clearly labelled."""

    name = "SAMPLE DATA (test mode - not real prices)"

    def __init__(self, now: datetime | None = None):
        self.now = now

    async def get_quotes(self, symbols: dict[str, str]) -> dict[str, Quote]:
        now = self.now or datetime.now(timezone.utc)
        out = {}
        for i, (s, n) in enumerate(symbols.items()):
            base = 1000.0 + 731.0 * (i + 1)
            delta = ((i % 5) - 2) * 0.0042
            out[s] = Quote(symbol=s, name=n, price=round(base * (1 + delta), 2), previous_close=base,
                           as_of=now, provider=self.name, delayed=True)
        return out
