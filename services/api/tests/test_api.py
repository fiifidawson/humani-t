"""The HTTP boundary: system routes and the OpenAPI document."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSystem:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_info_reports_the_configured_paper_sources(self, client):
        body = client.get("/api/v1/info").json()

        assert body["paper_sources"] == []
        assert body["makes_network_calls"] is False
        assert body["api_prefix"] == "/api/v1"


class TestOpenAPI:
    def test_all_endpoints_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert set(paths) == {
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/info",
            "/api/v1/projects",
            "/api/v1/projects/{project_id}",
            "/api/v1/projects/{project_id}/archive",
            "/api/v1/projects/{project_id}/unarchive",
            "/api/v1/projects/{project_id}/members",
            "/api/v1/projects/{project_id}/members/{user_id}",
            "/api/v1/projects/{project_id}/reviews",
            "/api/v1/projects/{project_id}/reviews/{review_id}",
            "/api/v1/reviews/{review_id}/runs",
            "/api/v1/runs/{run_id}/events",
            "/api/v1/runs/{run_id}/cancel",
            "/api/v1/runs/{run_id}/manifest",
            "/api/v1/reviews/{review_id}/papers",
            "/api/v1/reviews/{review_id}/papers/{paper_id}",
            "/api/v1/reviews/{review_id}/prisma",
            "/api/v1/gates/{gate_id}",
            "/api/v1/reviews/{review_id}/brief",
            "/api/v1/briefs/{brief_id}/evidence/{tag}",
            "/api/v1/briefs/{brief_id}/trace",
            "/api/v1/reviews/{review_id}/exports/{name}",
            "/api/v1/reviews/{review_id}/questions",
            "/api/v1/reviews/{review_id}/threads",
            "/api/v1/threads/{thread_id}",
            "/api/v1/questions/{question_id}",
            "/api/v1/projects/{project_id}/masking-rules",
            "/api/v1/projects/{project_id}/masking-rules/{rule_id}",
            "/api/v1/projects/{project_id}/documents",
            "/api/v1/projects/{project_id}/documents/{document_id}",
            "/api/v1/projects/{project_id}/documents/{document_id}/masked",
            "/api/v1/projects/{project_id}/documents/{document_id}/review",
            "/api/v1/projects/{project_id}/documents/{document_id}/masks",
            "/api/v1/projects/{project_id}/documents/{document_id}/unmask",
            "/api/v1/projects/{project_id}/documents/{document_id}/approve",
            "/api/v1/projects/{project_id}/documents/{document_id}/reject",
            "/api/v1/reviews/{review_id}/plan",
            "/api/v1/reviews/{review_id}/plan/answers",
            "/api/v1/reviews/{review_id}/plan/changes",
            "/api/v1/reviews/{review_id}/plan/accept",
            "/api/v1/reviews/{review_id}/plan/vocabulary",
            "/api/v1/reviews/{review_id}/plan/versions/{version}",
            "/api/v1/auth/login",
            "/api/v1/auth/logout",
            "/api/v1/auth/session",
            "/api/v1/auth/reauthenticate",
            "/api/v1/auth/register",
            "/api/v1/auth/password-reset",
            "/api/v1/auth/password-reset/confirm",
            "/api/v1/account/sessions",
            "/api/v1/account/sessions/{session_id}",
            "/api/v1/account/sessions/revoke-others",
            "/api/v1/account/password",
            "/api/v1/auth/mfa/totp",
            "/api/v1/auth/mfa/recovery-code",
            "/api/v1/auth/mfa/passkey/options",
            "/api/v1/auth/mfa/passkey",
            "/api/v1/account/mfa",
            "/api/v1/account/mfa/totp",
            "/api/v1/account/mfa/totp/confirm",
            "/api/v1/account/mfa/passkeys/options",
            "/api/v1/account/mfa/passkeys",
            "/api/v1/account/mfa/passkeys/{passkey_id}",
            "/api/v1/account/mfa/recovery-codes",
            "/api/v1/admin/invitations",
            "/api/v1/admin/users/{user_id}/disable",
            "/api/v1/admin/users/{user_id}/enable",
        }

    def test_request_bodies_carry_a_worked_example(self, client):
        """Swagger's 'Try it out' should prefill something that actually works."""
        schema = client.get("/openapi.json").json()
        example = schema["components"]["schemas"]["MaskingRuleIn"]["example"]

        assert example["term"] == "Kofi Mensah"

    def test_endpoints_are_grouped_by_tag(self, client):
        schema = client.get("/openapi.json").json()
        tags = {t["name"] for t in schema["tags"]}

        assert tags == {
            "system",
            "auth",
            "account",
            "admin",
            "projects",
            "reviews",
            "runs",
            "briefs",
            "exports",
            "questions",
            "plans",
            "papers",
            "masking",
            "documents",
        }
