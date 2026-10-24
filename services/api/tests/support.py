"""Test helpers shared by the authentication specs.

Nothing here is a mock: the clock really moves, the hasher really hashes (just
cheaply), and requests go through the real middleware, routes and storage.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from app.api.dependencies import Providers, build_providers
from app.auth.fake import FakePasswordHasher
from app.main import create_app
from app.settings import Environment, Settings

ORIGIN = "https://testserver"

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "violet-harbour-quietly-41-lanterns"
USER_EMAIL = "amara@example.org"
USER_PASSWORD = "seventeen-copper-kites-over-lagos"
NEW_PASSWORD = "the-tide-returns-at-nine-o-clock"


@dataclass
class FakeClock:
    """A clock tests move by hand, so expiry is tested without sleeping."""

    now: datetime = field(default_factory=lambda: datetime(2026, 10, 24, 9, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class AuthHarness:
    """One app, its providers, and a browser-like client pointed at it."""

    app: FastAPI
    providers: Providers
    clock: FakeClock
    hasher: FakePasswordHasher

    def browser(self) -> TestClient:
        """A new client with its own cookie jar — a separate browser."""
        return TestClient(self.app, base_url=ORIGIN, headers={"Origin": ORIGIN})

    def create_user(self, email: str, password: str, *, is_admin: bool = False) -> None:
        async def create() -> None:
            async with self.providers.storage.unit_of_work() as uow:
                await self.providers.accounts.create_user(
                    uow,
                    email=email,
                    password=password,
                    display_name=email.split("@")[0],
                    is_admin=is_admin,
                )
                await uow.commit()

        asyncio.run(create())

    @property
    def outbox(self) -> list[object]:
        return self.providers.email.outbox  # type: ignore[attr-defined, no-any-return]


def make_harness(**settings_overrides: object) -> AuthHarness:
    clock = FakeClock()
    hasher = FakePasswordHasher()
    values: dict[str, object] = {"environment": Environment.TEST, "public_origin": ORIGIN}
    values.update(settings_overrides)
    settings = Settings.model_validate(values)
    providers = build_providers(settings, clock=clock, password_hasher=hasher)
    return AuthHarness(create_app(settings, providers=providers), providers, clock, hasher)


def plant_session_cookie(client: TestClient, value: str) -> None:
    """Put a session cookie in the jar exactly where the browser itself would.

    The standard library files cookies for a dotless host such as `testserver`
    under `testserver.local`; a cookie set for plain `testserver` is never sent,
    and a test using one would pass without testing anything.
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
