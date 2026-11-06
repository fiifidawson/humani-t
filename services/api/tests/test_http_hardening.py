"""What every response carries, and what no response may reveal."""

from __future__ import annotations

import dataclasses

import pytest
import structlog
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import SECURITY_HEADERS
from app.domain.inference import InferenceUnavailableError
from app.domain.sources import PaperSourceUnavailableError
from app.main import create_app
from app.settings import Environment, Settings
from tests.project_support import OWNER_EMAIL, PASSWORD, make_project_harness, signed_in
from tests.support import csrf_headers
from tests.test_settings import production_settings

# What an upstream failure may carry, none of which belongs in a response.
_UPSTREAM_DETAILS = ("api.openalex.org", "10.0.0.5", "ollama:11434", "connection refused")


def _app_failing_with(settings: Settings, error: Exception) -> FastAPI:
    app = create_app(settings)
    failing = APIRouter()

    @failing.get("/boom")
    async def explode() -> None:
        raise error

    app.include_router(failing)
    return app


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

    def test_validation_errors_never_echo_the_submitted_input(self):
        # Compiling needs a signed-in user since M10; the rule under test is unchanged.
        harness = make_project_harness()
        harness.create_user(OWNER_EMAIL, PASSWORD)
        browser = signed_in(harness, OWNER_EMAIL)
        personal_data = "Jane Doe, +44 7700 900123"
        response = browser.post(
            "/api/v1/vocabulary/compile",
            json={"origin": "llm_assisted", "concepts": personal_data},
            headers=csrf_headers(browser),
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


class TestUnexpectedErrors:
    """A 500 passes through the same middleware as every other response."""

    @pytest.fixture
    def failing(self, settings: Settings) -> TestClient:
        error = RuntimeError("database password is hunter2")
        return TestClient(_app_failing_with(settings, error), raise_server_exceptions=False)

    def test_an_unexpected_error_is_a_problem_document_without_its_exception(self, failing):
        response = failing.get("/boom")

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["status"] == 500
        for leak in ("hunter2", "RuntimeError", "Traceback", "explode"):
            assert leak not in response.text

    def test_an_unexpected_error_carries_every_security_header(self, failing):
        headers = failing.get("/boom").headers

        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value
        assert headers["content-security-policy"].startswith("default-src 'none'")
        assert headers["cache-control"] == "no-store"

    def test_an_unexpected_error_carries_the_request_id_it_asks_users_to_quote(self, failing):
        response = failing.get("/boom", headers={"x-request-id": "caddy-abc-12345"})

        assert response.headers["x-request-id"] == "caddy-abc-12345"

    def test_the_error_is_logged_with_the_same_request_id(self, failing):
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            response = failing.get("/boom")

        logged = [entry for entry in logs if entry["event"] == "unhandled_error"]
        assert len(logged) == 1
        assert logged[0]["request_id"] == response.headers["x-request-id"]


class TestUnavailableUpstreams:
    """A paper source or model server being down is the upstream's failure, not ours."""

    @pytest.mark.parametrize(
        "error",
        [
            PaperSourceUnavailableError(
                "openalex", "https://api.openalex.org/works: 10.0.0.5 connection refused"
            ),
            InferenceUnavailableError("http://ollama:11434/api/chat: connection refused"),
        ],
        ids=["paper-source", "model-server"],
    )
    def test_an_unavailable_upstream_answers_502_naming_no_host_or_upstream_text(
        self, settings, error
    ):
        client = TestClient(_app_failing_with(settings, error), raise_server_exceptions=False)

        response = client.get("/boom")

        assert response.status_code == 502
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"]
        for detail in _UPSTREAM_DETAILS:
            assert detail not in response.text.lower()


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
