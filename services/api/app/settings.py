"""Configuration read from the environment and from Docker secret files.

This is one of the two places Pydantic is allowed (the other is `api/schemas`),
because the environment is untrusted input just like a request body. Only the
composition root — `main.py`, `api/dependencies.py`, `cli.py` — may import
`Settings`. Everything below it receives plain values or domain policy objects.

Secrets are read from `/run/secrets/<name>` when that directory exists, so a
production deployment never needs a secret in an environment variable.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETS_DIR = Path("/run/secrets")


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HUMANI_T_",
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
        extra="forbid",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    public_origin: str = Field(
        default="https://localhost",
        description="The one origin the browser uses for both the SPA and the API.",
    )
    database_url: SecretStr | None = Field(
        default=None,
        description=(
            "postgresql+asyncpg://… for the runtime role. Unset uses in-memory storage."
        ),
    )
    migration_database_url: SecretStr | None = Field(
        default=None,
        description=(
            "postgresql+asyncpg://… for the schema-owner role, used only by Alembic. "
            "Kept separate so the running app cannot alter or drop tables."
        ),
    )
    key_encryption_keys: SecretStr | None = Field(
        default=None,
        description=(
            "Comma-separated `version:base64key` pairs, e.g. `1:…,2:…`. The highest "
            "version encrypts new data keys; older versions still decrypt."
        ),
    )
    valkey_url: SecretStr | None = Field(
        default=None,
        description=(
            "redis://:password@host:port/0 for rate limits. Unset uses in-process limits."
        ),
    )
    smtp_host: str | None = Field(
        default=None,
        description=(
            "Mail server for sign-in links. Unset keeps mail in memory (development only)."
        ),
    )
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender: str = "humani-t <no-reply@localhost>"
    smtp_starttls: bool = True
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        # Swagger UI loads its scripts from a CDN, which a strict CSP forbids,
        # and it advertises every route to anyone who asks.
        return not self.is_production

    @model_validator(mode="after")
    def _persistent_data_requires_a_kek(self) -> Self:
        # Without a configured KEK the app generates one per process. With a
        # database, everything encrypted would be unreadable after a restart.
        if self.database_url is not None and self.key_encryption_keys is None:
            raise ValueError("database_url requires key_encryption_keys")
        return self

    @model_validator(mode="after")
    def _production_requires_real_backends(self) -> Self:
        if not self.is_production:
            return self
        missing = [
            name
            for name in ("database_url", "key_encryption_keys", "valkey_url", "smtp_host")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"production requires: {', '.join(missing)}")
        if not self.public_origin.startswith("https://"):
            raise ValueError("production requires an https:// public_origin")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
