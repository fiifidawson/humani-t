"""The rate-limiting seam: a sliding window per key."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after: timedelta = timedelta(0)


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        """Count one event if the key is under its limit; refused events are not counted.

        Not counting refusals means a flood of attempts cannot keep a key
        blocked indefinitely: the window drains on schedule regardless.
        """
        ...

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        """Report whether one more event would be allowed, without counting it."""
        ...

    async def reset(self, key: str) -> None: ...

    async def close(self) -> None: ...
