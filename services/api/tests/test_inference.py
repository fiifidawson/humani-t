"""The InferenceProvider seam, the Ollama implementation and the prompt guard.

The Ollama responses are shaped from the documented `/api/chat` response
(docs.ollama.com/api/chat), not recorded: no model server was available when
these were written. The live smoke test is where the real shape is confirmed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.application.inference import GuardedInference
from app.domain.inference import (
    ChatMessage,
    ChatRequest,
    ChatRole,
    InferenceUnavailableError,
    InvalidStructuredOutputError,
    PromptRefusedError,
    PromptVerdict,
    TokenUsage,
)
from app.inference.base import InferenceProvider, PromptGuard
from app.inference.fake import FakeInferenceProvider, FakePromptGuard
from app.inference.ollama import OllamaInferenceProvider
from app.inference.schema import validate_output
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

BASE_URL = "http://ollama:11434"
MODEL = "llama3.1:8b"


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


def ollama_reply(
    content: str = '{"include": true, "reason": "Humanitarian chatbot deployment."}',
    *,
    status: int = 200,
    done_reason: str = "stop",
    body: Mapping[str, Any] | None = None,
) -> FakeHttpFetcher:
    payload = body or {
        "model": MODEL,
        "created_at": "2026-11-03T10:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
        "total_duration": 5_000_000_000,
        "load_duration": 1_000_000,
        "prompt_eval_count": 42,
        "prompt_eval_duration": 300_000_000,
        "eval_count": 17,
        "eval_duration": 900_000_000,
    }

    def handler(request: FetchRequest) -> FetchResponse:
        return FetchResponse.of_json(request.url, payload, status=status)

    return FakeHttpFetcher(handler)


def ollama(fetcher: FakeHttpFetcher, base_url: str = BASE_URL) -> OllamaInferenceProvider:
    return OllamaInferenceProvider(fetcher, base_url=base_url, model=MODEL)


def sent_body(fetcher: FakeHttpFetcher) -> dict[str, Any]:
    (request,) = fetcher.requests
    assert request.body is not None
    return json.loads(request.body)


class TestChatRequest:
    def test_a_message_must_have_content(self):
        with pytest.raises(ValueError, match=r"ChatMessage\.content"):
            ChatMessage(ChatRole.USER, "   ")

    def test_a_request_needs_at_least_one_message(self):
        with pytest.raises(ValueError, match=r"ChatRequest\.messages"):
            chat_request(messages=())

    def test_a_request_needs_an_object_schema(self):
        with pytest.raises(ValueError, match=r"ChatRequest\.output_schema"):
            chat_request(output_schema={"type": "string"})

    @pytest.mark.parametrize("temperature", [-0.1, 2.1])
    def test_temperature_stays_in_range(self, temperature):
        with pytest.raises(ValueError, match=r"ChatRequest\.temperature"):
            chat_request(temperature=temperature)

    def test_the_schema_cannot_be_changed_after_the_request_is_built(self):
        schema = {"type": "object", "properties": {}}
        request = chat_request(output_schema=schema)

        schema["properties"] = {"leak": {"type": "string"}}

        assert request.output_schema["properties"] == {}

    def test_token_counts_cannot_be_negative(self):
        with pytest.raises(ValueError, match=r"TokenUsage\.prompt_tokens"):
            TokenUsage(prompt_tokens=-1, completion_tokens=0)

    def test_total_tokens_adds_both_sides(self):
        assert TokenUsage(prompt_tokens=42, completion_tokens=17).total_tokens == 59

    def test_a_refusal_must_say_why(self):
        with pytest.raises(ValueError, match=r"PromptVerdict\.reason"):
            PromptVerdict.refuse("  ")


class TestOutputValidation:
    def test_a_conforming_document_passes(self):
        validate_output({"include": False, "reason": "Off topic."}, SCHEMA)

    @pytest.mark.parametrize(
        "document",
        [
            {"include": "yes", "reason": "x"},
            {"include": True},
            {"include": True, "reason": ""},
            {"include": True, "reason": "x", "extra": 1},
            ["not", "an", "object"],
        ],
    )
    def test_a_document_that_breaks_the_schema_is_refused(self, document):
        with pytest.raises(InvalidStructuredOutputError):
            validate_output(document, SCHEMA)

    def test_an_invalid_schema_is_a_programming_error(self):
        with pytest.raises(ValueError, match="schema"):
            validate_output({}, {"type": "object", "properties": {"x": {"type": 7}}})


class TestFakeInference:
    def test_it_satisfies_the_protocol_without_inheriting_it(self):
        fake = FakeInferenceProvider([{"include": True, "reason": "x"}])

        assert isinstance(fake, InferenceProvider)
        assert FakeInferenceProvider.__mro__[1:] == (object,)

    async def test_it_returns_scripted_outputs_with_provenance(self):
        fake = FakeInferenceProvider([{"include": True, "reason": "x"}], model_id="fake-1")

        result = await fake.chat(chat_request())

        assert result.output == {"include": True, "reason": "x"}
        assert result.model_id == "fake-1"
        assert result.usage.total_tokens > 0
        assert fake.requests == [chat_request()]

    async def test_it_validates_scripted_outputs_against_the_schema_for_real(self):
        fake = FakeInferenceProvider([{"include": "maybe"}])

        with pytest.raises(InvalidStructuredOutputError):
            await fake.chat(chat_request())

    async def test_it_can_compute_an_output_from_the_request(self):
        fake = FakeInferenceProvider(
            lambda request: {"include": True, "reason": request.messages[-1].content}
        )

        result = await fake.chat(chat_request())

        assert result.output["reason"] == "<paper>Chatbots in [LOCATION] camps.</paper>"


class TestOllama:
    def test_it_satisfies_the_protocol_without_inheriting_it(self):
        provider = ollama(FakeHttpFetcher())

        assert isinstance(provider, InferenceProvider)
        assert OllamaInferenceProvider.__mro__[1:] == (object,)
        assert provider.model_id == MODEL

    async def test_it_uses_the_native_chat_endpoint_with_the_schema_as_format(self):
        fetcher = ollama_reply()

        await ollama(fetcher).chat(chat_request(seed=7))

        (request,) = fetcher.requests
        assert request.method == "POST"
        assert request.url == "http://ollama:11434/api/chat"
        assert sent_body(fetcher) == {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "Screen the paper against the criteria."},
                {"role": "user", "content": "<paper>Chatbots in [LOCATION] camps.</paper>"},
            ],
            "format": SCHEMA,
            "stream": False,
            "options": {"temperature": 0.0, "seed": 7},
        }

    async def test_a_base_url_with_a_path_prefix_is_kept(self):
        fetcher = ollama_reply()

        await ollama(fetcher, base_url="https://models.example.org/ollama/").chat(
            chat_request()
        )

        assert fetcher.requests[0].url == "https://models.example.org/ollama/api/chat"

    async def test_the_result_carries_the_output_model_and_token_usage(self):
        result = await ollama(ollama_reply()).chat(chat_request())

        assert result.output == {"include": True, "reason": "Humanitarian chatbot deployment."}
        assert result.model_id == MODEL
        assert result.usage == TokenUsage(prompt_tokens=42, completion_tokens=17)

    @pytest.mark.parametrize(
        "content",
        ["not json at all", '{"include": "yes", "reason": "x"}', '{"include": true}'],
    )
    async def test_output_that_breaks_the_schema_is_refused(self, content):
        with pytest.raises(InvalidStructuredOutputError):
            await ollama(ollama_reply(content)).chat(chat_request())

    async def test_output_cut_off_by_the_length_limit_is_refused(self):
        fetcher = ollama_reply('{"include": true, "reason": "x"}', done_reason="length")

        with pytest.raises(InvalidStructuredOutputError, match="length"):
            await ollama(fetcher).chat(chat_request())

    async def test_a_missing_model_is_reported_as_unavailable_naming_the_model(self):
        fetcher = ollama_reply(status=404, body={"error": f"model '{MODEL}' not found"})

        with pytest.raises(InferenceUnavailableError, match=MODEL):
            await ollama(fetcher).chat(chat_request())

    async def test_a_server_error_is_reported_as_unavailable(self):
        fetcher = ollama_reply(status=500, body={"error": "out of memory"})

        with pytest.raises(InferenceUnavailableError, match="500"):
            await ollama(fetcher).chat(chat_request())

    async def test_a_network_failure_is_reported_as_unavailable(self):
        def unreachable(request: FetchRequest) -> FetchResponse:
            raise FetchFailedError("connection refused")

        with pytest.raises(InferenceUnavailableError) as raised:
            await ollama(FakeHttpFetcher(unreachable)).chat(chat_request())

        assert isinstance(raised.value.__cause__, FetchFailedError)

    async def test_an_invalid_schema_is_refused_before_anything_is_sent(self):
        fetcher = ollama_reply()
        bad = {"type": "object", "properties": {"x": {"type": 7}}}

        with pytest.raises(ValueError, match="schema"):
            await ollama(fetcher).chat(chat_request(output_schema=bad))

        assert fetcher.requests == []


class TestPromptGuard:
    """The last check on what leaves: a refusal stops the call. Fail closed."""

    def test_the_fake_guard_satisfies_the_protocol(self):
        assert isinstance(FakePromptGuard(), PromptGuard)

    async def test_an_allowed_prompt_reaches_the_model_unchanged(self):
        provider = FakeInferenceProvider([{"include": True, "reason": "x"}])
        guard = FakePromptGuard()

        await GuardedInference(provider, guard).chat(chat_request())

        assert guard.inspected == [chat_request()]
        assert provider.requests == [chat_request()]

    async def test_a_refused_prompt_never_reaches_the_model(self):
        fetcher = ollama_reply()
        guard = FakePromptGuard(refusal="2 unmasked personal names")

        with pytest.raises(PromptRefusedError, match="2 unmasked personal names"):
            await GuardedInference(ollama(fetcher), guard).chat(chat_request())

        assert fetcher.requests == []

    async def test_a_guard_that_fails_stops_the_call_too(self):
        fetcher = ollama_reply()
        guard = FakePromptGuard(error=RuntimeError("detector crashed"))

        with pytest.raises(RuntimeError, match="detector crashed"):
            await GuardedInference(ollama(fetcher), guard).chat(chat_request())

        assert fetcher.requests == []

    def test_the_guarded_service_reports_the_models_id_for_the_manifest(self):
        provider = ollama(FakeHttpFetcher())

        assert GuardedInference(provider, FakePromptGuard()).model_id == MODEL
