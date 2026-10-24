"""Which concrete implementations the routes get.

This is the seam made operational. Every provider choice in the system is made
here and nowhere else, so swapping the fake for OpenAlex — or one LLM provider
for another — is an edit to this file alone.

If you ever find yourself importing a concrete provider inside a route, the
architecture has sprung a leak. Import it here instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.application.encryption import ScopeEncryption
from app.application.search_service import SearchService
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider
from app.settings import Settings
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage

API_PREFIX = "/api/v1"


@dataclass(frozen=True, slots=True)
class Providers:
    """Everything built once per process. Providers hold pools and caches."""

    storage: StorageProvider
    search: PaperSearchProvider
    encryption: ScopeEncryption


def build_providers(settings: Settings) -> Providers:
    """THE switch: the one function that turns configuration into implementations."""
    if settings.database_url is None:
        storage: StorageProvider = MemoryStorage()
    else:
        storage = PostgresStorage.from_url(settings.database_url.get_secret_value())

    if settings.key_encryption_keys is None:
        # Only reachable with in-memory storage (Settings enforces it): the data
        # dies with the process, so a key that dies with it loses nothing.
        keys = LocalKeyProvider.from_config(f"1:{generate_kek()}")
    else:
        keys = LocalKeyProvider.from_config(settings.key_encryption_keys.get_secret_value())

    return Providers(
        storage=storage,
        search=FakePaperSearchProvider(),
        encryption=ScopeEncryption(keys, AesGcmCipher()),
    )


def get_providers(request: Request) -> Providers:
    providers: Providers = request.app.state.providers
    return providers


ProvidersDep = Annotated[Providers, Depends(get_providers)]


def get_storage(providers: ProvidersDep) -> StorageProvider:
    return providers.storage


StorageDep = Annotated[StorageProvider, Depends(get_storage)]


def get_search_provider(providers: ProvidersDep) -> PaperSearchProvider:
    return providers.search


SearchProviderDep = Annotated[PaperSearchProvider, Depends(get_search_provider)]


def get_search_service(provider: SearchProviderDep) -> SearchService:
    return SearchService(provider)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
