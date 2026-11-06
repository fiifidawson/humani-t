"""A scripted InferenceProvider and PromptGuard. No model, no network.

`FakeInferenceProvider` validates every scripted answer against the request's
schema exactly as a real provider must, so a test that scripts a malformed
answer sees the same `InvalidStructuredOutputError` production would.

`FakePromptGuard` allows everything unless told to refuse. Production runs
the real leak check, `privacy.guard.LeakCheckPromptGuard` (architecture §3.9.1).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.domain.inference import ChatRequest, ChatResult, PromptVerdict, TokenUsage
from app.inference.schema import validate_output

type Script = Iterable[Mapping[str, Any]] | Callable[[ChatRequest], Mapping[str, Any]]


class FakeInferenceProvider:
    """Answers in order from a list, or computes each answer from the request."""

    def __init__(self, outputs: Script, *, model_id: str = "fake-model") -> None:
        self._compute = outputs if callable(outputs) else None
        self._queue = [] if callable(outputs) else list(outputs)
        self._model_id = model_id
        self.requests: list[ChatRequest] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    async def chat(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        output = self._compute(request) if self._compute else self._queue.pop(0)
        validate_output(output, request.output_schema)
        return ChatResult(
            output=output,
            model_id=self._model_id,
            usage=TokenUsage(
                prompt_tokens=sum(len(m.content.split()) for m in request.messages),
                completion_tokens=len(json.dumps(output).split()),
            ),
        )


class FakePromptGuard:
    """Allows every prompt, refuses every prompt with `refusal`, or fails with `error`."""

    def __init__(self, refusal: str | None = None, *, error: Exception | None = None) -> None:
        self._refusal = refusal
        self._error = error
        self.inspected: list[ChatRequest] = []

    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        self.inspected.append(request)
        if self._error is not None:
            raise self._error
        if self._refusal is not None:
            return PromptVerdict.refuse(self._refusal)
        return PromptVerdict.allow()
