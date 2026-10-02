"""Per-client pacing. IP-wide coordination between processes belongs to the caller."""

from __future__ import annotations

import asyncio
import math
import time


class RateLimiter:
    def __init__(self, requests_per_second: float | None) -> None:
        if requests_per_second is not None and (
            isinstance(requests_per_second, bool)
            or not math.isfinite(requests_per_second)
            or requests_per_second <= 0
        ):
            raise ValueError("requests_per_second must be finite and positive, or None")
        self._interval = 0 if requests_per_second is None else 1 / requests_per_second
        self._next_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            delay = self._next_request - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + self._interval
