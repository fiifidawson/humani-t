"""Every error leaves as an RFC 9457 problem document.

One shape for clients to handle, and one place that decides what an error may
reveal. Validation errors never echo the submitted input back: it may be the
sensitive text the user was trying to send.
"""

from __future__ import annotations

import math
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.accounts import (
    AccountError,
    CurrentPasswordIncorrectError,
    EmailTakenError,
    ForbiddenError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
    PasswordRejectedError,
    ReauthenticationRequiredError,
    ThrottledError,
)
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.domain.notifications import EmailDeliveryError

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


_ACCOUNT_STATUS: dict[type[AccountError], int] = {
    InvalidCredentialsError: 401,
    ReauthenticationRequiredError: 401,
    CurrentPasswordIncorrectError: 403,
    ForbiddenError: 403,
    NotFoundError: 404,
    InvalidTokenError: 400,
    EmailTakenError: 409,
    PasswordRejectedError: 422,
    ThrottledError: 429,
}


async def _account_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AccountError)  # noqa: S101 — narrows the handler type
    status = next(code for kind, code in _ACCOUNT_STATUS.items() if isinstance(exc, kind))
    extensions: dict[str, Any] = {}
    if isinstance(exc, PasswordRejectedError):
        extensions["violations"] = list(exc.violations)
    if isinstance(exc, ReauthenticationRequiredError):
        extensions["reauthentication_required"] = True
    response = problem(status, str(exc), **extensions)
    if isinstance(exc, ThrottledError):
        response.headers["Retry-After"] = str(
            max(1, math.ceil(exc.retry_after.total_seconds()))
        )
    return response


# Seconds a client should wait before retrying when the database is saturated:
# long enough for a burst to drain, short enough not to feel broken.
_STORAGE_RETRY_AFTER_SECONDS = 5


async def _storage_unavailable(_: Request, exc: Exception) -> JSONResponse:
    _log.warning("storage_unavailable", reason=str(exc))
    response = problem(503, "The service is busy. Try again shortly.")
    response.headers["Retry-After"] = str(_STORAGE_RETRY_AFTER_SECONDS)
    return response


async def _concurrent_update(_: Request, exc: Exception) -> JSONResponse:
    return problem(409, "The record changed while you were editing it. Reload and try again.")


async def _email_delivery(_: Request, exc: Exception) -> JSONResponse:
    _log.warning("email_delivery_failed", exc_info=exc)
    return problem(502, "The email could not be sent. The action was saved; try sending again.")


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_error", path=request.url.path, exc_info=exc)
    return problem(500, "An unexpected error occurred. Quote the request id when reporting it.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(AccountError, _account_error)
    app.add_exception_handler(StorageUnavailableError, _storage_unavailable)
    app.add_exception_handler(ConcurrentUpdateError, _concurrent_update)
    app.add_exception_handler(EmailDeliveryError, _email_delivery)
    app.add_exception_handler(Exception, _unhandled)
