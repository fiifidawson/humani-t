"""The wake-up signal's requirements, run against Valkey as well as memory.

A signal only says "look again" and carries no data: the event log is the truth and every
waiter also wakes on its timeout, so a lost signal costs latency, never an event. The Valkey
run needs TEST_VALKEY_URL; pub/sub ignores the database index, so each test uses its own
prefix.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections.abc import AsyncIterator

import pytest

from app.signals.base import Signals
from app.signals.memory import MemorySignals
from app.signals.valkey import ValkeySignals

_VALKEY_URL = os.environ.get("TEST_VALKEY_URL")
# Long enough for a pub/sub round trip on a busy machine, short enough to keep
# the suite fast. Only tests that expect *no* wake-up wait the whole time.
_PATIENCE = 2.0
_SHORT = 0.1


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "valkey",
            marks=pytest.mark.skipif(not _VALKEY_URL, reason="TEST_VALKEY_URL not set"),
        ),
    ]
)
async def signals(request: pytest.FixtureRequest) -> AsyncIterator[Signals]:
    if request.param == "memory":
        yield MemorySignals()
        return
    assert _VALKEY_URL is not None
    valkey = ValkeySignals.from_url(_VALKEY_URL, prefix=f"test:{uuid.uuid4().hex}:")
    yield valkey
    await valkey.close()


async def _waiting(signals: Signals, channel: str, seconds: float) -> asyncio.Task[bool]:
    """Start a waiter and give it time to subscribe before anything is published."""
    task = asyncio.create_task(signals.wait(channel, seconds))
    await asyncio.sleep(_SHORT)
    return task


class TestSignalsContract:
    async def test_a_publish_wakes_a_waiter_on_its_channel(self, signals):
        waiter = await _waiting(signals, "run:1", _PATIENCE)

        await signals.publish("run:1")

        assert await waiter is True

    async def test_a_wait_with_nothing_published_times_out_quietly(self, signals):
        assert await signals.wait("run:1", _SHORT) is False

    async def test_a_publish_on_another_channel_does_not_wake_a_waiter(self, signals):
        waiter = await _waiting(signals, "run:1", 0.5)

        await signals.publish("run:2")

        assert await waiter is False

    async def test_every_waiter_on_a_channel_is_woken(self, signals):
        waiters = [await _waiting(signals, "runs:queued", _PATIENCE) for _ in range(3)]

        await signals.publish("runs:queued")

        assert await asyncio.gather(*waiters) == [True, True, True]

    async def test_publishing_with_no_one_waiting_is_harmless(self, signals):
        await signals.publish("run:nobody")

        assert await signals.wait("run:nobody", _SHORT) is False


class TestValkeyOutage:
    async def test_an_unreachable_valkey_degrades_to_timeouts_not_errors(self):
        """The log is the truth; with no signals the stream and worker fall back to polling."""
        unreachable = ValkeySignals.from_url("redis://127.0.0.1:1/0", prefix="test:")
        try:
            await unreachable.publish("run:1")
            assert await unreachable.wait("run:1", _SHORT) is False
        finally:
            await unreachable.close()


class TestMemoryAcrossThreads:
    def test_a_publish_from_another_event_loop_wakes_the_waiter(self):
        """The test client serves each request on its own loop; a decision must still wake."""
        signals = MemorySignals()
        result: list[bool] = []

        async def wait() -> None:
            result.append(await signals.wait("run:1", _PATIENCE))

        waiter = threading.Thread(target=lambda: asyncio.run(wait()))
        waiter.start()
        asyncio.run(asyncio.sleep(_SHORT))
        asyncio.run(signals.publish("run:1"))
        waiter.join()

        assert result == [True]
