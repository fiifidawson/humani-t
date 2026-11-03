"""Sliding-window limiter shared by every API worker, stored in Valkey.

Each key is a sorted set of event timestamps. The check-and-add runs as one Lua
script, so two workers cannot both admit the last allowed event.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from app.ratelimit.base import RateDecision

_HIT = """
local key, now, window, limit, record = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]),
    tonumber(ARGV[3]), ARGV[4] == '1'
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return {0, tonumber(oldest[2]) + window - now}
end
if record then
    redis.call('ZADD', key, now, ARGV[5])
    redis.call('PEXPIRE', key, window)
end
return {1, 0}
"""
_PREFIX = "ratelimit:"


class ValkeyRateLimiter:
    def __init__(
        self, client: Redis, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._client = client
        self._clock = clock
        self._script: Any = client.register_script(_HIT)

    @classmethod
    def from_url(
        cls, url: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> ValkeyRateLimiter:
        return cls(Redis.from_url(url), clock)

    async def _run(
        self, key: str, limit: int, window: timedelta, *, record: bool
    ) -> RateDecision:
        now_ms = int(self._clock().timestamp() * 1000)
        window_ms = int(window.total_seconds() * 1000)
        allowed, retry_ms = await self._script(
            keys=[_PREFIX + key],
            args=[now_ms, window_ms, limit, "1" if record else "0", uuid.uuid4().hex],
        )
        return RateDecision(bool(allowed), timedelta(milliseconds=int(retry_ms)))

    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        return await self._run(key, limit, window, record=True)

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        return await self._run(key, limit, window, record=False)

    async def reset(self, key: str) -> None:
        await self._client.delete(_PREFIX + key)

    async def close(self) -> None:
        await self._client.aclose()
