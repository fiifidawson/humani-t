"""Signals within one process, for tests and for running without Valkey.

A publish wakes waiters in this process only. With more than one process (an
API and a worker), each still finds changes by polling on its timeout.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict


class MemorySignals:
    def __init__(self) -> None:
        self._waiters: defaultdict[
            str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]
        ] = defaultdict(set)
        # The test client serves each request on its own thread and event loop,
        # so a publish may come from a loop other than the waiter's.
        self._lock = threading.Lock()

    async def publish(self, channel: str) -> None:
        with self._lock:
            waiters = list(self._waiters.get(channel, ()))
        for loop, event in waiters:
            if not loop.is_closed():
                loop.call_soon_threadsafe(event.set)

    async def wait(self, channel: str, seconds: float) -> bool:
        waiter = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters[channel].add(waiter)
        try:
            async with asyncio.timeout(seconds):
                await waiter[1].wait()
        except TimeoutError:
            return False
        finally:
            with self._lock:
                self._waiters[channel].discard(waiter)
                if not self._waiters[channel]:
                    del self._waiters[channel]
        return True

    async def close(self) -> None:
        return None
