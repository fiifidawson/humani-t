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
        self._refused_settings: set[str] = set()

    @property
    def model_id(self) -> str:
        return self._model

    async def chat(self, request: ChatRequest) -> ChatResult:
        check_schema(request.output_schema)
        response = await self._post(request)
        while (refused := _refused_setting(response)) and refused not in self._refused_settings:
            self._refused_settings.add(refused)
            response = await self._post(request)
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

    async def _post(self, request: ChatRequest) -> FetchResponse:
        try:
            return await self._fetcher.fetch(
                FetchRequest.post_json(self._chat_url, self._body(request))
            )
        except OutboundError as exc:
            raise InferenceUnavailableError(
                f"the model server could not be reached: {type(exc).__name__}"
            ) from exc

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in request.messages
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": _response_schema(request),
            },
            "temperature": request.temperature,
        }
        if request.seed is not None:
            body["seed"] = request.seed
        for setting in self._refused_settings:
            body.pop(setting, None)
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


# Sampling settings a model may refuse: OpenAI's reasoning models (GPT-5, o-series)
# take only their default temperature. Such a setting is left out from then on;
# the answer is less repeatable, and the RunManifest still names the model.
_DROPPABLE_SETTINGS = frozenset({"temperature", "seed"})


def _refused_setting(response: FetchResponse) -> str | None:
    """The sampling setting a 400 answer names as unsupported, if that is all it says."""
    if response.status != 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    param = error.get("param")
    if param in _DROPPABLE_SETTINGS and error.get("code") in (
        "unsupported_value",
        "unsupported_parameter",
    ):
        return str(param)
    return None


# Keywords strict structured outputs refuse (platform.openai.com/docs/guides/
# structured-outputs, "Supported schemas", checked 2026-11-08). They are left out
# of what is sent and still enforced, since every answer is validated against the
# full schema.
_NOT_IN_STRICT = frozenset(
    {
        "minLength",
        "maxLength",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "patternProperties",
        "contains",
        "minContains",
        "maxContains",
    }
)
# Keywords whose meaning strict mode cannot express at all: such a schema is sent
# without strict mode rather than changed into a different one.
_BLOCKS_STRICT = frozenset({"oneOf", "allOf", "not", "if", "then", "else"})


def _response_schema(request: ChatRequest) -> dict[str, Any]:
    """Strict when the schema allows it, so the model cannot skip a required field."""
    schema = dict(request.output_schema)
    strict = _strict_copy(schema)
    if strict is None:
        return {"name": "answer", "schema": schema, "strict": False}
    return {"name": "answer", "schema": strict, "strict": True}


def _strict_copy(node: Any) -> Any:
    """`node` without the keywords strict mode refuses, or None if it cannot be strict.

    Strict mode also needs every object to list all its properties as required
    and to forbid any others.
    """
    if isinstance(node, list):
        items = [_strict_copy(item) for item in node]
        return None if any(item is None for item in items) else items
    if not isinstance(node, Mapping):
        return node
    if _BLOCKS_STRICT & node.keys():
        return None
    if node.get("type") == "object" or "properties" in node:
        properties = node.get("properties", {})
        if node.get("additionalProperties") is not False or set(
            node.get("required", ())
        ) != set(properties):
            return None
    copy: dict[str, Any] = {}
    for key, value in node.items():
        if key in _NOT_IN_STRICT:
            continue
        if key in ("properties", "$defs"):
            children = {name: _strict_copy(child) for name, child in value.items()}
            if any(child is None for child in children.values()):
                return None
            copy[key] = children
        elif key in ("items", "anyOf"):
            child = _strict_copy(value)
            if child is None:
                return None
            copy[key] = child
        else:
            copy[key] = value
    return copy


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
