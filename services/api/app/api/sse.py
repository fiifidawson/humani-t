"""The Server-Sent Events wire format: the stream's items as bytes.

Sources: the WHATWG HTML standard, "Server-sent events". Each event is an `id:`,
an `event:` and one `data:` line of JSON, then a blank line. A line starting
with `:` is a comment, which browsers ignore and proxies count as traffic.
`retry:` sets how long the browser waits before reconnecting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.application.runs import KeepAlive, StreamItem

MEDIA_TYPE = "text/event-stream"

# Three seconds: quick enough that a dropped connection feels like a blip, slow
# enough that a hundred tabs reconnecting after a deploy do not stampede the API.
RECONNECT_DELAY_MS = 3000

KEEP_ALIVE = b": keep-alive\n\n"


def encode(item: StreamItem) -> bytes:
    if isinstance(item, KeepAlive):
        return KEEP_ALIVE
    # ASCII-only JSON escapes every line break inside a string, so the data
    # stays on one line and cannot end the event early or inject a field.
    data = json.dumps(item.data, separators=(",", ":"))
    return f"id: {item.id}\nevent: {item.name}\ndata: {data}\n\n".encode()


async def event_stream(items: AsyncIterator[StreamItem]) -> AsyncIterator[bytes]:
    yield f"retry: {RECONNECT_DELAY_MS}\n\n".encode()
    async for item in items:
        yield encode(item)
