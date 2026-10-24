"""What every response carries, and what no response may reveal."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.middleware import SECURITY_HEADERS
from app.main import create_app
from app.settings import Environment, Settings
from tests.test_settings import production_settings


class _UnreachableStorage:
    async def is_ready(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class TestSecurityHeaders:
    def test_every_api_response_carries_the_security_headers(self, client):
        response = client.get("/api/v1/health")

        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value

    def test_api_responses_forbid_loading_framing_and_caching(self, client):
        headers = client.get("/api/v1/health").headers

        assert headers["content-security-policy"].startswith("default-src 'none'")
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["cache-control"] == "no-store"

    def test_error_responses_carry_them_too(self, client):
        response = client.get("/api/v1/does-not-exist")

        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_dev_docs_are_exempt_from_the_csp_only(self, client):
        headers = client.get("/docs").headers

        assert "content-security-policy" not in headers
        assert headers["x-frame-options"] == "DENY"


class TestRequestId:
    def test_a_request_id_is_generated_and_echoed(self, client):
        assert len(client.get("/api/v1/health").headers["x-request-id"]) == 32

    def test_a_well_formed_upstream_id_is_kept(self, client):
        response = client.get("/api/v1/health", headers={"x-request-id": "caddy-abc-12345"})

        assert response.headers["x-request-id"] == "caddy-abc-12345"

    def test_an_id_that_could_inject_into_logs_is_replaced(self, client):
        response = client.get("/api/v1/health", headers={"x-request-id": "x\nfake=entry"})

        assert response.headers["x-request-id"] != "x\nfake=entry"


class TestProblemResponses:
    def test_not_found_is_a_problem_document(self, client):
        response = client.get("/api/v1/nope")

        assert response.headers["content-type"] == "application/problem+json"
        assert response.json() == {
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": "Not Found",
        }

    def test_validation_errors_never_echo_the_submitted_input(self, client):
        personal_data = "Jane Doe, +44 7700 900123"
        response = client.post(
            "/api/v1/vocabulary/compile",
            json={"origin": "llm_assisted", "concepts": personal_data},
        )

        assert response.status_code == 422
        assert personal_data not in response.text
        assert response.json()["errors"][0]["loc"] == ["body", "concepts"]

    def test_unexpected_errors_reveal_nothing(self, settings):
        app = create_app(settings)
        boom = APIRouter()

        @boom.get("/boom")
        async def explode() -> None:
            raise RuntimeError("database password is hunter2")

        app.include_router(boom)
        response = TestClient(app, raise_server_exceptions=False).get("/boom")

        assert response.status_code == 500
        assert "hunter2" not in response.text
        assert response.headers["content-type"] == "application/problem+json"


class TestReadiness:
    def test_ready_when_storage_answers(self, client):
        assert client.get("/api/v1/ready").json() == {"status": "ready"}

    def test_not_ready_when_storage_does_not(self, settings):
        app = create_app(settings)
        app.state.providers = dataclasses.replace(
            app.state.providers,
            storage=_UnreachableStorage(),  # type: ignore[arg-type]
        )

        assert TestClient(app).get("/api/v1/ready").status_code == 503


class TestProductionSurface:
    @pytest.fixture
    def production(self) -> TestClient:
        return TestClient(create_app(production_settings()))

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/api/v1/info"])
    def test_introspection_endpoints_do_not_exist(self, production, path):
        assert production.get(path).status_code == 404

    def test_docs_exist_outside_production(self):
        client = TestClient(create_app(Settings(environment=Environment.DEVELOPMENT)))

        assert client.get("/docs").status_code == 200
