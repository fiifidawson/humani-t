"""The OpenAI-compatible InferenceProvider: OpenAI, Azure OpenAI, Mistral, Groq and others.

Requirements: architecture §3.1 (every model call through `InferenceProvider`,
never unchecked output), CLAUDE.md "the one rule" (a hosted API only when the
operator opts in; `test_settings.py` and `test_outbound_wiring.py` hold that
side), and the provider's own wire format (platform.openai.com/docs/api-reference/chat).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.domain.inference import (
    ChatMessage,
    ChatRequest,
    ChatRole,
    InferenceUnavailableError,
    InvalidStructuredOutputError,
    TokenUsage,
)
from app.inference.base import InferenceProvider
from app.inference.openai_compatible import OpenAICompatibleInferenceProvider
from app.net.base import FetchFailedError, FetchRequest, FetchResponse
from app.net.fake import FakeHttpFetcher

SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "include": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 1},
    },
    "required": ["include", "reason"],
    "additionalProperties": False,
}

BASE_URL = "https://api.openai.com/v1"
MODEL = "gpt-5-mini"
ANSWER = '{"include": true, "reason": "Humanitarian chatbot deployment."}'


def chat_request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "messages": (
            ChatMessage(ChatRole.SYSTEM, "Screen the paper against the criteria."),
            ChatMessage(ChatRole.USER, "<paper>Chatbots in [LOCATION] camps.</paper>"),
        ),
        "output_schema": SCHEMA,
    }
    values.update(overrides)
    return ChatRequest(**values)


def completion(
    content: str | None = ANSWER, finish_reason: str = "stop", refusal: str | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": f"{MODEL}-2026-08-07",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 17, "total_tokens": 59},
    }


def reply(
    content: str | None = ANSWER,
    *,
    status: int = 200,
    finish_reason: str = "stop",
    refusal: str | None = None,
    body: Mapping[str, Any] | None = None,
) -> FakeHttpFetcher:
    payload = body or completion(content, finish_reason, refusal)

    def handler(request: FetchRequest) -> FetchResponse:
        return FetchResponse.of_json(request.url, payload, status=status)

    return FakeHttpFetcher(handler)


def provider(
    fetcher: FakeHttpFetcher, base_url: str = BASE_URL
) -> OpenAICompatibleInferenceProvider:
    return OpenAICompatibleInferenceProvider(fetcher, base_url=base_url, model=MODEL)


def sent_body(fetcher: FakeHttpFetcher) -> dict[str, Any]:
    (request,) = fetcher.requests
    assert request.body is not None
    return json.loads(request.body)


class TestRequest:
    def test_it_satisfies_the_protocol_without_inheriting_it(self):
        assert isinstance(provider(reply()), InferenceProvider)
        assert OpenAICompatibleInferenceProvider.__mro__[1:] == (object,)

    async def test_it_posts_chat_completions_with_the_schema_as_the_response_format(self):
        fetcher = reply()

        await provider(fetcher).chat(chat_request(seed=7))

        (request,) = fetcher.requests
        assert request.method == "POST"
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert sent_body(fetcher) == {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "Screen the paper against the criteria."},
                {"role": "user", "content": "<paper>Chatbots in [LOCATION] camps.</paper>"},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    # minLength is dropped: strict mode refuses it. It is still
                    # checked on the answer (TestStrictMode).
                    "schema": {
                        "type": "object",
                        "properties": {
                            "include": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["include", "reason"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            "temperature": 0.0,
            "seed": 7,
        }

    async def test_no_seed_is_sent_when_none_is_asked_for(self):
        fetcher = reply()

        await provider(fetcher).chat(chat_request())

        assert "seed" not in sent_body(fetcher)

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://my-resource.openai.azure.com/openai/v1",
            "https://my-resource.openai.azure.com/openai/v1/",
        ],
    )
    async def test_the_base_url_keeps_its_path_with_or_without_a_trailing_slash(self, base_url):
        fetcher = reply()

        await provider(fetcher, base_url).chat(chat_request())

        assert fetcher.requests[0].url == (
            "https://my-resource.openai.azure.com/openai/v1/chat/completions"
        )


class TestStrictMode:
    """Strict structured outputs make the model fill every required field.

    OpenAI accepts strict mode only when every object lists all its properties
    as required and forbids others, and only some keywords (checked 2026-11-08).
    """

    async def test_a_schema_with_an_optional_property_is_sent_without_strict_mode(self):
        optional = {**SCHEMA, "required": ["include"]}
        fetcher = reply()

        await provider(fetcher).chat(chat_request(output_schema=optional))

        sent = sent_body(fetcher)["response_format"]["json_schema"]
        assert sent["strict"] is False
        assert sent["schema"] == optional

    async def test_strict_mode_drops_keywords_it_refuses_at_every_depth(self):
        nested = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "uniqueItems": True,
                    "minItems": 2,
                    "items": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": 9},
                            {"type": "null"},
                        ]
                    },
                },
            },
            "required": ["tags"],
            "additionalProperties": False,
        }
        fetcher = reply('{"tags": ["a", "b"]}')

        await provider(fetcher).chat(chat_request(output_schema=nested))

        sent = sent_body(fetcher)["response_format"]["json_schema"]
        assert sent["strict"] is True
        assert sent["schema"]["properties"]["tags"] == {
            "type": "array",
            "minItems": 2,
            "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        }

    async def test_a_dropped_keyword_is_still_enforced_on_the_answer(self):
        fetcher = reply('{"include": true, "reason": ""}')

        with pytest.raises(InvalidStructuredOutputError):
            await provider(fetcher).chat(chat_request())


class TestAnswer:
    async def test_the_result_carries_the_output_the_serving_model_and_token_usage(self):
        result = await provider(reply()).chat(chat_request())

        assert result.output == {"include": True, "reason": "Humanitarian chatbot deployment."}
        assert result.model_id == f"{MODEL}-2026-08-07"
        assert result.usage == TokenUsage(prompt_tokens=42, completion_tokens=17)

    @pytest.mark.parametrize(
        "content",
        ["not json at all", '{"include": "yes", "reason": "x"}', '{"include": true}', None],
    )
    async def test_output_that_breaks_the_schema_is_refused(self, content):
        with pytest.raises(InvalidStructuredOutputError):
            await provider(reply(content)).chat(chat_request())

    async def test_output_cut_off_by_the_length_limit_is_refused(self):
        fetcher = reply('{"include": true, "reason": "x"}', finish_reason="length")

        with pytest.raises(InvalidStructuredOutputError, match="length"):
            await provider(fetcher).chat(chat_request())

    async def test_a_refusal_by_the_model_is_refused_as_output(self):
        fetcher = reply(None, refusal="I can't help with that.")

        with pytest.raises(InvalidStructuredOutputError, match="refused"):
            await provider(fetcher).chat(chat_request())


def refusing(param: str) -> FakeHttpFetcher:
    """A server that, like OpenAI's reasoning models, refuses one sampling setting."""

    def handler(request: FetchRequest) -> FetchResponse:
        assert request.body is not None
        if param in json.loads(request.body):
            error = {
                "message": f"Unsupported value: '{param}' does not support 0.0.",
                "type": "invalid_request_error",
                "param": param,
                "code": "unsupported_value",
            }
            return FetchResponse.of_json(request.url, {"error": error}, status=400)
        return FetchResponse.of_json(request.url, completion())

    return FakeHttpFetcher(handler)


