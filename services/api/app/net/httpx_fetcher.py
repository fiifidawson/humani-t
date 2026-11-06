"""The SSRF-guarded HttpFetcher, built on httpx2 and its connection layer, httpcore2.

The guard sits in the network backend, the one place a TCP connection is
opened. There the host is resolved once, every address is checked, and the
socket is opened to the checked address itself. TLS still presents the original
host name (SNI and certificate checks) and the Host header is untouched, so
servers see an ordinary request. A second DNS answer cannot swap the address,
because nothing ever asks DNS again for that connection.

Rejected: checking the URL's host before handing it to an ordinary client. The
client resolves again at connect time, so a name answering 1.2.3.4 to the check
and 127.0.0.1 to the connection (DNS rebinding) walks straight through.

Redirects are followed here, by hand, so every hop goes through the same
allowlist and address check. No proxy settings, cookies or `.netrc` are read
from the environment: requests leave exactly as this module builds them.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Iterable
from typing import Any, cast

import httpcore2
import httpx2
import structlog

from app.net.addresses import is_blocked_address, parse_ip_literal
from app.net.base import (
    BlockedAddressError,
    Destination,
    DestinationNotAllowedError,
    FetchFailedError,
    FetchRequest,
    FetchResponse,
    FetchTimeoutError,
    IPAddress,
    OutboundPolicy,
    OutboundRefusedError,
    Resolver,
    ResponseTooLargeError,
    TooManyRedirectsError,
)

USER_AGENT = "humani-t (self-hosted evidence synthesis)"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_SCHEMES = frozenset({"http", "https"})
_NETWORK_ERRORS = (
    httpcore2.NetworkError,
    httpcore2.ProtocolError,
    httpcore2.UnsupportedProtocol,
    httpx2.DecodingError,
)

_log = structlog.get_logger(__name__)


class SystemResolver:
    """The operating system's resolver, asked once per connection."""

    async def resolve(self, host: str) -> tuple[IPAddress, ...]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise FetchFailedError(f"{host} does not resolve") from exc
        addresses: dict[IPAddress, None] = {}
        for *_, sockaddr in infos:
            address = parse_ip_literal(str(sockaddr[0]))
            if address is not None:
                addresses[address] = None
        if not addresses:
            raise FetchFailedError(f"{host} does not resolve")
        return tuple(addresses)


