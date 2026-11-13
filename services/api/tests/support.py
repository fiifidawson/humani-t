"""Test helpers shared by the authentication specs.

Nothing here is a mock: the clock moves, the hasher hashes (cheaply) and requests go through
the real middleware, routes and storage. The `Tracking`/`Observing` wrappers record how many
transactions are open when something slow happens, to prove no connection is held across
hashing or email.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from app.api.dependencies import Providers, build_providers
from app.auth.fake import FakePasswordHasher
from app.domain.masking import MaskingPolicy
from app.domain.mfa import MfaPolicy
from app.domain.notifications import EmailMessage
from app.email.recording import RecordingEmailProvider
from app.main import create_app
from app.ratelimit.base import RateLimiter
from app.settings import Environment, Settings
from app.storage.base import UnitOfWork
from app.storage.memory import MemoryStorage

ORIGIN = "https://testserver"

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "violet-harbour-quietly-41-lanterns"
USER_EMAIL = "amara@example.org"
USER_PASSWORD = "seventeen-copper-kites-over-lagos"
NEW_PASSWORD = "the-tide-returns-at-nine-o-clock"

# Projects start masked and stay masked, as with a hosted model: the specs written
# before masking became a project's choice assume it (ADR 0014).
MASKED_BY_DEPLOYMENT = MaskingPolicy(is_model_hosted=True)


@dataclass
class FakeClock:
    """A clock tests move by hand, so expiry is tested without sleeping."""

    now: datetime = field(default_factory=lambda: datetime(2026, 10, 24, 9, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class TrackingStorage:
    """In-memory storage that counts the transactions open right now."""

    def __init__(self) -> None:
        self.inner = MemoryStorage()
        self.open_units = 0

    @property
    def state(self) -> Any:
        return self.inner.state

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[UnitOfWork]:
        self.open_units += 1
        try:
            async with self.inner.unit_of_work() as uow:
                yield uow
        finally:
            self.open_units -= 1

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class ObservingHasher(FakePasswordHasher):
    """Records how many transactions were open each time it hashed or verified."""

    def __init__(self, storage: TrackingStorage) -> None:
        super().__init__()
        self._storage = storage
        self.open_units_seen: list[int] = []

    def hash(self, password: str) -> str:
        self.open_units_seen.append(self._storage.open_units)
        return super().hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        self.open_units_seen.append(self._storage.open_units)
        return super().verify(stored_hash, password)


class ObservingEmail(RecordingEmailProvider):
    """Records how many transactions were open each time a message was sent."""

    def __init__(self, storage: TrackingStorage) -> None:
        super().__init__()
        self._storage = storage
        self.open_units_seen: list[int] = []
        self._release: asyncio.Event | None = None

    def hold(self) -> asyncio.Event:
        """Make each send wait until the returned event is set, like a slow mail server."""
        self._release = asyncio.Event()
        return self._release

    async def send(self, message: EmailMessage) -> None:
        self.open_units_seen.append(self._storage.open_units)
        if self._release is not None:
            await self._release.wait()
        await super().send(message)


@dataclass
class AuthHarness:
    """One app, its providers, and a browser-like client pointed at it."""

    app: FastAPI
    providers: Providers
    clock: FakeClock
    hasher: ObservingHasher
    storage: TrackingStorage
    email: ObservingEmail

    def browser(self) -> TestClient:
        """A new client with its own cookie jar — a separate browser."""
        return TestClient(self.app, base_url=ORIGIN, headers={"Origin": ORIGIN})

    def create_user(self, email: str, password: str, *, is_admin: bool = False) -> None:
        asyncio.run(
            self.providers.accounts.create_user(
                email=email,
                password=password,
                display_name=email.split("@")[0],
                is_admin=is_admin,
            )
        )

    @property
    def outbox(self) -> list[object]:
        return self.providers.email.outbox  # type: ignore[attr-defined, no-any-return]


def make_harness(
    *,
    mfa_policy: MfaPolicy | None = None,
    rate_limiter: RateLimiter | None = None,
    clock: FakeClock | None = None,
    masking_policy: MaskingPolicy = MASKED_BY_DEPLOYMENT,
    **settings_overrides: object,
) -> AuthHarness:
    """An app with production's defaults — MFA required — unless a spec says otherwise.

    `mfa_policy=MfaPolicy(is_required=False)` is for specs of the password factor
    alone; `rate_limiter` lets a spec unplug the limiter's backend.
    """
    clock = clock or FakeClock()
    storage = TrackingStorage()
    hasher = ObservingHasher(storage)
    email = ObservingEmail(storage)
    # Tests never load a real language model: they would depend on the ner extra
    # being installed. Specs of the real model opt in explicitly.
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "public_origin": ORIGIN,
        "masking_ner": "off",
    }
    values.update(settings_overrides)
    settings = Settings.model_validate(values)
    providers = build_providers(
        settings,
        clock=clock,
        password_hasher=hasher,
        storage=storage,
        email=email,
        rate_limiter=rate_limiter,
        mfa_policy=mfa_policy,
        masking_policy=masking_policy,
    )
    return AuthHarness(
        create_app(settings, providers=providers), providers, clock, hasher, storage, email
    )


def plant_session_cookie(client: TestClient, value: str) -> None:
    """Put a session cookie in the jar where the browser itself would.

    The standard library files cookies for a dotless host under `testserver.local`; a cookie
    for plain `testserver` is never sent, and its test would pass without testing anything.
    """
    client.cookies.set("__Host-session", value, domain="testserver.local")


def session_cookie_value(response: Response) -> str:
    """The session token a response just issued."""
    header = set_cookie_headers(response)["__Host-session"]
    return header.split("=", 1)[1].split(";", 1)[0]


def csrf_headers(client: TestClient) -> dict[str, str]:
    """What the SPA sends on every state-changing request."""
    return {"X-CSRF-Token": client.cookies.get("__Host-csrf") or ""}


def login(client: TestClient, email: str, password: str) -> Response:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def set_cookie_headers(response: Response) -> dict[str, str]:
    """Map cookie name to its full Set-Cookie header."""
    return {
        header.split("=", 1)[0]: header for header in response.headers.get_list("set-cookie")
    }


def cookie_attributes(header: str) -> set[str]:
    """`a=b; HttpOnly; Path=/` -> {"httponly", "path=/"}: the attributes, lower-cased."""
    return {part.strip().lower() for part in header.split(";")[1:]}


def last_link(harness: AuthHarness, marker: str) -> str:
    """The token from the newest email whose body contains `marker`."""
    for message in reversed(harness.outbox):
        body: str = message.body  # type: ignore[attr-defined]
        if marker in body:
            return body.split(f"{marker}#token=", 1)[1].split()[0]
    raise AssertionError(f"no email containing {marker!r}")
