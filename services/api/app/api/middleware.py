"""Response hardening and request correlation, as plain ASGI middleware.

Plain ASGI rather than Starlette's `BaseHTTPMiddleware`: the latter buffers
streaming responses and breaks the SSE endpoints the review pipeline needs.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

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
