"""The default InferenceProvider: an Ollama server the operator runs.

Uses Ollama's native `POST /api/chat` with `format` set to the JSON schema and
`stream: false` (docs.ollama.com/api/chat and /capabilities/structured-outputs,
checked 2026-11-03). Chosen over the OpenAI-compatible `/v1/chat/completions`
because:

- structured output with a full JSON schema is documented only for the native
  `format` field; the compatibility page lists `response_format` but never the
  `json_schema` type, and a silently ignored schema would surface as bad output;
- the native response reports the model and token counts (`prompt_eval_count`,
  `eval_count`) that a RunManifest records.

The cost is a wire format only Ollama speaks. Another server (vLLM, llama.cpp)
becomes another implementation of the same Protocol, not a change here.

The base URL and any bearer token are the operator's. The token is attached
by the fetcher's policy for that one host; this class never sees it.
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
