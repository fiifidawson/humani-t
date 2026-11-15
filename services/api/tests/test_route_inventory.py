"""Every route the app registers is checked for sign-in and project membership.

These walk the app's own route table, so a route added later is covered the moment it
exists, and a route meant to be public must be named below, where a reviewer sees it.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts

from tests.project_support import (
    OWNER_EMAIL,
    PASSWORD,
    PROJECTS,
    Team,
    fill_path,
    make_project_harness,
    make_team,
    send,
    signed_in,
)

# Reachable without signing in, each for a reason:
PUBLIC_ROUTES = frozenset(
    {
        ("GET", "/api/v1/health"),  # liveness probe
        ("GET", "/api/v1/ready"),  # readiness probe for the proxy
        ("GET", "/api/v1/info"),  # never mounted in production
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/register"),  # needs an invitation token instead
        ("POST", "/api/v1/auth/password-reset"),
        ("POST", "/api/v1/auth/password-reset/confirm"),  # needs a reset token instead
    }
)

# FastAPI's own documentation routes. Not mounted in production
# (`test_http_hardening.py::TestProductionSurface`).
_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})

# The project contract. Listed so this file fails until the routes exist, rather
# than passing over an empty set.
PROJECT_ROUTES = frozenset(
    {
        ("GET", PROJECTS),
        ("POST", PROJECTS),
        ("GET", f"{PROJECTS}/{{project_id}}"),
        ("PATCH", f"{PROJECTS}/{{project_id}}"),
        ("DELETE", f"{PROJECTS}/{{project_id}}"),
        ("PUT", f"{PROJECTS}/{{project_id}}/masking"),
        ("POST", f"{PROJECTS}/{{project_id}}/archive"),
        ("POST", f"{PROJECTS}/{{project_id}}/unarchive"),
        ("GET", f"{PROJECTS}/{{project_id}}/members"),
        ("POST", f"{PROJECTS}/{{project_id}}/members"),
        ("PATCH", f"{PROJECTS}/{{project_id}}/members/{{user_id}}"),
        ("DELETE", f"{PROJECTS}/{{project_id}}/members/{{user_id}}"),
    }
)

# A project's own documents (walkthrough 16). Every one sits under the project.
_DOCUMENTS = f"{PROJECTS}/{{project_id}}/documents"
DOCUMENT_ROUTES = frozenset(
    {
        ("GET", _DOCUMENTS),
        ("POST", _DOCUMENTS),
        ("GET", f"{_DOCUMENTS}/{{document_id}}"),
        ("DELETE", f"{_DOCUMENTS}/{{document_id}}"),
        ("GET", f"{_DOCUMENTS}/{{document_id}}/masked"),
        ("GET", f"{_DOCUMENTS}/{{document_id}}/review"),
        ("POST", f"{_DOCUMENTS}/{{document_id}}/masks"),
        ("POST", f"{_DOCUMENTS}/{{document_id}}/unmask"),
        ("POST", f"{_DOCUMENTS}/{{document_id}}/approve"),
        ("POST", f"{_DOCUMENTS}/{{document_id}}/reject"),
    }
)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path template) the app answers, documentation aside.

    Walks FastAPI's flattened view, because `app.routes` holds included routers
    as single opaque entries.
    """
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        path, methods = context.path, context.methods
        # A mount or websocket has no methods to check: it needs its own decision.
        assert path is not None, f"unchecked route {context!r}"
        assert methods is not None, f"unchecked route {context!r}"
        if path in _DOCS_PATHS:
            continue
        found |= {(m, path) for m in methods if m not in _IGNORED_METHODS}
    return found


def _protected(app: FastAPI) -> list[tuple[str, str]]:
    return sorted(registered_routes(app) - PUBLIC_ROUTES)


# A review, run or gate belongs to exactly one project, and routes that take one
# of their ids resolve the project from it, so they are inside a project too.
_PROJECT_PARAMETERS = (
    "{project_id}",
    "{review_id}",
    "{run_id}",
    "{gate_id}",
    "{brief_id}",
    "{thread_id}",
    "{question_id}",
)


