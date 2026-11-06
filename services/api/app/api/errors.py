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
    LastAdminError,
    NotFoundError,
    OwnAccountError,
    PasswordRejectedError,
    ReauthenticationRequiredError,
    ThrottledError,
)
from app.domain.errors import (
    ConcurrentUpdateError,
    RateLimiterUnavailableError,
    StorageUnavailableError,
)
from app.domain.inference import InferenceUnavailableError
from app.domain.mfa import (
    EnrolmentCodeIncorrectError,
    LastAuthenticatorError,
    MfaEnrolmentRequiredError,
    MfaRequiredError,
    NotEnrolledError,
    PasskeyAlreadyRegisteredError,
    PasskeyRegistrationRejectedError,
    SecondFactorRejectedError,
    TotpAlreadyEnrolledError,
)
from app.domain.notifications import EmailDeliveryError
from app.domain.projects import (
    AlreadyMemberError,
    LastOwnerError,
    NoSuchAccountError,
    ProjectError,
)
from app.domain.reviews import InvalidTransitionError
from app.domain.runs import RunConflictError
from app.domain.sources import PaperSourceUnavailableError

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
    SecondFactorRejectedError: 401,
    MfaRequiredError: 401,
    MfaEnrolmentRequiredError: 403,
    EnrolmentCodeIncorrectError: 400,
    PasskeyRegistrationRejectedError: 400,
    LastAuthenticatorError: 409,
    TotpAlreadyEnrolledError: 409,
    PasskeyAlreadyRegisteredError: 409,
    NotEnrolledError: 409,
    OwnAccountError: 409,
    LastAdminError: 409,
}


async def _account_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AccountError)  # noqa: S101 — narrows the handler type
    status = next(code for kind, code in _ACCOUNT_STATUS.items() if isinstance(exc, kind))
    extensions: dict[str, Any] = {}
    if isinstance(exc, PasswordRejectedError):
        extensions["violations"] = list(exc.violations)
    if isinstance(exc, ReauthenticationRequiredError):
        extensions["reauthentication_required"] = True
    if isinstance(exc, MfaRequiredError):
        extensions["mfa_required"] = True
    if isinstance(exc, MfaEnrolmentRequiredError):
        extensions["mfa_enrolment_required"] = True
    if isinstance(exc, LastAdminError):
        extensions["last_admin"] = True
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


async def _rate_limiter_unavailable(_: Request, exc: Exception) -> JSONResponse:
    # Fail closed: an attempt that cannot be counted is refused, not let through.
    _log.warning("rate_limiter_unavailable", reason=str(exc))
    response = problem(503, "Sign-in is briefly unavailable. Try again shortly.")
    response.headers["Retry-After"] = str(_STORAGE_RETRY_AFTER_SECONDS)
    return response


_PROJECT_STATUS: dict[type[ProjectError], int] = {
    LastOwnerError: 409,
    AlreadyMemberError: 409,
    # 422, not 404: on a project route the UI reads 404 as "no such project".
    NoSuchAccountError: 422,
}


async def _project_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProjectError)  # noqa: S101 — narrows the handler type
    status = next(code for kind, code in _PROJECT_STATUS.items() if isinstance(exc, kind))
    extensions: dict[str, Any] = {}
    if isinstance(exc, LastOwnerError):
        extensions["last_owner"] = True
    return problem(status, str(exc), **extensions)


async def _run_conflict(_: Request, exc: Exception) -> JSONResponse:
    # The messages are written for members: what changed, and what to do now.
    return problem(409, str(exc))


async def _concurrent_update(_: Request, exc: Exception) -> JSONResponse:
    return problem(409, "The record changed while you were editing it. Reload and try again.")


async def _email_delivery(_: Request, exc: Exception) -> JSONResponse:
    _log.warning("email_delivery_failed", exc_info=exc)
    return problem(502, "The email could not be sent. The action was saved; try sending again.")


# An upstream's reason can name its host, an address or its own error text: it
# goes to the operator's log, and the response says only which source failed.
async def _paper_source_unavailable(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PaperSourceUnavailableError)  # noqa: S101 — narrows the handler type
    _log.warning("paper_source_unavailable", source_id=exc.source_id, reason=exc.reason)
    return problem(
        502, "A paper source could not be reached. Try again later.", source_id=exc.source_id
    )


async def _inference_unavailable(_: Request, exc: Exception) -> JSONResponse:
    _log.warning("inference_unavailable", reason=str(exc))
    return problem(502, "The model server could not be reached. Try again later.")


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Reached only from the outermost middleware; a route's errors are answered
    # by UnhandledErrorMiddleware, inside the security headers and request id.
    _log.exception("unhandled_error", path=request.url.path, exc_info=exc)
    return problem(500, "An unexpected error occurred. Quote the request id when reporting it.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(AccountError, _account_error)
    app.add_exception_handler(ProjectError, _project_error)
    app.add_exception_handler(StorageUnavailableError, _storage_unavailable)
    app.add_exception_handler(RateLimiterUnavailableError, _rate_limiter_unavailable)
    app.add_exception_handler(ConcurrentUpdateError, _concurrent_update)
    app.add_exception_handler(RunConflictError, _run_conflict)
    app.add_exception_handler(InvalidTransitionError, _run_conflict)
    app.add_exception_handler(EmailDeliveryError, _email_delivery)
    app.add_exception_handler(PaperSourceUnavailableError, _paper_source_unavailable)
    app.add_exception_handler(InferenceUnavailableError, _inference_unavailable)
    app.add_exception_handler(Exception, _unhandled)
