"""A scripted HttpFetcher and Resolver. No sockets, no DNS.

`FakeHttpFetcher` stands in for the whole fetcher when testing what is built
on top of it (paper sources, the model server). `FakeResolver` stands in for
DNS when testing the real fetcher, so tests can make a name resolve to any
address, or to a different one the second time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from app.net.addresses import parse_ip_literal
from app.net.base import (
    DestinationNotAllowedError,
    FetchFailedError,
    FetchRequest,
    FetchResponse,
    IPAddress,
    normalise_host,
)

type Handler = Callable[[FetchRequest], FetchResponse]


def _not_found(request: FetchRequest) -> FetchResponse:
    return FetchResponse(status=404, url=request.url, headers={}, body=b"")


class FakeHttpFetcher:
    """Answers each request with `handler(request)` and records what was asked.

    Given `allowed_hosts`, it refuses other hosts the way the real fetcher does,
    so a test can prove a provider only talks to its own source.
    """

    def __init__(
        self, handler: Handler | None = None, *, allowed_hosts: Iterable[str] | None = None
    ) -> None:
        self._handler = handler or _not_found
        self._allowed = (
            None if allowed_hosts is None else {normalise_host(h) for h in allowed_hosts}
        )
        self.requests: list[FetchRequest] = []
        self.is_closed = False

    async def fetch(self, request: FetchRequest) -> FetchResponse:
        host = normalise_host(urlsplit(request.url).hostname or "")
        if self._allowed is not None and host not in self._allowed:
            raise DestinationNotAllowedError(f"{host} is not on this fetcher's allowlist")
        self.requests.append(request)
        return self._handler(request)

    async def close(self) -> None:
        self.is_closed = True


class FakeResolver:
    """Answers lookups from a script. Successive answers for one name are used in turn.

    The last answer for a name repeats, so a single `answer()` behaves like a
    stable DNS record and two behave like a record that changes (rebinding).
    """

    def __init__(self) -> None:
        self._answers: dict[str, list[tuple[IPAddress, ...]]] = {}
        self.calls: list[str] = []

    def answer(self, host: str, *addresses: str) -> FakeResolver:
        parsed = tuple(parse_ip_literal(a) for a in addresses)
        if any(a is None for a in parsed):
            raise ValueError(f"FakeResolver.answer needs IP addresses, got {addresses}")
        self._answers.setdefault(normalise_host(host), []).append(
            tuple(a for a in parsed if a is not None)
        )
        return self

    async def resolve(self, host: str) -> tuple[IPAddress, ...]:
        self.calls.append(host)
        queue = self._answers.get(normalise_host(host))
        if not queue:
            raise FetchFailedError(f"{host} does not resolve")
        return queue.pop(0) if len(queue) > 1 else queue[0]
