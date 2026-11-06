"""The rate limiter's requirements, run against Valkey as well as memory.

The Valkey run is skipped unless TEST_VALKEY_URL points at a Valkey (or Redis)
server, e.g. the one in compose.yaml.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemoryRateLimiter
from app.ratelimit.valkey import ValkeyRateLimiter
from tests.support import FakeClock

_VALKEY_URL = os.environ.get("TEST_VALKEY_URL")
_WINDOW = timedelta(minutes=15)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "valkey",
            marks=pytest.mark.skipif(not _VALKEY_URL, reason="TEST_VALKEY_URL not set"),
        ),
    ]
)
async def limiter(
    request: pytest.FixtureRequest, clock: FakeClock
) -> AsyncIterator[RateLimiter]:
    if request.param == "memory":
        yield MemoryRateLimiter(clock)
        return
    assert _VALKEY_URL is not None
    valkey = ValkeyRateLimiter.from_url(_VALKEY_URL, clock)
    yield valkey
    await valkey.close()


@pytest.fixture
def key() -> str:
    """A fresh key per test, so runs against a shared server never interfere."""
    return f"test:{uuid.uuid4()}"


class TestRateLimiterContract:
    async def test_the_limit_is_exact(self, limiter, key):
        decisions = [await limiter.hit(key, 3, _WINDOW) for _ in range(4)]

        assert [d.allowed for d in decisions] == [True, True, True, False]
        assert timedelta(0) < decisions[-1].retry_after <= _WINDOW

    async def test_check_does_not_count(self, limiter, key):
        for _ in range(5):
            assert (await limiter.check(key, 1, _WINDOW)).allowed

        assert (await limiter.hit(key, 1, _WINDOW)).allowed

    async def test_the_window_slides(self, limiter, key, clock):
        for _ in range(3):
            await limiter.hit(key, 3, _WINDOW)

        clock.advance(minutes=16)

        assert (await limiter.hit(key, 3, _WINDOW)).allowed

    async def test_refused_attempts_do_not_extend_the_block(self, limiter, key, clock):
        for _ in range(3):
            await limiter.hit(key, 3, _WINDOW)
        for _ in range(50):
            clock.advance(seconds=10)
            await limiter.hit(key, 3, _WINDOW)

        clock.advance(minutes=8)  # 15 min 20 s after the counted attempts

        assert (await limiter.hit(key, 3, _WINDOW)).allowed

    async def test_reset_clears_the_key(self, limiter, key):
        for _ in range(3):
            await limiter.hit(key, 3, _WINDOW)

        await limiter.reset(key)

        assert (await limiter.hit(key, 3, _WINDOW)).allowed

    async def test_concurrent_hits_never_exceed_the_limit(self, limiter, key):
        decisions = await asyncio.gather(*(limiter.hit(key, 10, _WINDOW) for _ in range(50)))

        assert sum(d.allowed for d in decisions) == 10


class TestMemoryLimiterIsBounded:
    async def test_checking_a_key_does_not_start_tracking_it(self, clock):
        limiter = MemoryRateLimiter(clock)

        for n in range(100):
            await limiter.check(f"login:{n}", 5, _WINDOW)

        assert len(limiter) == 0

    async def test_expired_keys_are_forgotten(self, clock):
        limiter = MemoryRateLimiter(clock)
        for n in range(5000):
            await limiter.hit(f"old:{n}", 5, _WINDOW)

        clock.advance(minutes=16)
        for n in range(5000):
            await limiter.hit(f"new:{n}", 5, _WINDOW)

        assert len(limiter) <= 5000
