"""Spacing requests to one source so this process keeps to its published rate limit.

A pacer serialises requests as well as spacing them: arXiv asks for a single
connection at a time, and one at a time is the simplest rule that never
exceeds any source's per-second limit.

The limit is per process. Several API or worker processes each keep their own
pace, so a deployment with N of them can reach N times the rate; a shared
limiter (Valkey) is the fix when that matters.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager


class Pacer:
    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("Pacer interval_seconds must not be negative")
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_finished: float | None = None

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        """Wait for this request's turn; the next one waits until the interval has passed."""
        async with self._lock:
            if self._last_finished is not None:
                wait = self._last_finished + self.interval_seconds - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                yield
            finally:
                # Measured from the end of a request, not its start: a slow
                # response must not let the next one start early.
                self._last_finished = self._clock()
