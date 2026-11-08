"""How configuration becomes outbound destinations: the operator decides, nothing else.

These pin the one rule at the composition root. A credential belongs to exactly
one host, only the inference server the operator named may sit on a private
network, and no paper source is reached unless the operator listed it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from app.api.dependencies import (
    build_providers,
    inference_destination,
    inference_provider,
    paper_source_destination,
)
from app.inference.ollama import OllamaInferenceProvider
from app.inference.openai_compatible import OpenAICompatibleInferenceProvider
from app.net.base import Destination
from app.net.fake import FakeHttpFetcher
from app.settings import Environment, Settings


def settings(**overrides: Any) -> Settings:
    return Settings.model_validate({"environment": Environment.TEST, **overrides})


class TestPaperSources:
    async def test_no_paper_source_is_reached_unless_the_operator_lists_it(self):
        providers = build_providers(settings())

        assert providers.paper_sources == ()
        await providers.close()

    async def test_listed_sources_are_built_in_the_order_given(self):
        providers = build_providers(settings(paper_sources="arxiv, openalex"))

        assert [p.source_id for p in providers.paper_sources] == ["arxiv", "openalex"]
        await providers.close()

    @pytest.mark.parametrize(
        ("source_id", "host"),
        [
            ("openalex", "api.openalex.org"),
            ("europepmc", "www.ebi.ac.uk"),
            ("arxiv", "export.arxiv.org"),
        ],
    )
    def test_each_source_may_reach_only_its_own_public_https_host(self, source_id, host):
        assert paper_source_destination(source_id, settings()) == Destination(host)

    def test_the_openalex_key_travels_only_to_openalex(self):
        configured = settings(openalex_api_key=SecretStr("oa-key"))

        assert paper_source_destination("openalex", configured).bearer_token == "oa-key"
        assert paper_source_destination("europepmc", configured).bearer_token is None
        assert paper_source_destination("arxiv", configured).bearer_token is None


class TestInference:
    def test_the_default_is_the_compose_model_server_on_the_internal_network(self):
        destination = inference_destination(settings())

        assert destination == Destination(
            "ollama", allows_private_addresses=True, allows_plain_http=True
        )

    def test_a_remote_server_is_reached_over_https_with_the_operators_token(self):
        destination = inference_destination(
            settings(
                inference_url="https://models.example.org/ollama",
                inference_token=SecretStr("tok-123"),
            )
        )

        assert destination.host == "models.example.org"
        assert destination.allows_plain_http is False
        assert destination.bearer_token == "tok-123"
        # Operator-named, so its address is not second-guessed: a VPS on a
        # private VPN address is a legitimate choice.
        assert destination.allows_private_addresses is True

    def test_ollama_is_spoken_unless_the_operator_chooses_another_format(self):
        chosen = inference_provider(settings(), FakeHttpFetcher())

        assert isinstance(chosen, OllamaInferenceProvider)

    def test_the_openai_format_uses_the_openai_compatible_provider_and_model(self):
        chosen = inference_provider(
            settings(
                inference_api="openai",
                inference_url="https://api.openai.com/v1",
                inference_model="gpt-5-mini",
                allow_hosted_inference=True,
            ),
            FakeHttpFetcher(),
        )

        assert isinstance(chosen, OpenAICompatibleInferenceProvider)
        assert chosen.model_id == "gpt-5-mini"

    def test_a_hosted_api_key_travels_only_to_that_api(self):
        destination = inference_destination(
            settings(
                inference_api="openai",
                inference_url="https://api.openai.com/v1",
                inference_token=SecretStr("sk-test"),
                allow_hosted_inference=True,
            )
        )

        assert destination.host == "api.openai.com"
        assert destination.bearer_token == "sk-test"
        assert destination.allows_plain_http is False

    async def test_the_model_call_is_guarded_and_names_the_configured_model(self):
        providers = build_providers(settings(inference_model="qwen3:8b"))

        assert providers.inference.model_id == "qwen3:8b"
        await providers.close()
