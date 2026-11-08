"""Shared HTTP helpers built on httpx."""
from __future__ import annotations

import httpx

from app.utils.retry import RetryableError, retry_async

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def make_client(user_agent: str, timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": user_agent, "Accept": "*/*"},
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


async def get_with_retry(client: httpx.AsyncClient, url: str, *, attempts: int = 3, **kw) -> httpx.Response:
    """GET with retries on network errors and transient HTTP statuses.

    Non-transient errors (404, 403, ...) raise httpx.HTTPStatusError immediately.
    """

    async def once() -> httpx.Response:
        try:
            resp = await client.get(url, **kw)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise RetryableError(f"network error: {type(e).__name__}") from e
        if resp.status_code in RETRY_STATUS:
            raise RetryableError(f"HTTP {resp.status_code}", retry_after=_retry_after(resp))
        resp.raise_for_status()
        return resp

    return await retry_async(once, attempts=attempts, base_delay=1.5, what=f"GET {httpx.URL(url).host}")
