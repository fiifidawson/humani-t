"""In-process sliding-window limiter for tests and single-process development.

Each API worker would hold its own counts, so production uses Valkey instead
(Settings refuses to start without it).

Memory stays bounded: `check` never creates an entry, and keys whose newest
event has left every window are swept periodically. Without the sweep, every
email address and IP ever seen would stay in memory for the life of the process.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.ratelimit.base import RateDecision

_SWEEP_EVERY = 1000


class MemoryRateLimiter:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._events: dict[str, deque[datetime]] = {}
        self._longest_window = timedelta(0)
        self._operations = 0

    def __len__(self) -> int:
        """How many keys are being tracked."""
        return len(self._events)

    def _pruned(self, key: str, window: timedelta) -> deque[datetime]:
        events = self._events.get(key, deque())
        cutoff = self._clock() - window
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def _decide(self, events: deque[datetime], limit: int, window: timedelta) -> RateDecision:
        if len(events) < limit:
            return RateDecision(allowed=True)
        return RateDecision(allowed=False, retry_after=events[0] + window - self._clock())

    def _maybe_sweep(self, window: timedelta) -> None:
        self._longest_window = max(self._longest_window, window)
        self._operations += 1
        if self._operations % _SWEEP_EVERY:
            return
        cutoff = self._clock() - self._longest_window
        stale = [
            key for key, events in self._events.items() if not events or events[-1] <= cutoff
        ]
        for key in stale:
            del self._events[key]

    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        self._maybe_sweep(window)
        events = self._pruned(key, window)
        decision = self._decide(events, limit, window)
        if decision.allowed:
            events.append(self._clock())
            self._events[key] = events
        return decision

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        self._maybe_sweep(window)
        return self._decide(self._pruned(key, window), limit, window)

    async def reset(self, key: str) -> None:
        self._events.pop(key, None)

    async def close(self) -> None:
        return None
