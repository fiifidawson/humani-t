"""Wake-up signals: "something changed on this channel, look again".

A signal carries no data: the truth is in the database, and every waiter also wakes
on a timeout, so a lost signal costs latency, never an event. Channel names hold an
id at most, never project content.
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
