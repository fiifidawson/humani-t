"""Composition root for the humani-t API.

Assembles the application and nothing else. The decision about *which*
implementations are used lives in `app/api/dependencies.py`; this module wires
them to middleware, error handlers and URLs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api.dependencies import API_PREFIX, Providers, build_providers
from app.api.errors import install_error_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    CrossSiteRequestMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.routes import account, admin, auth, search, system, vocabulary
from app.api.routes.system import API_NAME, API_VERSION
from app.observability import configure_logging
from app.settings import Settings, get_settings

TAGS_METADATA = [
    {"name": "system", "description": "Liveness, readiness and what is wired in."},
    {
        "name": "auth",
        "description": (
            "Signing in and out. Sessions are server-side, carried in a `__Host-session` "
            "cookie; every write also needs the `X-CSRF-Token` header, copied from the "
            "`__Host-csrf` cookie."
        ),
    },
    {"name": "account", "description": "The signed-in user's own sessions and password."},
    {"name": "admin", "description": "Invitations and account administration."},
    {
        "name": "vocabulary",
        "description": (
            "Turning curated terms into a Boolean query. Concepts sharing a theme "
            "are OR'd; themes are AND'd. A user-supplied vocabulary is used "
            "exactly as given."
        ),
    },
    {
        "name": "search",
        "description": (
            "Running a compiled query against a source. Results always travel "
            "with the query and provenance that produced them."
        ),
    },
]

# JSON bodies in this API are small; uploads will get their own, larger route.
_MAX_BODY_BYTES = 1024 * 1024

_DOCS_PATHS = ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json")


def create_app(
    settings: Settings | None = None, *, providers: Providers | None = None
) -> FastAPI:
    """Build the app. Tests pass `providers` built with a fake clock and hasher."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, as_json=settings.is_production)

    # Built eagerly, not in the lifespan: engines connect lazily, and tests that
    # never enter the lifespan still get a fully wired app.
    providers = providers or build_providers(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await providers.close()

    app = FastAPI(
        title=API_NAME,
        version=API_VERSION,
        description="Privacy-preserving evidence synthesis for humanitarian decisions.",
        openapi_tags=TAGS_METADATA,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.providers = providers
    _install(app, settings)
    return app


def _install(app: FastAPI, settings: Settings) -> None:
    install_error_handlers(app)

    # Starlette runs the last-added middleware first: the request id is bound
    # before anything can log, and security headers wrap even the cross-site
    # refusals, which answer without reaching a route.
    app.add_middleware(CrossSiteRequestMiddleware, public_origin=settings.public_origin)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=_MAX_BODY_BYTES)
    app.add_middleware(
        SecurityHeadersMiddleware,
        csp_exempt=_DOCS_PATHS if settings.docs_enabled else (),
    )
    app.add_middleware(RequestIdMiddleware)

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(system.router)
    if settings.docs_enabled:
        v1.include_router(system.info_router)
    v1.include_router(auth.router)
    v1.include_router(account.router)
    v1.include_router(admin.router)
    v1.include_router(vocabulary.router)
    v1.include_router(search.router)
    app.include_router(v1)


app = create_app()
