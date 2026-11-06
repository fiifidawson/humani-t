"""The HttpFetcher seam: requests, responses, the outbound policy and its errors.

What belongs here is the contract every fetcher keeps, real or fake. How a
connection is opened, and which library opens it, belongs in the implementation.

The policy is fixed when a fetcher is built, not passed with each request: a
provider receives a fetcher already scoped to its own destinations, and cannot
widen that scope by asking.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol, runtime_checkable

from app.net.addresses import parse_ip_literal

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5

_METHODS = frozenset({"GET", "POST"})
# Credentials are bound to a Destination, never to a request, so a redirect
# cannot carry them to a host they were not meant for.
_FORBIDDEN_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "host"})

type IPAddress = IPv4Address | IPv6Address


class OutboundError(Exception):
    """An outbound request did not produce a response."""


class OutboundRefusedError(OutboundError):
    """The policy refused the request. Retrying will not help."""


class DestinationNotAllowedError(OutboundRefusedError):
    """The URL's scheme, host or form is not on this fetcher's allowlist."""


class BlockedAddressError(OutboundRefusedError):
    """The host resolved to an address outbound requests must never reach."""


class TooManyRedirectsError(OutboundRefusedError):
    pass


class ResponseTooLargeError(OutboundRefusedError):
    pass


class FetchTimeoutError(OutboundError):
    pass


class FetchFailedError(OutboundError):
    """The name did not resolve, the connection failed, or the peer broke the protocol."""


def normalise_host(host: str) -> str:
    """Lower-case, without a trailing dot or IPv6 brackets: the form hosts are compared in."""
    return host.strip().strip("[]").rstrip(".").lower()


@dataclass(frozen=True, slots=True)
class Destination:
    """One host a fetcher may reach, and on what terms.

    `allows_private_addresses` and `allows_plain_http` are for destinations the
    operator named, such as the model server on the compose network. Paper
    sources and PDF hosts never get them.
    """

    host: str
    allows_private_addresses: bool = False
    allows_plain_http: bool = False
    bearer_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        host = normalise_host(self.host)
        is_ip_literal = parse_ip_literal(host) is not None
        if not host or "/" in host or (":" in host and not is_ip_literal):
            raise ValueError(
                "Destination.host must be a bare host name, without scheme or port"
            )
        object.__setattr__(self, "host", host)


@dataclass(frozen=True, slots=True)
class OutboundPolicy:
    """Where a fetcher may go, and how much it may spend getting there."""

    destinations: tuple[Destination, ...]
    max_bytes: int = DEFAULT_MAX_BYTES
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 60.0
    max_redirects: int = MAX_REDIRECTS

    def __post_init__(self) -> None:
        if not self.destinations:
            raise ValueError("OutboundPolicy.destinations must not be empty")
        for name in (
            "max_bytes",
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "total_timeout_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"OutboundPolicy.{name} must be positive")
        if not 0 <= self.max_redirects <= MAX_REDIRECTS:
            raise ValueError(f"OutboundPolicy.max_redirects must be 0 to {MAX_REDIRECTS}")

    def destination_for(self, host: str) -> Destination | None:
        """Exact match only: an allowed host does not allow its subdomains."""
        wanted = normalise_host(host)
        return next((d for d in self.destinations if d.host == wanted), None)


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    method: str = "GET"
    params: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None

    def __post_init__(self) -> None:
        if self.method not in _METHODS:
            raise ValueError(f"FetchRequest.method must be one of {sorted(_METHODS)}")
        for name, _ in self.headers:
            if name.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(
                    f"FetchRequest header {name!r} is not allowed: credentials belong "
                    "to a Destination"
                )

    @classmethod
    def post_json(cls, url: str, payload: Mapping[str, object]) -> FetchRequest:
        return cls(
            url,
            method="POST",
            headers=(("Content-Type", "application/json"),),
            body=json.dumps(payload).encode("utf-8"),
        )


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """A complete response. `url` is where it finally came from, after redirects."""

    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", {k.lower(): v for k, v in self.headers.items()})

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> object:
        """Decode the body. Raises `ValueError` when it is not JSON."""
        return json.loads(self.body)

    @classmethod
    def of_json(cls, url: str, payload: object, *, status: int = 200) -> FetchResponse:
        return cls(
            status=status,
            url=url,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )


@runtime_checkable
class HttpFetcher(Protocol):
    async def fetch(self, request: FetchRequest) -> FetchResponse:
        """Send one request under this fetcher's policy and read the whole response.

        Any HTTP status is a response, not an error. Raises an `OutboundError`
        only when no response could be obtained, or the policy refused it.
        """
        ...

    async def close(self) -> None: ...


class Resolver(Protocol):
    async def resolve(self, host: str) -> tuple[IPAddress, ...]:
        """Every address `host` resolves to. Raises `FetchFailedError` when there are none."""
        ...
