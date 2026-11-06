"""Wake-up signals: "something changed on this channel, look again".

A signal carries no data. The truth is always in the database — the run table
for the queue, the event log for a stream — and every waiter also wakes on its
own timeout and looks anyway. So a lost signal costs latency, never an event,
and the system keeps working, more slowly, with no signals at all.

Channel names are internal (`runs:queued`, `run:<id>`): an id at most, never
project content.
"""

from __future__ import annotations

from typing import Protocol


class Signals(Protocol):
    async def publish(self, channel: str) -> None:
        """Wake every waiter on `channel`, in any process. Never raises for an outage."""
        ...

    async def wait(self, channel: str, seconds: float) -> bool:
        """Wait until `channel` is published or `seconds` pass.

        `True` if woken, `False` on timeout. Never raises for an outage: it
        waits the time out instead, so callers fall back to polling.
        """
        ...

    async def close(self) -> None: ...
