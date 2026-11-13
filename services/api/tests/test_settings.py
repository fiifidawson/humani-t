"""Configuration fails closed: a production process without its secrets never starts."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.crypto.local_key import generate_kek
from app.settings import Environment, Settings

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _SERVICE_ROOT / ".env.example"

# Distinctive, so finding either half anywhere can only mean it leaked. The host
# is short because Pydantic shortens a long input to its two ends, which would
# hide the password in this test while still showing it for a shorter URL.
_DATABASE_PASSWORD_PARTS = ("Quokka", "8841")
_DATABASE_URL = "postgresql+asyncpg://app:Quokka-8841@db/h"


def _shows_the_password(text: str) -> bool:
    return any(part in text for part in _DATABASE_PASSWORD_PARTS)


def _settings_reading_secrets_from(directory: Path) -> Settings:
    # `_secrets_dir` is a pydantic-settings option the type checker cannot see:
    # it derives the signature from the fields alone.
    options: dict[str, Any] = {"_secrets_dir": directory}
    return Settings(**options)


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The process environment with no HUMANI_T_ variable but those a test sets."""
    for name in list(os.environ):
        if name.upper().startswith("HUMANI_T_"):
            monkeypatch.delenv(name)
    return monkeypatch


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "public_origin": "https://reviews.example.org",
        # Never connected to: engines connect lazily, and these tests make no queries.
        "database_url": SecretStr("postgresql+asyncpg://app:pw@db.invalid/humani_t"),
        "key_encryption_keys": SecretStr(f"1:{generate_kek()}"),
        "valkey_url": SecretStr("redis://:pw@valkey.invalid:6379/0"),
        "smtp_host": "smtp.invalid",
    }
    values.update(overrides)
    return Settings.model_validate(values)


class TestProductionRequirements:
    def test_complete_production_settings_are_accepted(self):
        assert production_settings().is_production

    @pytest.mark.parametrize(
        "missing", ["database_url", "key_encryption_keys", "valkey_url", "smtp_host"]
    )
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


class TestCountriesAndRegionsSetting:
    def test_countries_and_regions_are_masked_unless_the_operator_says_otherwise(self):
        assert Settings(environment=Environment.TEST).mask_countries_and_regions is True


