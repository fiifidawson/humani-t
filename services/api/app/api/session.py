"""Session cookies and the dependency that turns one into the current caller.

Two cookies, with different jobs:

- `__Host-session` carries the session token. HttpOnly, so no script — ours or
  an injected one — can read it.
- `__Host-csrf` carries the per-session CSRF token. Readable by our scripts,
  which echo it in `X-CSRF-Token`; a page on another site cannot read it.

The `__Host-` prefix makes the browser refuse the cookie unless it is Secure,
has `Path=/` and has no `Domain`, so no subdomain can set or overwrite it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from app.api.dependencies import AccountsDep
from app.application.accounts import Current, SignedIn

SESSION_COOKIE = "__Host-session"
CSRF_COOKIE = "__Host-csrf"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session_cookies(response: Response, signed_in: SignedIn) -> None:
    # No Max-Age: the cookie ends with the browser session, and the server-side
    # timeouts end it sooner whatever the browser keeps.
    response.set_cookie(
        SESSION_COOKIE,
        signed_in.session_token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        signed_in.csrf_token,
        secure=True,
        httponly=False,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    # Deletion must repeat the attributes, or browsers ignore it for __Host- cookies.
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict", path="/")


def request_id(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else None


async def get_current(request: Request, accounts: AccountsDep) -> Current:
    """The signed-in caller; 401 if there is none, 403 if a write lacks its CSRF token.

    Authentication runs in its own short transaction, so the idle-timeout
    extension is saved even if the route itself later fails, and no connection
    is held while the route does its own work.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    current = await accounts.authenticate(token)
    if current is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")

    if request.method not in _SAFE_METHODS and not accounts.csrf_token_matches(
        current, request.headers.get(CSRF_HEADER, "")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
    return current


CurrentDep = Annotated[Current, Depends(get_current)]
