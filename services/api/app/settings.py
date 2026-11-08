"""Configuration read from the environment and from Docker secret files.

This is one of the two places Pydantic is allowed (the other is `api/schemas`),
because the environment is untrusted input just like a request body. Only the
composition root — `main.py`, `api/dependencies.py`, `cli.py` — may import
`Settings`. Everything below it receives plain values or domain policy objects.

Secrets are read from `/run/secrets/<name>` when that directory exists, so a
production deployment never needs a secret in an environment variable.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import SplitResult, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
)

from app.net.addresses import parse_ip_literal

_SECRETS_DIR = Path("/run/secrets")
_PREFIX = "humani_t_"

PAPER_SOURCE_IDS = ("openalex", "europepmc", "arxiv")
# Made-up papers generated in this process, for development without the internet.
DEMO_SOURCE_ID = "demo"

# Model APIs run by a third party. Not exhaustive: it catches the common
# mistake, and the operator's choice of URL remains the real control.
_HOSTED_INFERENCE_DOMAINS = (
    "openai.com",
    "openai.azure.com",
    "cognitiveservices.azure.com",
    "services.ai.azure.com",
    "anthropic.com",
    "googleapis.com",
    "ollama.com",
    "mistral.ai",
    "groq.com",
    "openrouter.ai",
    "together.ai",
    "together.xyz",
    "fireworks.ai",
    "deepseek.com",
    "x.ai",
    "perplexity.ai",
    "cohere.com",
    "cohere.ai",
    "huggingface.co",
    "replicate.com",
    "deepinfra.com",
    "cerebras.ai",
)
# A signed-in Ollama daemon runs these on ollama.com, however local its URL is.
_OLLAMA_CLOUD_MODEL = re.compile(r"[-:]cloud$", re.IGNORECASE)
# Plain http is acceptable only where the network itself is the operator's:
# loopback, private ranges, CGNAT (Tailscale and similar VPNs) and IPv6 ULA.
_INTERNAL_NETWORKS = (
    IPv4Network("127.0.0.0/8"),
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
    IPv4Network("100.64.0.0/10"),
    IPv6Network("::1/128"),
    IPv6Network("fc00::/7"),
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class _UnknownNamesRefused(PydanticBaseSettingsSource):
    """Refuse any HUMANI_T_ variable or secret file that names no setting.

    Pydantic skips them, so a misspelt name falls back to its default without a
    word: `HUMANI_T_ENVIROMENT=production` would start a development server.
    Only the name is reported, because the value may be a secret.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        environment: PydanticBaseSettingsSource,
        secret_files: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._environment = environment
        self._secret_files = secret_files

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        known = set(self.settings_cls.model_fields)
        found = [
            *((name.upper(), "environment variable") for name in self._variable_names()),
            *((name, "secret file") for name in self._secret_file_names()),
        ]
        unknown = [
            f"{name} ({kind})"
            for name, kind in found
            if name.lower().removeprefix(_PREFIX) not in known
        ]
        if unknown:
            raise ValueError(
                f"unknown setting {', '.join(sorted(unknown))}: every HUMANI_T_ name "
                "must be one of the settings in .env.example"
            )
        return {}

    def _variable_names(self) -> list[str]:
        if not isinstance(self._environment, EnvSettingsSource):
            return []
        return [name for name in self._environment.env_vars if name.lower().startswith(_PREFIX)]

    def _secret_file_names(self) -> list[str]:
        if not isinstance(self._secret_files, SecretsSettingsSource):
            return []
        configured = self._secret_files.secrets_dir
        if configured is None:
            return []
        directories = (
            [configured] if isinstance(configured, str | os.PathLike) else list(configured)
        )
        return [
            path.name
            for directory in map(Path, directories)
            if directory.is_dir()
            for path in directory.iterdir()
            if path.name.lower().startswith(_PREFIX)
        ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HUMANI_T_",
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
        extra="forbid",
        frozen=True,
        # Validation errors otherwise repeat their input, and the input here is
        # the database and Valkey URLs, passwords included, printed at startup.
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            _UnknownNamesRefused(settings_cls, env_settings, file_secret_settings),
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
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
    db_pool_size: int = Field(
        default=10,
        ge=1,
        description="Connections per API process. A hard limit: no overflow beyond it.",
    )
    db_pool_timeout_seconds: float = Field(
        default=5,
        gt=0,
        description="How long a request waits for a free connection before a 503.",
    )
    db_statement_timeout_ms: int = Field(
        default=15_000,
        ge=1000,
        description="Postgres cancels any single statement that runs longer.",
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
    inference_api: Literal["ollama", "openai"] = Field(
        default="ollama",
        description=(
            "What the model server speaks: Ollama's own API, or OpenAI's Chat Completions "
            "(OpenAI, Azure OpenAI, Mistral, Groq, vLLM…). A hosted one still needs "
            "allow_hosted_inference."
        ),
    )
    inference_url: str = Field(
        default="http://ollama:11434",
        description=(
            "The model server. Plain http only to a host inside the deployment in "
            "production; https with inference_token for a remote one. For the openai "
            "format, the base URL that ends before /chat/completions."
        ),
    )
    inference_model: str = Field(default="llama3.1:8b", min_length=1)
    inference_token: SecretStr | None = Field(
        default=None,
        description=(
            "Bearer token: a hosted API's key, or a token for a model server behind an "
            "authenticating proxy."
        ),
    )
    inference_timeout_seconds: float = Field(
        default=300,
        gt=0,
        description="How long one model call may take, including loading the model.",
    )
    allow_hosted_inference: bool = Field(
        default=False,
        description=(
            "Permit a third-party model API (OpenAI, Anthropic, ollama.com…). Off by "
            "default: turning it on sends masked text outside the deployment."
        ),
    )
    paper_sources: str = Field(
        default="",
        description="Comma-separated sources to search: openalex, europepmc, arxiv.",
    )
    papers_per_source: int = Field(
        default=25, ge=1, le=200, description="How many papers Discover asks each source for."
    )
    screening_concurrency: int = Field(
        default=2,
        ge=1,
        le=16,
        description=(
            "How many papers Screen sends to the model at once. Keep it low for a "
            "CPU-only model server."
        ),
    )
    brief_max_rounds: int = Field(
        default=3,
        ge=1,
        le=10,
        description="How many Writer and Critic rounds the decision brief may take.",
    )
    openalex_mailto: str | None = Field(
        default=None, description="Contact address sent to OpenAlex with each request."
    )
    openalex_api_key: SecretStr | None = Field(
        default=None, description="OpenAlex API key: a larger daily budget and rate limit."
    )
    trusted_proxies: str = Field(
        default="",
        description=(
            "Comma-separated addresses or networks of the reverse proxies whose "
            "X-Forwarded-For is believed. Empty: none, and the connecting address is "
            "the client."
        ),
    )
    masking_ner: Literal["auto", "spacy", "off"] = Field(
        default="auto",
        description=(
            "The local name-and-place detector for masking: auto uses spaCy when the "
            "ner extra is installed; spacy requires it; off masks patterns and rules only."
        ),
    )
    masking_ner_model: str = Field(
        default="en_core_web_sm",
        description="An installed spaCy model package, or an absolute path to a model folder.",
    )
    document_storage_dir: Path | None = Field(
        default=None,
        description=(
            "Directory (a volume) for uploaded files, stored sealed under each project's "
            "key. Unset: in memory with in-memory storage; uploads off with a database."
        ),
    )
    upload_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        le=200 * 1024 * 1024,
        description="The largest file accepted, in bytes. Keep clamd's StreamMaxLength above.",
    )
    documents_per_project: int = Field(
        default=200, ge=1, le=10_000, description="How many documents a project may hold."
    )
    document_bytes_per_project: int = Field(
        default=1024 * 1024 * 1024,
        ge=1024,
        description="How many bytes of uploaded files a project may hold.",
    )
    uploads_per_hour: int = Field(
        default=60, ge=1, le=10_000, description="Uploads one person may make per hour."
    )
    malware_scanner: Literal["clamd", "none"] | None = Field(
        default=None,
        description=(
            "clamd scans every upload with the operator's ClamAV daemon; none skips scanning "
            "and says so on each document. Unset in production: uploads are refused."
        ),
    )
    clamd_address: str | None = Field(
        default=None,
        description=(
            "tcp://host:port or unix:///path of a clamd inside the deployment: it receives "
            "the raw files."
        ),
    )
    extraction_timeout_seconds: float = Field(
        default=60, gt=0, le=600, description="How long reading one file may take."
    )
    extraction_max_pages: int = Field(
        default=500, ge=1, le=10_000, description="Pages of a PDF read; the rest are dropped."
    )
    extraction_max_characters: int = Field(
        default=2_000_000,
        ge=1000,
        le=50_000_000,
        description="Characters of text kept per document; the rest are dropped.",
    )
    run_worker_in_process: bool | None = Field(
        default=None,
        description=(
            "Work queued runs inside the API process. Unset: on with in-memory storage, "
            "which a separate worker cannot see; off with a database. Refused in production."
        ),
    )
    demo_inference: bool = Field(
        default=False,
        description=(
            "Development only: answer every model call with canned answers made in this "
            "process, so the whole flow runs without a model server. Refused in production."
        ),
    )
    dev_admin_email: str | None = Field(
        default=None,
        description=(
            "Development only, in-memory storage only: create this administrator at start, "
            "since `create-admin` cannot reach another process's memory."
        ),
    )
    dev_admin_password: SecretStr | None = Field(
        default=None, description="The development administrator's password."
    )
    log_level: str = "INFO"

    @field_validator("trusted_proxies")
    @classmethod
    def _trusted_proxies_are_addresses(cls, value: str) -> str:
        for entry in _comma_separated(value):
            try:
                ip_network(entry)
            except ValueError as exc:
                raise ValueError(
                    f"trusted_proxies must list IP addresses or networks, not {entry!r}"
                ) from exc
        return value

    @property
    def trusted_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return tuple(ip_network(entry) for entry in _comma_separated(self.trusted_proxies))

    @field_validator("malware_scanner", "clamd_address", "document_storage_dir", mode="before")
    @classmethod
    def _blank_scanner_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _scanner_is_complete(self) -> Self:
        if self.malware_scanner == "clamd" and not self.clamd_address:
            raise ValueError("malware_scanner clamd requires clamd_address")
        if self.clamd_address and self.malware_scanner != "clamd":
            raise ValueError("clamd_address is set but malware_scanner is not clamd")
        if self.document_bytes_per_project < self.upload_max_bytes:
            raise ValueError("document_bytes_per_project must be at least upload_max_bytes")
        return self

    @property
    def uploads_disabled_reason(self) -> str | None:
        """Why uploads are off, for the people affected; `None` when they are on."""
        if self.database_url is not None and self.document_storage_dir is None:
            return (
                "Uploads are switched off: the operator has not set a storage directory "
                "for documents."
            )
        if self.is_production and self.malware_scanner is None:
            return (
                "Uploads are switched off: the operator has not configured a malware scanner."
            )
        return None

    @property
    def runs_worker_in_process(self) -> bool:
        if self.run_worker_in_process is not None:
            return self.run_worker_in_process
        return self.database_url is None

    @field_validator(
        "inference_token",
        "openalex_api_key",
        "openalex_mailto",
        "dev_admin_email",
        "dev_admin_password",
        "run_worker_in_process",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # `NAME=` in an env file arrives as "", and means "not configured".
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return None if value == "" else value

    @property
    def paper_source_ids(self) -> tuple[str, ...]:
        return tuple(s.strip().lower() for s in self.paper_sources.split(",") if s.strip())

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

    @model_validator(mode="after")
    def _inference_stays_with_the_operator(self) -> Self:
        url = _inference_url_parts(self.inference_url)
        host = (url.hostname or "").rstrip(".").lower()
        if not self.allow_hosted_inference:
            if any(host == d or host.endswith(f".{d}") for d in _HOSTED_INFERENCE_DOMAINS):
                raise ValueError(
                    f"inference_url points at a hosted model API ({host}); "
                    "set allow_hosted_inference to send text to a third party"
                )
            if _OLLAMA_CLOUD_MODEL.search(self.inference_model):
                raise ValueError(
                    f"inference_model {self.inference_model!r} is an Ollama cloud model, "
                    "hosted on ollama.com; set allow_hosted_inference to use it"
                )
        if self.is_production and url.scheme == "http" and not _is_internal_host(host):
            raise ValueError(
                "production requires https for an inference_url outside the deployment"
            )
        return self

    @model_validator(mode="after")
    def _paper_sources_are_known(self) -> Self:
        ids = self.paper_source_ids
        unknown = [i for i in ids if i not in (*PAPER_SOURCE_IDS, DEMO_SOURCE_ID)]
        if unknown:
            raise ValueError(
                f"paper_sources names an unknown source: {', '.join(unknown)} "
                f"(available: {', '.join(PAPER_SOURCE_IDS)}; Google Scholar has no API)"
            )
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ValueError(f"paper_sources names {', '.join(repeated)} more than once")
        if self.openalex_mailto is not None and not _EMAIL.fullmatch(self.openalex_mailto):
            raise ValueError("openalex_mailto must be an email address")
        return self

    @model_validator(mode="after")
    def _development_conveniences_stay_out_of_production(self) -> Self:
        if (self.dev_admin_email is None) != (self.dev_admin_password is None):
            raise ValueError("dev_admin_email and dev_admin_password are set together")
        if self.dev_admin_email is not None:
            if not _EMAIL.fullmatch(self.dev_admin_email):
                raise ValueError("dev_admin_email must be an email address")
            if self.database_url is not None:
                raise ValueError(
                    "dev_admin_email is for in-memory storage only; with a database, "
                    "use `python -m app.cli create-admin`"
                )
        if not self.is_production:
            return self
        refused = [
            name
            for name, is_set in (
                ("run_worker_in_process", self.run_worker_in_process is True),
                ("demo_inference", self.demo_inference),
                (f"paper_sources={DEMO_SOURCE_ID}", DEMO_SOURCE_ID in self.paper_source_ids),
                ("dev_admin_email", self.dev_admin_email is not None),
            )
            if is_set
        ]
        if refused:
            raise ValueError(
                f"production refuses development-only settings: {', '.join(refused)}"
            )
        return self


def _inference_url_parts(url: str) -> SplitResult:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError("inference_url must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("inference_url must name a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("inference_url must not contain credentials; use inference_token")
    if parts.query or parts.fragment:
        raise ValueError("inference_url must not have a query or fragment")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("inference_url must have a valid port") from exc
    return parts


def _comma_separated(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _is_internal_host(host: str) -> bool:
    """A compose service name, localhost, or an address on the operator's own network.

    Decided from the name alone: settings never touch DNS. A dotted name that
    resolves to a private address still needs https, or a single-label alias.
    """
    if "." not in host and parse_ip_literal(host) is None:
        return True
    address = parse_ip_literal(host)
    return address is not None and any(address in n for n in _INTERNAL_NETWORKS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
