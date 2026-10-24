"""Which concrete implementations the routes get.

This is the seam made operational. Every provider choice in the system is made
here and nowhere else, so swapping the fake for OpenAlex — or one LLM provider
for another — is an edit to this file alone.

If you ever find yourself importing a concrete provider inside a route, the
architecture has sprung a leak. Import it here instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.application.search_service import SearchService
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider

API_PREFIX = "/api/v1"


@lru_cache(maxsize=1)
def _search_provider_singleton() -> FakePaperSearchProvider:
    """One instance per process. Providers hold connection pools and caches."""
    return FakePaperSearchProvider()


def get_search_provider() -> PaperSearchProvider:
    """THE switch.

    Session 3 changes this one return statement to an OpenAlexProvider and
    nothing above this line moves. That is the whole claim the architecture
    makes, expressed in one function.
    """
    return _search_provider_singleton()


SearchProviderDep = Annotated[PaperSearchProvider, Depends(get_search_provider)]


def get_search_service(provider: SearchProviderDep) -> SearchService:
    return SearchService(provider)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
