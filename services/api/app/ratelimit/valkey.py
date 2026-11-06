"""Sliding-window limiter shared by every API worker, stored in Valkey.

Each key is a sorted set of event timestamps. The check-and-add runs as one Lua
script, so two workers cannot both admit the last allowed event.

An outage is reported as `RateLimiterUnavailableError`, quickly: short socket
timeouts and no client-side retries, because a sign-in waiting on a dead Valkey
is worse than one refused with 503 and a `Retry-After`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.domain.errors import RateLimiterUnavailableError
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
_SOCKET_TIMEOUT_SECONDS = 2


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
        client = Redis.from_url(
            url,
            socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            retry=Retry(NoBackoff(), 0),
        )
        return cls(client, clock)

    async def _run(
        self, key: str, limit: int, window: timedelta, *, record: bool
    ) -> RateDecision:
        now_ms = int(self._clock().timestamp() * 1000)
        window_ms = int(window.total_seconds() * 1000)
        try:
            allowed, retry_ms = await self._script(
                keys=[_PREFIX + key],
                args=[now_ms, window_ms, limit, "1" if record else "0", uuid.uuid4().hex],
            )
        except (RedisError, OSError) as exc:
            raise RateLimiterUnavailableError("rate limiter unreachable") from exc
        return RateDecision(bool(allowed), timedelta(milliseconds=int(retry_ms)))

    async def hit(self, key: str, limit: int, window: timedelta) -> RateDecision:
        return await self._run(key, limit, window, record=True)

    async def check(self, key: str, limit: int, window: timedelta) -> RateDecision:
        return await self._run(key, limit, window, record=False)

    async def reset(self, key: str) -> None:
        try:
            await self._client.delete(_PREFIX + key)
        except (RedisError, OSError) as exc:
            raise RateLimiterUnavailableError("rate limiter unreachable") from exc

    async def close(self) -> None:
        await self._client.aclose()
