"""The InferenceProvider and PromptGuard Protocols.

`InferenceProvider` turns a `ChatRequest` into a schema-valid `ChatResult`.
`PromptGuard` decides whether a request's text may leave at all. They are
separate seams so the guard cannot be skipped by choosing a different model
server: `application.inference.GuardedInference` puts one in front of the other.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.inference import ChatRequest, ChatResult, PromptVerdict


@runtime_checkable
class InferenceProvider(Protocol):
    @property
    def model_id(self) -> str:
        """The configured model, as recorded in a RunManifest before any call is made."""
        ...

    async def chat(self, request: ChatRequest) -> ChatResult:
        """Ask the model and return an answer that matches `request.output_schema`.

        Raises `InvalidStructuredOutputError` when the answer does not match,
        and `InferenceUnavailableError` when there is no answer at all. It never
        returns unchecked output.
        """
        ...


@runtime_checkable
class PromptGuard(Protocol):
    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        """Check the exact outgoing text. Any exception counts as a refusal."""
        ...