class TestUnsupportedSettings:
    """GPT-5 and o-series models take only their default temperature (checked 2026-11-08)."""

    @pytest.mark.parametrize("param", ["temperature", "seed"])
    async def test_a_refused_setting_is_dropped_and_the_question_asked_again(self, param):
        fetcher = refusing(param)

        result = await provider(fetcher).chat(chat_request(seed=7))

        assert result.output["include"] is True
        first, second = (json.loads(r.body or "{}") for r in fetcher.requests)
        assert param in first
        assert param not in second

    async def test_the_model_is_not_asked_with_the_refused_setting_again(self):
        fetcher = refusing("temperature")
        model = provider(fetcher)

        await model.chat(chat_request())
        await model.chat(chat_request())

        assert len(fetcher.requests) == 3
        assert "temperature" not in json.loads(fetcher.requests[2].body or "{}")

    async def test_a_bad_request_about_anything_else_is_reported_as_unavailable(self):
        error = {"param": "messages", "code": "invalid_value", "message": "bad"}
        fetcher = reply(status=400, body={"error": error})

        with pytest.raises(InferenceUnavailableError, match="400"):
            await provider(fetcher).chat(chat_request())

        assert len(fetcher.requests) == 1


class TestFailures:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_api_key_is_reported_as_unavailable(self, status):
        fetcher = reply(status=status, body={"error": {"message": "Incorrect API key"}})

        with pytest.raises(InferenceUnavailableError, match="API key"):
            await provider(fetcher).chat(chat_request())

    async def test_a_missing_model_is_reported_as_unavailable_naming_the_model(self):
        fetcher = reply(status=404, body={"error": {"message": "model not found"}})

        with pytest.raises(InferenceUnavailableError, match=MODEL):
            await provider(fetcher).chat(chat_request())

    async def test_a_rate_limit_is_reported_as_unavailable(self):
        fetcher = reply(status=429, body={"error": {"message": "Rate limit reached"}})

        with pytest.raises(InferenceUnavailableError, match="rate limit"):
            await provider(fetcher).chat(chat_request())

    async def test_a_server_error_is_reported_as_unavailable(self):
        fetcher = reply(status=500, body={"error": {"message": "overloaded"}})

        with pytest.raises(InferenceUnavailableError, match="500"):
            await provider(fetcher).chat(chat_request())

    async def test_a_network_failure_is_reported_as_unavailable(self):
        def unreachable(request: FetchRequest) -> FetchResponse:
            raise FetchFailedError("connection refused")

        with pytest.raises(InferenceUnavailableError) as raised:
            await provider(FakeHttpFetcher(unreachable)).chat(chat_request())

        assert isinstance(raised.value.__cause__, FetchFailedError)

    async def test_an_invalid_schema_is_refused_before_anything_is_sent(self):
        fetcher = reply()
        bad = {"type": "object", "properties": {"x": {"type": 7}}}

        with pytest.raises(ValueError, match="schema"):
            await provider(fetcher).chat(chat_request(output_schema=bad))

        assert fetcher.requests == []
