"""Composition root for the humani-t API.

Assembles routers and nothing else. The decision about *which* implementations
are used lives in `app/api/dependencies.py`; this module only wires URLs.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.api.dependencies import API_PREFIX
from app.api.routes import search, system, vocabulary
from app.api.routes.system import API_NAME, API_VERSION

TAGS_METADATA = [
    {"name": "system", "description": "Liveness and what is wired into this process."},
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

app = FastAPI(
    title=API_NAME,
    version=API_VERSION,
    description="Privacy-preserving evidence synthesis for humanitarian decisions.",
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)

v1 = APIRouter(prefix=API_PREFIX)
v1.include_router(system.router)
v1.include_router(vocabulary.router)
v1.include_router(search.router)

app.include_router(v1)