class TestInferenceDestination:
    """The model server is the operator's choice, and a hosted API is never a default."""

    def test_the_default_is_the_compose_model_server(self):
        settings = Settings(environment=Environment.TEST)

        assert settings.inference_url == "http://ollama:11434"
        assert settings.allow_hosted_inference is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://my-resource.openai.azure.com",
            "https://api.anthropic.com",
            "https://generativelanguage.googleapis.com",
            "https://ollama.com",
            "https://api.mistral.ai",
            "https://api.groq.com/openai",
            "https://openrouter.ai/api",
            "https://API.OpenAI.com./v1",
        ],
    )
    def test_a_hosted_inference_api_is_refused_in_any_environment(self, url):
        with pytest.raises(ValidationError, match="hosted"):
            Settings(environment=Environment.DEVELOPMENT, inference_url=url)
        with pytest.raises(ValidationError, match="hosted"):
            production_settings(inference_url=url)

    def test_the_wire_format_defaults_to_ollama(self):
        assert Settings(environment=Environment.TEST).inference_api == "ollama"

    def test_the_openai_wire_format_is_accepted(self):
        settings = Settings(environment=Environment.TEST, inference_api="openai")

        assert settings.inference_api == "openai"

    def test_an_unknown_wire_format_is_refused(self):
        with pytest.raises(ValidationError, match="inference_api"):
            Settings.model_validate({"environment": "test", "inference_api": "anthropic"})

    def test_choosing_the_openai_format_does_not_open_hosted_apis(self):
        with pytest.raises(ValidationError, match="hosted"):
            Settings(
                environment=Environment.DEVELOPMENT,
                inference_api="openai",
                inference_url="https://api.openai.com/v1",
            )

    def test_a_hosted_api_is_accepted_only_when_the_operator_opts_in(self):
        settings = production_settings(
            inference_url="https://api.openai.com/v1", allow_hosted_inference=True
        )

        assert settings.allow_hosted_inference is True

    @pytest.mark.parametrize(
        "model", ["gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "x:cloud"]
    )
    def test_an_ollama_cloud_model_is_hosted_even_behind_a_local_server(self, model):
        """A signed-in Ollama daemon forwards `*-cloud` models to ollama.com."""
        with pytest.raises(ValidationError, match="hosted"):
            Settings(environment=Environment.TEST, inference_model=model)

        allowed = Settings(
            environment=Environment.TEST, inference_model=model, allow_hosted_inference=True
        )
        assert allowed.inference_model == model

    def test_a_name_that_merely_contains_a_hosted_domain_is_not_hosted(self):
        settings = production_settings(inference_url="https://notopenai.com.example.org")

        assert settings.inference_url == "https://notopenai.com.example.org"

    @pytest.mark.parametrize(
        "url",
        [
            "http://ollama:11434",
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://10.8.0.2:11434",
            "http://[fd00::2]:11434",
            "https://models.example.org",
        ],
    )
    def test_production_accepts_https_or_plain_http_on_an_internal_host(self, url):
        assert production_settings(inference_url=url).inference_url == url

    @pytest.mark.parametrize(
        "url", ["http://models.example.org:11434", "http://203.0.113.10:11434"]
    )
    def test_production_refuses_plain_http_to_a_host_outside_the_deployment(self, url):
        with pytest.raises(ValidationError, match="https"):
            production_settings(inference_url=url)

    def test_development_may_use_plain_http_anywhere(self):
        settings = Settings(
            environment=Environment.DEVELOPMENT, inference_url="http://192.0.2.10:11434"
        )

        assert settings.inference_url == "http://192.0.2.10:11434"

    @pytest.mark.parametrize(
        "url",
        [
            "ollama:11434",
            "ftp://ollama:11434",
            "https://user:pw@models.example.org",
            "https://models.example.org/?key=x",
            "https://models.example.org/#x",
            "https://",
        ],
    )
    def test_a_malformed_url_is_refused(self, url):
        with pytest.raises(ValidationError, match="inference_url must"):
            Settings(environment=Environment.TEST, inference_url=url)

    def test_the_token_is_a_secret(self):
        settings = production_settings(
            inference_url="https://models.example.org", inference_token=SecretStr("tok-123")
        )

        assert "tok-123" not in repr(settings)


class TestUnmaskedHostedInference:
    """ADR 0014: a hosted model gets unmasked text only if operator and owner both chose so."""

    def test_unmasked_text_to_a_hosted_model_is_off_by_default(self):
        assert Settings(environment=Environment.TEST).allow_unmasked_hosted_inference is False

    def test_it_is_refused_unless_hosted_inference_is_allowed_too(self):
        with pytest.raises(ValidationError, match="allow_hosted_inference"):
            Settings(environment=Environment.TEST, allow_unmasked_hosted_inference=True)

    def test_it_is_accepted_with_hosted_inference(self):
        settings = production_settings(
            inference_url="https://api.openai.com/v1",
            allow_hosted_inference=True,
            allow_unmasked_hosted_inference=True,
        )

        assert settings.allow_unmasked_hosted_inference is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://ollama:11434",
            "http://127.0.0.1:11434",
            "http://192.168.1.20:11434",
            "http://100.101.102.103:11434",
        ],
    )
    def test_a_model_server_inside_the_deployment_is_not_hosted(self, url):
        settings = Settings(environment=Environment.TEST, inference_url=url)

        assert settings.is_inference_hosted is False

    @pytest.mark.parametrize(
        "overrides",
        [
            {"inference_url": "https://api.openai.com/v1", "allow_hosted_inference": True},
            {"inference_url": "https://models.example.org/ollama"},
            {"inference_model": "gpt-oss:120b-cloud", "allow_hosted_inference": True},
        ],
        ids=["hosted-api", "outside-the-deployment", "ollama-cloud-model"],
    )
    def test_a_model_outside_the_deployment_or_in_ollamas_cloud_is_hosted(self, overrides):
        assert Settings(environment=Environment.TEST, **overrides).is_inference_hosted is True

    def test_the_demo_model_is_not_hosted_whatever_the_url_says(self):
        settings = Settings(
            environment=Environment.TEST,
            demo_inference=True,
            inference_url="https://models.example.org",
        )

        assert settings.is_inference_hosted is False


