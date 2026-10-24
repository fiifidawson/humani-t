"""In-process sliding-window limiter for tests and single-process development.

Each API worker would hold its own counts, so production uses Valkey instead
(Settings refuses to start without it).
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.ratelimit.base import RateDecision


class MemoryRateLimiter:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._events: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def _window(self, key: str, window: timedelta) -> deque[datetime]:
        events = self._events[key]
        cutoff = self._clock() - window
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def _decide(self, events: deque[datetime], limit: int, window: timedelta) -> RateDecision:
        if len(events) < limit:
            return RateDecision(allowed=True)
        return RateDecision(allowed=False, retry_after=events[0] + window - self._clock())

    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        events = self._window(key, window)
        decision = self._decide(events, limit, window)
        if decision.allowed:
            events.append(self._clock())
        return decision

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        return self._decide(self._window(key, window), limit, window)

    async def reset(self, key: str) -> None:
        self._events.pop(key, None)

    async def close(self) -> None:
        return None
