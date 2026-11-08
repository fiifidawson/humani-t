"""An InferenceProvider for servers that speak OpenAI's Chat Completions API.

That covers OpenAI, Azure OpenAI (its `/openai/v1` endpoint), Mistral, Groq,
OpenRouter, and self-hosted vLLM or llama.cpp servers. A hosted one receives
text only when the operator sets `allow_hosted_inference`; Settings refuses it
otherwise. This class knows nothing of that decision, or of the API key, which
the fetcher attaches for the one configured host.

`POST {base}/chat/completions` with `response_format` of type `json_schema`
(platform.openai.com/docs/api-reference/chat, checked 2026-11-08).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.domain.inference import (
    ChatRequest,
    ChatResult,
    InferenceUnavailableError,
    InvalidStructuredOutputError,
    TokenUsage,
)
from app.inference.schema import check_schema, validate_output
from app.net.base import FetchRequest, FetchResponse, HttpFetcher, OutboundError


class OpenAICompatibleInferenceProvider:
    def __init__(self, fetcher: HttpFetcher, *, base_url: str, model: str) -> None:
        self._fetcher = fetcher
        self._chat_url = base_url.rstrip("/") + "/chat/completions"
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    async def chat(self, request: ChatRequest) -> ChatResult:
        check_schema(request.output_schema)
        try:
            response = await self._fetcher.fetch(
                FetchRequest.post_json(self._chat_url, self._body(request))
            )
        except OutboundError as exc:
            raise InferenceUnavailableError(
                f"the model server could not be reached: {type(exc).__name__}"
            ) from exc
        payload = self._payload(response)
        choice = _first_choice(payload)
        if choice.get("finish_reason") == "length":
            raise InvalidStructuredOutputError("the answer was cut off by the length limit")
        output = _decode_answer(choice)
        validate_output(output, request.output_schema)
        usage = payload.get("usage")
        counts = usage if isinstance(usage, dict) else {}
        return ChatResult(
            output=output,
            model_id=str(payload.get("model") or self._model),
            usage=TokenUsage(
                prompt_tokens=_count(counts.get("prompt_tokens")),
                completion_tokens=_count(counts.get("completion_tokens")),
            ),
        )

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in request.messages
            ],
            # Not strict: strict mode rejects schemas with optional properties or
            # `minLength`, which ours use. The answer is validated below either way.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "schema": dict(request.output_schema),
                    "strict": False,
                },
            },
            "temperature": request.temperature,
        }
        if request.seed is not None:
            body["seed"] = request.seed
        return body

    def _payload(self, response: FetchResponse) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        # The provider's error text is not repeated: it can echo the request.
        if response.status in (401, 403):
            raise InferenceUnavailableError("the model server rejected the API key")
        if response.status == 404:
            raise InferenceUnavailableError(f"the model server does not have {self._model!r}")
        if response.status == 429:
            raise InferenceUnavailableError("the model server's rate limit was reached")
        if not response.is_success:
            raise InferenceUnavailableError(f"the model server answered HTTP {response.status}")
        if not isinstance(payload, dict):
            raise InferenceUnavailableError("the model server's answer is not a JSON object")
        return payload


def _first_choice(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(first, dict):
        raise InvalidStructuredOutputError("the answer has no choices")
    return first


def _decode_answer(choice: Mapping[str, Any]) -> Mapping[str, Any]:
    message = choice.get("message")
    if not isinstance(message, dict):
        raise InvalidStructuredOutputError("the answer has no message")
    if message.get("refusal"):
        raise InvalidStructuredOutputError("the model refused to answer")
    content = message.get("content")
    if not isinstance(content, str):
        raise InvalidStructuredOutputError("the answer has no message content")
    try:
        decoded = json.loads(content)
    except ValueError as exc:
        raise InvalidStructuredOutputError("the answer is not JSON") from exc
    if not isinstance(decoded, dict):
        raise InvalidStructuredOutputError("the answer is not a JSON object")
    return decoded


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
