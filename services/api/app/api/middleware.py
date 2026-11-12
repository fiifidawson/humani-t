"""Request filtering, response hardening and request correlation, as plain ASGI middleware.

Plain ASGI rather than Starlette's `BaseHTTPMiddleware`: the latter buffers
streaming responses and breaks the SSE endpoints the review pipeline needs.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from http import HTTPStatus
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
# Accept an id from the reverse proxy only if it cannot smuggle anything into logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# An API response is data, never a document: nothing in it may load, frame or run.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

SECURITY_HEADERS: dict[str, str] = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


class SecurityHeadersMiddleware:
    """Add security headers to every response.

    Paths in `csp_exempt` (the dev-only Swagger UI, which loads scripts from a
    CDN) get every header except the Content-Security-Policy.
    """

    def __init__(self, app: ASGIApp, csp_exempt: Iterable[str] = ()) -> None:
        self.app = app
        self.csp_exempt = frozenset(csp_exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_exempt = scope["path"] in self.csp_exempt

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if not is_exempt:
                    headers.setdefault("content-security-policy", _API_CSP)
                    # Responses carry project data; no shared or browser cache keeps them.
                    headers.setdefault("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestIdMiddleware:
    """Give every request an id, echo it in the response and bind it to every log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            await self.app(scope, receive, send_with_id)


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CrossSiteRequestMiddleware:
    """Refuse state-changing requests that a browser reports as coming from elsewhere.

    `Sec-Fetch-Site` and `Origin`, when present, must say same origin, and a body must
    be JSON, which a cross-site form cannot send. The session's CSRF token is checked
    later. Requests with neither header are not from a browser, so they pass here.
    `multipart_paths` (the upload) may send multipart and rely on the other checks.
    """

    def __init__(
        self, app: ASGIApp, public_origin: str, multipart_paths: Iterable[re.Pattern[str]] = ()
    ) -> None:
        self.app = app
        self.public_origin = public_origin.rstrip("/")
        self.multipart_paths = tuple(multipart_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        fetch_site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        if (fetch_site is not None and fetch_site != "same-origin") or (
            origin is not None and origin.rstrip("/") != self.public_origin
        ):
            await _refuse(send, 403, "Cross-site requests are not allowed.")
            return

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        has_body = headers.get("content-length", "0") != "0" or "transfer-encoding" in headers
        if (
            has_body
            and content_type != "application/json"
            and not self._is_upload(scope["path"], content_type)
        ):
            await _refuse(send, 415, "Request bodies must be application/json.")
            return

        await self.app(scope, receive, send)

    def _is_upload(self, path: str, content_type: str) -> bool:
        return content_type == "multipart/form-data" and any(
            pattern.match(path) for pattern in self.multipart_paths
        )


async def _refuse(send: Send, status: int, detail: str) -> None:
    body = json.dumps(
        {
            "type": "about:blank",
            "title": HTTPStatus(status).phrase,
            "status": status,
            "detail": detail,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    """Refuse request bodies over `max_bytes` without reading them into memory.

    A chunked body is counted as it streams. The reverse proxy has a limit too; this
    one holds when the app is reached some other way.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        larger_limits: Iterable[tuple[re.Pattern[str], int]] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.larger_limits = tuple(larger_limits)

    def _limit(self, path: str) -> int:
        matching = (n for pattern, n in self.larger_limits if pattern.match(path))
        return next(matching, self.max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _refuse(send, 413, "The request body is too large.")
            return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if not response_started:
                await _refuse(send, 413, "The request body is too large.")


_UNEXPECTED_ERROR = "An unexpected error occurred. Quote the request id when reporting it."


class UnhandledErrorMiddleware:
    """Answer an exception that no handler took with a 500 problem document.

    Starlette would answer it from `ServerErrorMiddleware`, which sits outside
    every middleware added here, so its 500 had no security headers and no
    request id. This one runs inside the middleware that decorate responses and
    outside those that only refuse requests, so its 500 is decorated like any other.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Looked up per app, not at import: a logger caches its configuration on
        # first use, and each `create_app` configures logging afresh.
        self._log = structlog.get_logger(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            self._log.exception("unhandled_error", path=scope["path"])
            if response_started:
                # Too late for a status line: the client sees the stream end early.
                raise
            await _refuse(send, 500, _UNEXPECTED_ERROR)


class ClientAddressMiddleware:
    """Settle the client address, believing X-Forwarded-For only from a trusted proxy.

    Uvicorn's `--proxy-headers` trusts 127.0.0.1 unless told otherwise, so any
    local process could choose the address the per-client throttle counts. The
    decision is made here instead, from one operator setting that trusts nobody
    by default. Uvicorn must run with `--no-proxy-headers`, or it decides first.
    """

    def __init__(
        self, app: ASGIApp, trusted_proxies: Iterable[IPv4Network | IPv6Network] = ()
    ) -> None:
        self.app = app
        self.trusted_proxies = tuple(trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        if scope["type"] in ("http", "websocket") and client and self._is_trusted(client[0]):
            forwarded = [
                value.decode("latin-1")
                for name, value in scope["headers"]
                if name == b"x-forwarded-for"
            ]
            scope["client"] = (self._forwarded_client(client[0], forwarded), client[1])
        await self.app(scope, receive, send)

    def _is_trusted(self, host: str) -> bool:
        address = _ip_address(host)
        return address is not None and any(address in n for n in self.trusted_proxies)

    def _forwarded_client(self, peer: str, headers: Iterable[str]) -> str:
        """The nearest address that is not a trusted proxy, reading from the right.

        Each proxy appends the address it received from, so anything left of the
        rightmost untrusted entry was written by the client itself and proves nothing.
        """
        hops = [hop.strip() for header in headers for hop in header.split(",")]
        nearest = peer
        for hop in reversed(hops):
            address = _ip_address(hop)
            if address is None:
                return peer
            nearest = str(address)
            if not any(address in n for n in self.trusted_proxies):
                return nearest
        return nearest


def _ip_address(text: str) -> IPv4Address | IPv6Address | None:
    try:
        address = ip_address(text)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address
