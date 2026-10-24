"""The health endpoint, through FastAPI's test client — no running server needed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_versioned(client):
    """Unversioned paths must not work. V2 will serve the same contract."""
    assert client.get("/health").status_code == 404


def test_openapi_schema_is_served(client):
    schema = client.get("/openapi.json").json()

    assert "/api/v1/health" in schema["paths"]
    assert schema["info"]["title"] == "humani-t API"
