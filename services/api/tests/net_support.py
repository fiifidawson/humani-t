"""A scripted socket layer for testing the real `HttpxFetcher` without a network.

It replaces only what sits *below* the guard: opening a TCP connection and
moving bytes. Address checks, redirects, the allowlist, the byte cap and the
timeouts all run for real. Each connection records the address it was opened
to, the TLS server name it was given and every byte the client sent, so a test
can prove where a request actually went.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpcore2


class Hang:
    """A read that never returns data: the peer is connected but silent."""


@dataclass(frozen=True)
class Drip:
    """An endless body, one small chunk at a time, each inside the read timeout."""

    chunk: bytes
    interval_seconds: float


class Refuse:
    """The connection attempt itself fails, as with a closed port."""


Part = bytes | Hang | Drip
Script = Sequence[Part] | Refuse


def http_response(
    status: int,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
    *,
    declare_length: bool = True,
) -> list[Part]:
    """Raw HTTP/1.1 response bytes. `Connection: close` keeps one request per connection."""
    lines = [f"HTTP/1.1 {status} Scripted"]
    all_headers = {"Connection": "close", **(headers or {})}
    if declare_length and "Content-Length" not in all_headers:
        all_headers["Content-Length"] = str(len(body))
    lines += [f"{name}: {value}" for name, value in all_headers.items()]
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    return [head, body] if body else [head]


def redirect(location: str, status: int = 302) -> list[Part]:
    return http_response(status, headers={"Location": location})


def json_response(body: bytes, status: int = 200) -> list[Part]:
    return http_response(status, body, {"Content-Type": "application/json"})


@dataclass
class Connection:
    host: str
    port: int
    sni: str | None = None
    sent: bytearray = field(default_factory=bytearray)
    read_timeouts: list[float | None] = field(default_factory=list)

    @property
    def request_text(self) -> str:
        return self.sent.decode("latin-1")

    def header(self, name: str) -> str | None:
        head = self.request_text.split("\r\n\r\n", 1)[0]
        for line in head.split("\r\n")[1:]:
            key, _, value = line.partition(":")
            if key.strip().lower() == name.lower():
                return value.strip()
        return None


class ScriptedStream(httpcore2.AsyncNetworkStream):
    def __init__(self, connection: Connection, parts: Iterable[Part]) -> None:
        self._connection = connection
        self._parts = list(parts)

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:  # noqa: ASYNC109
        self._connection.read_timeouts.append(timeout)
        if not self._parts:
            return b""
        part = self._parts[0]
        if isinstance(part, Hang):
            # What a real socket does: wait out the read timeout, then fail.
            if timeout is None:
                await asyncio.Event().wait()
            await asyncio.sleep(timeout or 0)
            raise httpcore2.ReadTimeout("scripted peer never answered")
        if isinstance(part, Drip):
            await asyncio.sleep(part.interval_seconds)
            return part.chunk
        self._parts.pop(0)
        return part[:max_bytes] if len(part) <= max_bytes else self._split(part, max_bytes)

    def _split(self, part: bytes, max_bytes: int) -> bytes:
        self._parts.insert(0, part[max_bytes:])
        return part[:max_bytes]

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:  # noqa: ASYNC109
        self._connection.sent.extend(buffer)

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's interface
    ) -> httpcore2.AsyncNetworkStream:
        self._connection.sni = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class ScriptedNetwork(httpcore2.AsyncNetworkBackend):
    """Serves one scripted response per connection, in order."""

    def __init__(self, *scripts: Script) -> None:
        self._scripts = list(scripts)
        self.connections: list[Connection] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        connection = Connection(host=host, port=port)
        self.connections.append(connection)
        if not self._scripts:
            raise AssertionError(f"unexpected connection to {host}:{port}")
        script = self._scripts.pop(0)
        if isinstance(script, Refuse):
            raise httpcore2.ConnectError("scripted: connection refused")
        return ScriptedStream(connection, script)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
