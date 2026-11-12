"""The InferenceProvider and PromptGuard Protocols.

They are separate seams so choosing another model server cannot skip the guard:
`GuardedInference` puts the guard in front of every provider.
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

        Raise `InvalidStructuredOutputError` for a mismatch, `InferenceUnavailableError`
        for no answer; never return unchecked output.
        """
        ...


@runtime_checkable
class PromptGuard(Protocol):
    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        """Check the exact outgoing text. Any exception counts as a refusal."""
        ...