class TestPaperSources:
    def test_none_are_enabled_by_default(self):
        assert Settings(environment=Environment.TEST).paper_source_ids == ()

    def test_listed_sources_are_read_in_order(self):
        settings = Settings(environment=Environment.TEST, paper_sources=" arxiv,OpenAlex ")

        assert settings.paper_source_ids == ("arxiv", "openalex")

    @pytest.mark.parametrize("sources", ["scholar", "openalex,scholar", "openalex,openalex"])
    def test_unknown_or_repeated_sources_are_refused(self, sources):
        with pytest.raises(ValidationError, match="paper_sources names"):
            Settings(environment=Environment.TEST, paper_sources=sources)

    def test_an_empty_optional_value_counts_as_unset(self):
        """`NAME=` in an env file must not become an empty key or address."""
        settings = Settings.model_validate(
            {
                "environment": Environment.TEST,
                "openalex_mailto": "",
                "openalex_api_key": SecretStr(""),
                "inference_token": "",
            }
        )

        assert settings.openalex_mailto is None
        assert settings.openalex_api_key is None
        assert settings.inference_token is None

    def test_an_openalex_mailto_must_be_an_address(self):
        with pytest.raises(ValidationError, match="openalex_mailto must"):
            Settings(environment=Environment.TEST, openalex_mailto="not-an-address")


class TestDevelopmentConveniences:
    """Running the whole flow on one machine, and none of it ever reaching production."""

    def test_runs_are_worked_in_the_api_process_by_default_with_in_memory_storage(self):
        assert Settings(environment=Environment.DEVELOPMENT).runs_worker_in_process is True

    def test_runs_wait_for_a_separate_worker_by_default_with_a_database(self):
        settings = Settings(
            environment=Environment.DEVELOPMENT,
            database_url=SecretStr("postgresql+asyncpg://app:pw@localhost/h"),
            key_encryption_keys=SecretStr(f"1:{generate_kek()}"),
        )

        assert settings.runs_worker_in_process is False

    def test_the_in_process_worker_can_be_switched_off(self):
        settings = Settings(environment=Environment.DEVELOPMENT, run_worker_in_process=False)

        assert settings.runs_worker_in_process is False

    def test_production_refuses_the_in_process_worker(self):
        with pytest.raises(ValidationError, match="run_worker_in_process"):
            production_settings(run_worker_in_process=True)

    def test_production_works_runs_in_a_separate_worker(self):
        assert production_settings().runs_worker_in_process is False

    def test_the_demo_model_is_off_unless_asked_for(self):
        assert Settings(environment=Environment.DEVELOPMENT).demo_inference is False

    def test_production_refuses_the_demo_model(self):
        with pytest.raises(ValidationError, match="demo_inference"):
            production_settings(demo_inference=True)

    def test_the_demo_paper_source_is_accepted_outside_production(self):
        settings = Settings(environment=Environment.DEVELOPMENT, paper_sources="demo")

        assert settings.paper_source_ids == ("demo",)

    def test_production_refuses_the_demo_paper_source(self):
        with pytest.raises(ValidationError, match="demo"):
            production_settings(paper_sources="openalex,demo")

    def test_a_development_administrator_needs_both_email_and_password(self):
        with pytest.raises(ValidationError, match="dev_admin"):
            Settings(environment=Environment.DEVELOPMENT, dev_admin_email="me@example.org")

    def test_a_development_administrator_needs_an_address(self):
        with pytest.raises(ValidationError, match="dev_admin_email"):
            Settings(
                environment=Environment.DEVELOPMENT,
                dev_admin_email="not-an-address",
                dev_admin_password=SecretStr("a-long-enough-password-for-dev"),
            )

    def test_a_development_administrator_is_only_for_in_memory_storage(self):
        with pytest.raises(ValidationError, match="dev_admin"):
            Settings(
                environment=Environment.DEVELOPMENT,
                database_url=SecretStr("postgresql+asyncpg://app:pw@localhost/h"),
                key_encryption_keys=SecretStr(f"1:{generate_kek()}"),
                dev_admin_email="me@example.org",
                dev_admin_password=SecretStr("a-long-enough-password-for-dev"),
            )

    def test_production_refuses_a_development_administrator(self):
        with pytest.raises(ValidationError, match="dev_admin"):
            production_settings(
                dev_admin_email="me@example.org",
                dev_admin_password=SecretStr("a-long-enough-password-for-dev"),
            )

    def test_the_development_password_is_a_secret(self):
        settings = Settings(
            environment=Environment.DEVELOPMENT,
            dev_admin_email="me@example.org",
            dev_admin_password=SecretStr("a-long-enough-password-for-dev"),
        )

        assert "a-long-enough-password-for-dev" not in repr(settings)


