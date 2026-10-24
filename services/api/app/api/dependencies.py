"""Which concrete implementations the routes get.

This is the seam made operational. Every provider choice in the system is made
here and nowhere else, so swapping the fake for OpenAlex — or one LLM provider
for another — is an edit to this file alone.

If you ever find yourself importing a concrete provider inside a route, the
architecture has sprung a leak. Import it here instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request

from app.application.accounts import AccountService
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.search_service import SearchService
from app.auth.argon2 import Argon2PasswordHasher
from app.auth.base import PasswordHasher
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.email.base import EmailProvider
from app.email.recording import RecordingEmailProvider
from app.email.smtp import SmtpEmailProvider
from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemoryRateLimiter
from app.ratelimit.valkey import ValkeyRateLimiter
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider
from app.settings import Settings
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage

API_PREFIX = "/api/v1"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Providers:
    """Everything built once per process. Providers hold pools and caches."""

    storage: StorageProvider
    search: PaperSearchProvider
    encryption: ScopeEncryption
    audit: AuditLog
    accounts: AccountService
    email: EmailProvider
    rate_limiter: RateLimiter


def build_providers(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = _utc_now,
    password_hasher: PasswordHasher | None = None,
) -> Providers:
    """THE switch: the one function that turns configuration into implementations.

    `clock` and `password_hasher` exist for tests: a clock they can move, and a
    hasher that does not spend 64 MiB per sign-in.
    """
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

    if settings.valkey_url is None:
        limiter: RateLimiter = MemoryRateLimiter(clock)
    else:
        limiter = ValkeyRateLimiter.from_url(settings.valkey_url.get_secret_value(), clock)

    if settings.smtp_host is None:
        email: EmailProvider = RecordingEmailProvider()
    else:
        email = SmtpEmailProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_sender,
            username=settings.smtp_username or None,
            password=settings.smtp_password.get_secret_value()
            if settings.smtp_password
            else None,
            use_starttls=settings.smtp_starttls,
        )

    encryption = ScopeEncryption(keys, AesGcmCipher(), clock)
    audit = AuditLog(encryption, clock)
    return Providers(
        storage=storage,
        search=FakePaperSearchProvider(),
        encryption=encryption,
        audit=audit,
        accounts=AccountService(
            hasher=password_hasher or Argon2PasswordHasher(),
            email=email,
            limiter=limiter,
            audit=audit,
            public_origin=settings.public_origin,
            clock=clock,
        ),
        email=email,
        rate_limiter=limiter,
    )


def get_providers(request: Request) -> Providers:
    providers: Providers = request.app.state.providers
    return providers


ProvidersDep = Annotated[Providers, Depends(get_providers)]


def get_storage(providers: ProvidersDep) -> StorageProvider:
    return providers.storage


StorageDep = Annotated[StorageProvider, Depends(get_storage)]


def get_accounts(providers: ProvidersDep) -> AccountService:
    return providers.accounts


AccountsDep = Annotated[AccountService, Depends(get_accounts)]


def get_search_provider(providers: ProvidersDep) -> PaperSearchProvider:
    return providers.search


SearchProviderDep = Annotated[PaperSearchProvider, Depends(get_search_provider)]


def get_search_service(provider: SearchProviderDep) -> SearchService:
    return SearchService(provider)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
