"""Every error leaves as an RFC 9457 problem document.

One shape for clients to handle, and one place that decides what an error may
reveal. Validation errors never echo the submitted input back: it may be the
sensitive text the user was trying to send.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"

_log = structlog.get_logger(__name__)


def problem(status: int, detail: str | None = None, **extensions: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
    }
    if detail is not None:
        body["detail"] = detail
    body.update(extensions)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE)


async def _http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 — narrows the handler type
    detail = exc.detail if isinstance(exc.detail, str) else None
    response = problem(exc.status_code, detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — narrows the handler type
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem(422, "The request body is not valid.", errors=errors)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_error", path=request.url.path, exc_info=exc)
    return problem(500, "An unexpected error occurred. Quote the request id when reporting it.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
