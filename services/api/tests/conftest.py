"""Shared fixtures. Every test builds its own app from explicit settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Environment, Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(environment=Environment.TEST)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