class _GuardedNetwork(httpcore2.AsyncNetworkBackend):
    """Opens a connection only to an address that passed the policy, and only to it."""

    def __init__(
        self, policy: OutboundPolicy, resolver: Resolver, inner: httpcore2.AsyncNetworkBackend
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        # Checked again here, below the request logic, so no code path above
        # can open a connection to a host the policy does not name.
        destination = self._policy.destination_for(host)
        if destination is None:
            raise DestinationNotAllowedError(f"{host} is not on this fetcher's allowlist")
        addresses = await self._addresses_for(host, timeout)
        if not destination.allows_private_addresses and any(
            is_blocked_address(a) for a in addresses
        ):
            # One bad answer refuses the name: picking the "good" address out of
            # a mixed answer would let an attacker race the order.
            raise BlockedAddressError(f"{host} resolves to an address that is not public")
        failure: httpcore2.ConnectError | httpcore2.ConnectTimeout | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                failure = exc
        assert failure is not None  # noqa: S101 — addresses is never empty
        raise failure

    async def _addresses_for(
        self, host: str, limit_seconds: float | None
    ) -> tuple[IPAddress, ...]:
        literal = parse_ip_literal(host)
        if literal is not None:
            return (literal,)
        try:
            async with asyncio.timeout(limit_seconds):
                return await self._resolver.resolve(host)
        except TimeoutError as exc:
            raise httpcore2.ConnectTimeout(f"resolving {host} timed out") from exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        raise DestinationNotAllowedError("unix sockets are not an outbound destination")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _BodyStream(httpx2.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        if hasattr(self._stream, "aclose"):
            await self._stream.aclose()


class HttpxFetcher:
    """An HttpFetcher that can only reach the destinations in its policy."""

    def __init__(
        self,
        policy: OutboundPolicy,
        *,
        resolver: Resolver | None = None,
        network: httpcore2.AsyncNetworkBackend | None = None,
    ) -> None:
        """`network` replaces the socket layer beneath the guard; tests script it."""
        self._policy = policy
        self._timeout = httpx2.Timeout(
            policy.read_timeout_seconds, connect=policy.connect_timeout_seconds
        )
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=10,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_GuardedNetwork(
                policy, resolver or SystemResolver(), network or _anyio_backend()
            ),
        )

    async def fetch(self, request: FetchRequest) -> FetchResponse:
        try:
            async with asyncio.timeout(self._policy.total_timeout_seconds):
                return await self._follow_redirects(request)
        except OutboundRefusedError as exc:
            _log.warning("outbound_refused", reason=type(exc).__name__, detail=str(exc))
            raise
        except (TimeoutError, httpcore2.TimeoutException) as exc:
            raise FetchTimeoutError("the request did not complete in time") from exc
        except _NETWORK_ERRORS as exc:
            raise FetchFailedError(f"the request failed: {type(exc).__name__}") from exc

    async def close(self) -> None:
        await self._pool.aclose()

    async def _follow_redirects(self, request: FetchRequest) -> FetchResponse:
        url = _parse_url(request.url).copy_merge_params(request.params)
        method, body, headers = request.method, request.body, request.headers
        for hop in range(self._policy.max_redirects + 1):
            destination = self._check(url)
            status, response_headers, response_body = await self._send(
                method, url, headers, body, destination
            )
            location = response_headers.get("location")
            if status not in _REDIRECTS or not location:
                return FetchResponse(
                    status=status, url=str(url), headers=response_headers, body=response_body
                )
            if hop == self._policy.max_redirects:
                raise TooManyRedirectsError(
                    f"more than {self._policy.max_redirects} redirects from {request.url}"
                )
            url = url.join(location)
            if status == 303 or (status in (301, 302) and method == "POST"):
                method, body = "GET", None
                headers = tuple((k, v) for k, v in headers if k.lower() != "content-type")
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    def _check(self, url: httpx2.URL) -> Destination:
        if url.scheme not in _SCHEMES:
            raise DestinationNotAllowedError(f"the {url.scheme!r} scheme is not allowed")
        if url.userinfo:
            raise DestinationNotAllowedError("credentials in a URL are not allowed")
        host = url.raw_host.decode("ascii")
        destination = self._policy.destination_for(host)
        if destination is None:
            raise DestinationNotAllowedError(f"{host} is not on this fetcher's allowlist")
        if url.scheme == "http" and not destination.allows_plain_http:
            raise DestinationNotAllowedError(f"{host} must be reached over https")
        return destination

    async def _send(
        self,
        method: str,
        url: httpx2.URL,
        headers: tuple[tuple[str, str], ...],
        body: bytes | None,
        destination: Destination,
    ) -> tuple[int, dict[str, str], bytes]:
        outgoing = [
            ("User-Agent", USER_AGENT),
            # Uncompressed, so the byte cap measures what is held in memory and
            # no small compressed body can expand past it.
            ("Accept-Encoding", "identity"),
            *headers,
        ]
        if destination.bearer_token:
            outgoing.append(("Authorization", f"Bearer {destination.bearer_token}"))
        prepared = httpx2.Request(method, url, headers=outgoing, content=body)
        core_response = await self._pool.handle_async_request(
            httpcore2.Request(
                method=method,
                url=httpcore2.URL(
                    scheme=url.raw_scheme, host=url.raw_host, port=url.port, target=url.raw_path
                ),
                headers=prepared.headers.raw,
                content=prepared.content,
                extensions={"timeout": self._timeout.as_dict()},
            )
        )
        response = httpx2.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_BodyStream(core_response.stream),
            request=prepared,
        )
        try:
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            if response.status_code in _REDIRECTS:
                return response.status_code, response_headers, b""
            return response.status_code, response_headers, await self._read_capped(response)
        finally:
            await response.aclose()

    async def _read_capped(self, response: httpx2.Response) -> bytes:
        limit = self._policy.max_bytes
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise ResponseTooLargeError(
                f"the response declares {declared} bytes; limit {limit}"
            )
        received = bytearray()
        async for chunk in response.aiter_bytes():
            received.extend(chunk)
            if len(received) > limit:
                raise ResponseTooLargeError(f"the response exceeded {limit} bytes")
        return bytes(received)


def _anyio_backend() -> httpcore2.AsyncNetworkBackend:
    # httpcore2 types AnyIOBackend as a stub when anyio might be missing; it is a
    # dependency of httpx2, so the real class is always what this returns.
    return cast(httpcore2.AsyncNetworkBackend, httpcore2.AnyIOBackend())


def _parse_url(url: str) -> httpx2.URL:
    try:
        return httpx2.URL(url)
    except httpx2.InvalidURL as exc:
        raise DestinationNotAllowedError("the URL is not valid") from exc