def _project_scoped(app: FastAPI) -> list[tuple[str, str]]:
    return sorted(
        r for r in registered_routes(app) if any(p in r[1] for p in _PROJECT_PARAMETERS)
    )


def _body_for(method: str) -> dict[str, object] | None:
    # An empty JSON object: valid JSON that no route accepts as a complete body.
    # Sign-in and membership must be checked before the body is, so the answer
    # cannot depend on it.
    return {} if method in _UNSAFE_METHODS else None


def _filled(team: Team, template: str) -> str:
    return fill_path(template, project_id=team.project_id, user_id=team.user_id(OWNER_EMAIL))


class TestInventory:
    def test_the_public_allowlist_names_only_routes_that_exist(self):
        """A stale entry would quietly make a future route public."""
        app = make_project_harness().app

        assert registered_routes(app) >= PUBLIC_ROUTES

    def test_every_project_route_in_the_contract_is_registered(self):
        app = make_project_harness().app

        assert sorted(PROJECT_ROUTES - registered_routes(app)) == []

    def test_every_document_route_in_the_contract_is_registered(self):
        app = make_project_harness().app

        assert sorted(DOCUMENT_ROUTES - registered_routes(app)) == []

    def test_every_route_inside_a_project_names_the_project_in_its_path(self):
        """Membership is checked against the path, never a body or query field."""
        app = make_project_harness().app
        scoped = {r for r in registered_routes(app) if r[1].startswith(f"{PROJECTS}/")}

        assert all("{project_id}" in path for _, path in scoped)


class TestSignInIsRequired:
    def test_every_route_but_the_public_ones_refuses_an_anonymous_request_with_401(self):
        harness = make_project_harness()
        anonymous = harness.browser()

        failures = []
        for method, template in _protected(harness.app):
            response = anonymous.request(method, fill_path(template), json=_body_for(method))
            if response.status_code != 401:
                failures.append((method, template, response.status_code))

        assert failures == []

    def test_every_signed_in_write_refuses_a_missing_csrf_token(self):
        harness = make_project_harness()
        harness.create_user(OWNER_EMAIL, PASSWORD)
        browser = signed_in(harness, OWNER_EMAIL)

        failures = []
        for method, template in _protected(harness.app):
            if method not in _UNSAFE_METHODS:
                continue
            response = browser.request(method, fill_path(template), json=_body_for(method))
            if response.status_code != 403:
                failures.append((method, template, response.status_code))

        assert failures == []


class TestMembershipIsRequired:
    def test_every_project_route_refuses_a_non_member_with_404(self):
        team = make_team()

        failures = []
        for method, template in _project_scoped(team.harness.app):
            response = send(team.outsider, method, _filled(team, template), _body_for(method))
            if response.status_code != 404:
                failures.append((method, template, response.status_code))

        assert _project_scoped(team.harness.app), "no project routes found"
        assert failures == []

    def test_every_project_route_refuses_an_instance_administrator_who_is_not_a_member(self):
        team = make_team()

        failures = []
        for method, template in _project_scoped(team.harness.app):
            response = send(team.admin, method, _filled(team, template), _body_for(method))
            if response.status_code != 404:
                failures.append((method, template, response.status_code))

        assert failures == []

    def test_every_project_route_answers_a_non_member_exactly_as_for_a_missing_project(self):
        team = make_team()
        missing = Team(**{**vars(team), "project_id": "00000000-0000-4000-8000-000000000000"})

        failures = []
        for method, template in _project_scoped(team.harness.app):
            body = _body_for(method)
            existing = send(team.outsider, method, _filled(team, template), body)
            absent = send(team.outsider, method, _filled(missing, template), body)
            if (existing.status_code, existing.json()) != (absent.status_code, absent.json()):
                failures.append((method, template))

        assert failures == []