class TestUnknownSettings:
    """A misspelt name must stop the process, not quietly fall back to a default."""

    def test_a_misspelt_environment_variable_is_refused(self, environment):
        environment.setenv("HUMANI_T_ENVIROMENT", "production")

        with pytest.raises(ValueError, match="HUMANI_T_ENVIROMENT"):
            Settings()

    def test_the_refusal_names_the_variable_but_never_its_value(self, environment):
        environment.setenv("HUMANI_T_DATABSE_URL", _DATABASE_URL)

        with pytest.raises(ValueError, match="HUMANI_T_DATABSE_URL") as refused:
            Settings()

        assert not _shows_the_password(str(refused.value))

    def test_a_misnamed_secret_file_is_refused(self, environment, tmp_path):
        (tmp_path / "humani_t_databse_url").write_text(_DATABASE_URL, encoding="utf-8")

        with pytest.raises(ValueError, match="humani_t_databse_url") as refused:
            _settings_reading_secrets_from(tmp_path)

        assert not _shows_the_password(str(refused.value))

    def test_known_names_and_other_programs_variables_are_accepted(self, environment, tmp_path):
        environment.setenv("HUMANI_T_ENVIRONMENT", "test")
        environment.setenv("HUMANI_TOOLS_HOME", "not ours: no underscore after the prefix")
        (tmp_path / "humani_t_valkey_url").write_text("redis://valkey:6379/0", encoding="utf-8")
        (tmp_path / "db_superuser_password").write_text("not ours either", encoding="utf-8")

        settings = _settings_reading_secrets_from(tmp_path)

        assert settings.environment is Environment.TEST
        assert settings.valkey_url is not None


class TestSecretsNeverAppearInErrors:
    """A refused configuration is printed at startup, into logs that outlive the secret."""

    @pytest.mark.parametrize(
        "variables",
        [
            {},
            {"HUMANI_T_ENVIRONMENT": "production"},
            {"HUMANI_T_INFERENCE_URL": "https://api.openai.com/v1"},
        ],
        ids=["database-without-kek", "production-incomplete", "hosted-inference"],
    )
    def test_a_refused_configuration_does_not_echo_the_database_url(
        self, environment, variables
    ):
        environment.setenv("HUMANI_T_DATABASE_URL", _DATABASE_URL)
        for name, value in variables.items():
            environment.setenv(name, value)

        with pytest.raises(ValidationError) as refused:
            Settings()

        printed = "".join(traceback.format_exception(refused.value))
        assert not _shows_the_password(printed)
        assert not _shows_the_password(repr(refused.value))

    @pytest.mark.parametrize(
        "command",
        [["-m", "app.cli", "purge-expired"], ["-m", "uvicorn", "app.main:app", "--port", "0"]],
        ids=["cli", "uvicorn"],
    )
    def test_startup_output_never_contains_the_database_password(self, command):
        """Uvicorn imports `app.main`, so this also covers the app factory."""
        variables = {
            k: v for k, v in os.environ.items() if not k.upper().startswith("HUMANI_T_")
        }
        variables["HUMANI_T_DATABASE_URL"] = _DATABASE_URL

        result = subprocess.run(  # noqa: S603 — fixed arguments, this interpreter
            [sys.executable, *command],
            cwd=_SERVICE_ROOT,
            env=variables,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "key_encryption_keys" in output
        assert not _shows_the_password(output)


class TestTrustedProxies:
    """Whose X-Forwarded-For is believed is the operator's explicit choice."""

    def test_none_are_trusted_by_default(self):
        assert Settings(environment=Environment.TEST).trusted_proxy_networks == ()

    def test_addresses_and_networks_are_read(self):
        settings = Settings(
            environment=Environment.TEST, trusted_proxies=" 172.30.0.2, 10.8.0.0/16,fd00::2 "
        )

        assert settings.trusted_proxy_networks == (
            ip_network("172.30.0.2/32"),
            ip_network("10.8.0.0/16"),
            ip_network("fd00::2/128"),
        )

    @pytest.mark.parametrize("value", ["*", "caddy", "10.0.0.0/33", "10.0.0.1/8"])
    def test_anything_but_an_address_or_a_network_is_refused(self, value):
        with pytest.raises(ValidationError, match="trusted_proxies"):
            Settings(environment=Environment.TEST, trusted_proxies=value)


def test_env_example_documents_every_setting():
    documented = {
        line.split("=", 1)[0].removeprefix("HUMANI_T_").lower()
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith("HUMANI_T_")
    }

    assert documented == set(Settings.model_fields)
