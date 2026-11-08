"""Async retry with exponential backoff + jitter."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)


class RetryableError(Exception):
    """Raise to signal a transient failure. `retry_after` (seconds) is honoured if set."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (RetryableError,),
    what: str = "operation",
) -> T:
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as e:  # type: ignore[misc]
            last = e
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            ra = getattr(e, "retry_after", None)
            if ra:
                delay = min(max_delay, max(delay, float(ra)))
            delay += random.uniform(0, delay * 0.25)
            log.warning("%s failed (attempt %d/%d): %s - retrying in %.1fs", what, attempt, attempts, e, delay)
            await asyncio.sleep(delay)
    assert last is not None
    raise last
