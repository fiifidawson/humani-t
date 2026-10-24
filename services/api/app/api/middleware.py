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

    The first two CSRF layers; the per-session token is the third, checked once
    the session is known (`api/session.py`):

    - `Sec-Fetch-Site`, when present, must be `same-origin`.
    - `Origin`, when present, must be exactly the public origin.
    - A request with a body must be JSON. A cross-site HTML form can only send
      form encodings or text/plain, and cross-site JSON needs a CORS preflight
      this API never grants.

    A request with neither header comes from a non-browser client (curl,
    Postman). Forgery needs a victim's browser, so those pass; anything signed
    in still needs the session's CSRF token.
    """

    def __init__(self, app: ASGIApp, public_origin: str) -> None:
        self.app = app
        self.public_origin = public_origin.rstrip("/")

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
        if has_body and content_type != "application/json":
            await _refuse(send, 415, "Request bodies must be application/json.")
            return

        await self.app(scope, receive, send)


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

    A declared `Content-Length` over the limit is refused before a byte is read.
    A body without one (chunked) is counted as it streams, and refused as soon as
    it passes the limit. The reverse proxy enforces a limit too; this one holds
    when the app is reached some other way.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _refuse(send, 413, "The request body is too large.")
            return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
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
