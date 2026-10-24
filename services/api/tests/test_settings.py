"""Configuration fails closed: a production process without its secrets never starts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.crypto.local_key import generate_kek
from app.settings import Environment, Settings

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "public_origin": "https://reviews.example.org",
        # Never connected to: engines connect lazily, and these tests make no queries.
        "database_url": SecretStr("postgresql+asyncpg://app:pw@db.invalid/humani_t"),
        "key_encryption_keys": SecretStr(f"1:{generate_kek()}"),
    }
    values.update(overrides)
    return Settings.model_validate(values)


class TestProductionRequirements:
    def test_complete_production_settings_are_accepted(self):
        assert production_settings().is_production

    @pytest.mark.parametrize("missing", ["database_url", "key_encryption_keys"])
    def test_missing_backend_is_refused(self, missing):
        with pytest.raises(ValidationError):
            production_settings(**{missing: None})

    def test_plain_http_origin_is_refused(self):
        with pytest.raises(ValidationError, match="https"):
            production_settings(public_origin="http://reviews.example.org")

    def test_docs_are_disabled(self):
        assert production_settings().docs_enabled is False


class TestAnyEnvironment:
    def test_a_database_without_a_kek_is_refused(self):
        with pytest.raises(ValidationError, match="key_encryption_keys"):
            Settings(
                environment=Environment.DEVELOPMENT,
                database_url=SecretStr("postgresql+asyncpg://x@localhost/db"),
            )

    def test_unknown_settings_are_refused(self):
        with pytest.raises(ValidationError):
            Settings.model_validate({"enviroment": "production"})

    def test_secrets_are_not_shown_in_repr(self):
        settings = production_settings()

        assert "pw@" not in repr(settings)


def test_env_example_documents_every_setting():
    documented = {
        line.split("=", 1)[0].removeprefix("HUMANI_T_").lower()
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith("HUMANI_T_")
    }

    assert documented == set(Settings.model_fields)
