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


def reply(
    content: str | None = ANSWER,
    *,
    status: int = 200,
    finish_reason: str = "stop",
    refusal: str | None = None,
    body: Mapping[str, Any] | None = None,
) -> FakeHttpFetcher:
    payload = body or {
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
                "json_schema": {"name": "answer", "schema": dict(SCHEMA), "strict": False},
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
