"""Liveness and configuration visibility."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import API_PREFIX, SearchProviderDep
from app.api.schemas import HealthOut, InfoOut

router = APIRouter(tags=["system"])

API_NAME = "humani-t API"
API_VERSION = "0.1.0"

# Sources that answer from memory rather than over the network. Used to tell a
# reader of /info whether this process would phone anyone.
OFFLINE_SOURCES = {"fake"}


@router.get("/health", summary="Liveness probe")
async def health() -> HealthOut:
    """Answers 'is this process up', not 'is the whole system healthy'.

    Deliberately touches no provider and no database. Conflating the two makes
    deploys hard to debug, because a failing dependency looks like a dead process.
    """
    return HealthOut(status="ok")


@router.get("/info", summary="What is wired into this process")
async def info(provider: SearchProviderDep) -> InfoOut:
    """Reports which implementations are active behind the interfaces.

    Worth having early: the whole design rests on implementations being
    swappable, and this is the endpoint that makes the current choice visible
    instead of buried in a config file.
    """
    return InfoOut(
        name=API_NAME,
        version=API_VERSION,
        api_prefix=API_PREFIX,
        search_provider=provider.source_id,
        makes_network_calls=provider.source_id not in OFFLINE_SOURCES,
    )
