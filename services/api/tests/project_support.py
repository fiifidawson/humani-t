"""Helpers for the project specs: one project, and a team of signed-in browsers around it.

Nothing is mocked. Amara owns the project, Kofi reviews and Maya views; Tariq and the
instance administrator are not members, because administering the deployment grants
nothing inside a project.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response

from app.domain.audit import AuditEvent, ChainReport
from app.domain.encryption import EncryptionContext
from tests.support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    USER_EMAIL,
    USER_PASSWORD,
    AuthHarness,
    csrf_headers,
    login,
    make_harness,
)

PROJECTS = "/api/v1/projects"

OWNER_EMAIL = USER_EMAIL
REVIEWER_EMAIL = "kofi@example.org"
VIEWER_EMAIL = "maya@example.org"
OUTSIDER_EMAIL = "tariq@example.org"
PASSWORD = USER_PASSWORD

# Realistic and sensitive on purpose: a project name alone can reveal an
# operation, a place and a population. It must never reach storage or the
# audit log in the clear.
PROJECT_NAME = "Cholera response, Sana'a"

# How the project's name is bound when encrypted: table "projects", column
# "name", row = the project's id.
NAME_PURPOSE = "projects.name"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass
class Team:
    """One project and everyone the specs need around it, each in their own browser."""

    harness: AuthHarness
    owner: TestClient
    reviewer: TestClient
    viewer: TestClient
    outsider: TestClient
    admin: TestClient
    project_id: str

    def path(self, suffix: str = "") -> str:
        return f"{PROJECTS}/{self.project_id}{suffix}"

    def user_id(self, email: str) -> str:
        return user_id(self.harness, email)

    def browser_for(self, role: str) -> TestClient:
        return {"owner": self.owner, "reviewer": self.reviewer, "viewer": self.viewer}[role]


def make_project_harness() -> AuthHarness:
    """Return a harness in which a password sign-in is a full sign-in.

    Project specs cover authorisation, so they opt out of MFA; the MFA specs cover it.
    """
    if "mfa_policy" not in inspect.signature(make_harness).parameters:
        return make_harness()
    mfa = importlib.import_module("app.domain.mfa")
    return make_harness(mfa_policy=mfa.MfaPolicy(is_required=False))


def make_team() -> Team:
    harness = make_project_harness()
    harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
    for email in (OWNER_EMAIL, REVIEWER_EMAIL, VIEWER_EMAIL, OUTSIDER_EMAIL):
        harness.create_user(email, PASSWORD)

    owner = signed_in(harness, OWNER_EMAIL)
    project_id = create_project(owner)["id"]
    add_member(owner, project_id, REVIEWER_EMAIL, "reviewer")
    add_member(owner, project_id, VIEWER_EMAIL, "viewer")
    return Team(
        harness=harness,
        owner=owner,
        reviewer=signed_in(harness, REVIEWER_EMAIL),
        viewer=signed_in(harness, VIEWER_EMAIL),
        outsider=signed_in(harness, OUTSIDER_EMAIL),
        admin=signed_in(harness, ADMIN_EMAIL, ADMIN_PASSWORD),
        project_id=project_id,
    )


def signed_in(harness: AuthHarness, email: str, password: str = PASSWORD) -> TestClient:
    browser = harness.browser()
    response = login(browser, email, password)
    assert response.status_code == 200, f"sign-in as {email} failed: {response.text}"
    return browser


def send(
    browser: TestClient, method: str, path: str, body: dict[str, Any] | None = None
) -> Response:
    """Send a request the way the SPA does: JSON body and the CSRF header."""
    return browser.request(method, path, json=body, headers=csrf_headers(browser))


def create_project(browser: TestClient, name: str = PROJECT_NAME) -> dict[str, Any]:
    response = send(browser, "POST", PROJECTS, {"name": name})
    assert response.status_code == 201, f"POST {PROJECTS} -> {response.status_code}"
    body: dict[str, Any] = response.json()
    return body


def add_member(browser: TestClient, project_id: str, email: str, role: str) -> dict[str, Any]:
    path = f"{PROJECTS}/{project_id}/members"
    response = send(browser, "POST", path, {"email": email, "role": role})
    assert response.status_code == 201, f"POST {path} -> {response.status_code}"
    body: dict[str, Any] = response.json()
    return body


def fill_path(template: str, **values: str) -> str:
    """Replace `{name}` placeholders; any not given becomes a fresh random UUID."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), str(uuid.uuid4())), template)


def user_id(harness: AuthHarness, email: str) -> str:
    users = harness.storage.state.users
    return str(next(u.id for u in users.values() if u.email == email))


def audit_events(harness: AuthHarness) -> list[AuditEvent]:
    async def read() -> list[AuditEvent]:
        async with harness.providers.storage.unit_of_work() as uow:
            return await uow.audit.list_events()

    return asyncio.run(read())


def project_events(harness: AuthHarness, project_id: str) -> list[AuditEvent]:
    """Events about one project: every one of them carries its id as `scope_id`."""
    scope = UUID(project_id)
    return [e for e in audit_events(harness) if e.entry.scope_id == scope]


def verify_audit(harness: AuthHarness) -> ChainReport:
    """What `app.cli verify-audit` runs, against this harness's storage."""

    async def verify() -> ChainReport:
        async with harness.providers.storage.unit_of_work() as uow:
            return await harness.providers.audit.verify(uow)

    return asyncio.run(verify())


def stored_name_ciphertext(harness: AuthHarness, project_id: str) -> bytes:
    """The project's name exactly as storage holds it — what a backup would contain."""

    async def read() -> bytes:
        async with harness.providers.storage.unit_of_work() as uow:
            project = await uow.projects.get(UUID(project_id))  # type: ignore[attr-defined]
        assert project is not None
        sealed: bytes = project.name_sealed
        return sealed

    return asyncio.run(read())


def decrypt_name(harness: AuthHarness, project_id: str, sealed: bytes) -> bytes:
    context = EncryptionContext(UUID(project_id), NAME_PURPOSE, project_id)

    async def decrypt() -> bytes:
        async with harness.providers.storage.unit_of_work() as uow:
            return await harness.providers.encryption.decrypt(uow, context, sealed)

    return asyncio.run(decrypt())


def stored_memberships(harness: AuthHarness, project_id: str) -> list[Any]:
    async def read() -> list[Any]:
        async with harness.providers.storage.unit_of_work() as uow:
            members: list[Any] = await uow.memberships.list_for_project(  # type: ignore[attr-defined]
                UUID(project_id)
            )
            return members

    return asyncio.run(read())
