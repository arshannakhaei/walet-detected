"""A small async rate limiter so free-tier API quotas are not exceeded."""

import asyncio
import time


class RateLimiter:
    """Spaces calls at least `1 / rate` seconds apart."""

    def __init__(self, rate_per_second: float):
        self._interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + self._interval
