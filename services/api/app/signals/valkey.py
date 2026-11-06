"""Signals shared by every API and worker process, over Valkey pub/sub.

Each process holds one subscription (a pattern over this deployment's prefix)
and hands each message to its own waiters, rather than one subscription per
waiting stream, so a thousand open streams cost one connection.

Pub/sub delivers at most once and only to connected subscribers; that is
enough, because a signal only says "look again" (see `signals/base.py`). An
outage is logged and waited out, never raised: the callers poll on their
timeouts and keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

_SOCKET_TIMEOUT_SECONDS = 2
_DEFAULT_PREFIX = "humani-t:signal:"

_log = structlog.get_logger(__name__)


class ValkeySignals:
    def __init__(self, client: Redis, prefix: str = _DEFAULT_PREFIX) -> None:
        self._client = client
        self._prefix = prefix
        self._waiters: defaultdict[str, set[asyncio.Event]] = defaultdict(set)
        self._pubsub: PubSub | None = None
        self._listener: asyncio.Task[None] | None = None
        self._subscribing: asyncio.Lock | None = None

    @classmethod
    def from_url(cls, url: str, prefix: str = _DEFAULT_PREFIX) -> ValkeySignals:
        client = Redis.from_url(
            url,
            socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            retry=Retry(NoBackoff(), 0),
        )
        return cls(client, prefix)

    async def publish(self, channel: str) -> None:
        try:
            await self._client.publish(self._prefix + channel, "1")
        except (RedisError, OSError):
            _log.warning("signal_publish_failed", channel=channel)

    async def wait(self, channel: str, seconds: float) -> bool:
        event = asyncio.Event()
        self._waiters[channel].add(event)
        try:
            async with asyncio.timeout(seconds):
                if not await self._ensure_listening():
                    await asyncio.sleep(seconds)
                await event.wait()
        except TimeoutError:
            return False
        finally:
            self._waiters[channel].discard(event)
            if not self._waiters[channel]:
                del self._waiters[channel]
        return True

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        if self._pubsub is not None:
            with contextlib.suppress(RedisError, OSError):
                await self._pubsub.aclose()
        await self._client.aclose()

    async def _ensure_listening(self) -> bool:
        if self._subscribing is None:
            self._subscribing = asyncio.Lock()
        async with self._subscribing:
            if self._listener is not None and not self._listener.done():
                return True
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(self._prefix + "*")
            except (RedisError, OSError):
                _log.warning("signal_subscribe_failed")
                with contextlib.suppress(RedisError, OSError):
                    await pubsub.aclose()
                return False
            self._pubsub = pubsub
            self._listener = asyncio.create_task(self._listen(pubsub))
            return True

    async def _listen(self, pubsub: PubSub) -> None:
        try:
            async for message in pubsub.listen():
                self._dispatch(message)
        except (RedisError, OSError):
            # The next wait subscribes again; until then waiters time out and poll.
            _log.warning("signal_subscription_lost")

    def _dispatch(self, message: dict[str, Any]) -> None:
        if message.get("type") != "pmessage":
            return
        raw = message.get("channel", b"")
        name = raw.decode() if isinstance(raw, bytes) else str(raw)
        for event in list(self._waiters.get(name.removeprefix(self._prefix), ())):
            event.set()
