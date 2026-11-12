"""The default InferenceProvider: an Ollama server the operator runs.

Native `POST /api/chat` with `format` set to the JSON schema (docs.ollama.com, checked
2026-11-03), not the OpenAI-compatible endpoint: only the native one documents full
JSON-schema output. The fetcher attaches any bearer token; this class never sees it.
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


class OllamaInferenceProvider:
    def __init__(self, fetcher: HttpFetcher, *, base_url: str, model: str) -> None:
        self._fetcher = fetcher
        self._chat_url = base_url.rstrip("/") + "/api/chat"
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
        if payload.get("done_reason") == "length":
            raise InvalidStructuredOutputError("the answer was cut off by the length limit")
        output = _decode_answer(payload)
        validate_output(output, request.output_schema)
        return ChatResult(
            output=output,
            model_id=str(payload.get("model") or self._model),
            usage=TokenUsage(
                prompt_tokens=_count(payload.get("prompt_eval_count")),
                completion_tokens=_count(payload.get("eval_count")),
            ),
        )

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": request.temperature}
        if request.seed is not None:
            options["seed"] = request.seed
        return {
            "model": self._model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in request.messages
            ],
            "format": dict(request.output_schema),
            "stream": False,
            # A reasoning model (qwen3.5, deepseek-r1) otherwise thinks before every
            # answer: 28 times the tokens on a CPU, all discarded, since only the
            # schema-checked answer is kept. Models that cannot think ignore it.
            "think": False,
            "options": options,
        }

    def _payload(self, response: FetchResponse) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status == 404:
            raise InferenceUnavailableError(f"the model server does not have {self._model!r}")
        if not response.is_success:
            raise InferenceUnavailableError(f"the model server answered HTTP {response.status}")
        if not isinstance(payload, dict):
            raise InferenceUnavailableError("the model server's answer is not a JSON object")
        return payload


def _decode_answer(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
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
