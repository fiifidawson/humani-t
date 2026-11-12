"""Composition root for the humani-t API.

Assembles the application and nothing else. The decision about *which*
implementations are used lives in `app/api/dependencies.py`; this module wires
them to middleware, error handlers and URLs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI

from app.api.dependencies import API_PREFIX, Providers, build_providers
from app.api.errors import install_error_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    ClientAddressMiddleware,
    CrossSiteRequestMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from app.api.routes import (
    account,
    admin,
    auth,
    briefs,
    documents,
    exports,
    masking,
    mfa,
    papers,
    plans,
    projects,
    questions,
    reviews,
    runs,
    system,
)
from app.api.routes.system import API_NAME, API_VERSION
from app.domain.accounts import EmailTakenError
from app.observability import configure_logging
from app.settings import Settings, get_settings
from app.worker import running_in_process

_log = structlog.get_logger(__name__)

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
    {
        "name": "account",
        "description": "The signed-in user's own sessions, password and second factors.",
    },
    {"name": "admin", "description": "Invitations and account administration."},
    {
        "name": "projects",
        "description": (
            "Projects and their members. A project is the unit of access, encryption "
            "and deletion: non-members get 404 for everything about it."
        ),
    },
    {"name": "reviews", "description": "Questions inside a project, encrypted with its key."},
    {
        "name": "plans",
        "description": (
            "The search-planning conversation of a review: clarifying questions, drafts, "
            "change requests, acceptance, your own terms (used exactly as given), versions "
            "with their differences, and a receipt of what each model call received."
        ),
    },
    {
        "name": "papers",
        "description": (
            "A run's papers and screening decisions with their provenance, PRISMA "
            "counts and the run manifest. Read-only; text is served masked."
        ),
    },
    {
        "name": "runs",
        "description": (
            "Background runs of a review's pipeline stages. Start one, follow it as "
            "Server-Sent Events (reconnecting with `Last-Event-ID`), decide its gates, "
            "cancel it. These paths name a review, run or gate; a non-member gets 404."
        ),
    },
    {
        "name": "briefs",
        "description": (
            "Reading a decision brief, its cited sources and how it was made. Briefs "
            "are written by a run's `answer` stage. A non-member gets 404."
        ),
    },
    {
        "name": "exports",
        "description": (
            "Downloads of a review's results: the screening dataset, included papers, "
            "PRISMA counts, the run manifest and the brief. Audited, never cached."
        ),
    },
    {
        "name": "questions",
        "description": (
            "Follow-up questions in threads, answered in the background from the "
            "review's evidence. The question is masked before it is stored or sent."
        ),
    },
    {
        "name": "masking",
        "description": (
            "A project's masking rules: terms always masked in every text. Owners only; "
            "terms are raw text, sealed at rest, and every listing is audited."
        ),
    },
    {
        "name": "documents",
        "description": (
            "A project's own files: upload (multipart), scanning, reading and masking in the "
            "background, the owner's masking review, and approval before any model sees one."
        ),
    },
]

# JSON bodies in this API are small; the document upload has its own, larger limit.
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
    async def lifespan(started: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as development:
            await _start_development_conveniences(
                settings, started.state.providers, development
            )
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


async def _start_development_conveniences(
    settings: Settings, providers: Providers, stack: AsyncExitStack
) -> None:
    """What Settings allows outside production only: an in-process worker, a first admin."""
    if settings.demo_inference:
        _log.warning(
            "demo_model_in_use", detail="model calls get canned answers, not a model's"
        )
    if settings.dev_admin_email is not None and settings.dev_admin_password is not None:
        try:
            await providers.accounts.create_user(
                email=settings.dev_admin_email,
                password=settings.dev_admin_password.get_secret_value(),
                display_name=settings.dev_admin_email.split("@")[0],
                is_admin=True,
            )
        except EmailTakenError:
            # Normal when the app starts twice in one process: the account is there.
            _log.info("development_admin_exists")
        else:
            _log.warning("development_admin_created")
    if settings.runs_worker_in_process:
        await stack.enter_async_context(running_in_process(providers))


def _install(app: FastAPI, settings: Settings) -> None:
    install_error_handlers(app)

    # Order matters: Starlette runs the last-added middleware first, so the client
    # address and request id are settled before anything logs, and security headers
    # wrap even the cross-site refusals and the 500 for unhandled errors.
    app.add_middleware(
        CrossSiteRequestMiddleware,
        public_origin=settings.public_origin,
        multipart_paths=(documents.UPLOAD_PATH,),
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=_MAX_BODY_BYTES,
        larger_limits=(
            (documents.UPLOAD_PATH, documents.upload_body_limit(settings.upload_max_bytes)),
        ),
    )
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        csp_exempt=_DOCS_PATHS if settings.docs_enabled else (),
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ClientAddressMiddleware, trusted_proxies=settings.trusted_proxy_networks)

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(system.router)
    if settings.docs_enabled:
        v1.include_router(system.info_router)
    v1.include_router(auth.router)
    v1.include_router(mfa.sign_in_router)
    v1.include_router(account.router)
    v1.include_router(mfa.account_router)
    v1.include_router(admin.router)
    v1.include_router(projects.router)
    v1.include_router(reviews.router)
    v1.include_router(runs.router)
    v1.include_router(papers.router)
    v1.include_router(briefs.router)
    v1.include_router(exports.router)
    v1.include_router(questions.router)
    v1.include_router(plans.router)
    v1.include_router(masking.router)
    v1.include_router(documents.router)
    app.include_router(v1)


app = create_app()
