"""A request to a language model and what comes back, independent of any model server.

Every call asks for structured output: a JSON document that must match a
schema. Free text from a model is not something the rest of the system can
check, cite or store as a decision, so it is not offered.

The result carries the model id and token usage because a `RunManifest` must
be able to say exactly which model produced a decision, and at what cost.
Wire formats (Ollama's, OpenAI's) never appear here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("ChatMessage.content must not be blank")


def _frozen_json(value: Mapping[str, Any]) -> Mapping[str, Any]:
    # A JSON round trip both proves the value serialises and cuts every link to
    # the caller's dict, so changing it afterwards cannot change this request.
    copied: dict[str, Any] = json.loads(json.dumps(value))
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Messages plus the JSON schema the answer must match.

    Every text in `messages` must already be masked. The `PromptGuard` checks it
    again immediately before the call, and refuses rather than repairs.
    """

    messages: tuple[ChatMessage, ...]
    output_schema: Mapping[str, Any]
    temperature: float = 0.0
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("ChatRequest.messages must not be empty")
        if self.output_schema.get("type") != "object":
            raise ValueError(
                'ChatRequest.output_schema must describe an object ("type": "object")'
            )
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("ChatRequest.temperature must be between 0 and 2")
        object.__setattr__(self, "output_schema", _frozen_json(self.output_schema))


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0:
            raise ValueError("TokenUsage.prompt_tokens must not be negative")
        if self.completion_tokens < 0:
            raise ValueError("TokenUsage.completion_tokens must not be negative")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class ChatResult:
    """A schema-valid answer and its provenance: which model, and how many tokens."""

    output: Mapping[str, Any]
    model_id: str
    usage: TokenUsage

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("ChatResult.model_id must not be blank")


@dataclass(frozen=True, slots=True)
class PromptVerdict:
    """Whether outgoing text may leave. A refusal says why in counts and types, never values."""

    is_allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> Self:
        return cls(is_allowed=True)

    @classmethod
    def refuse(cls, reason: str) -> Self:
        if not reason.strip():
            raise ValueError("PromptVerdict.reason must say why the prompt was refused")
        return cls(is_allowed=False, reason=reason)


class InferenceError(Exception):
    """A model call did not produce a usable answer."""


class InferenceUnavailableError(InferenceError):
    """The model server could not be reached, refused, or does not have the model."""


class InferenceRateLimitedError(InferenceUnavailableError):
    """The model service refused for now: too many requests. Waiting can fix it."""


class InvalidStructuredOutputError(InferenceError):
    """The model answered, but not with a document matching the schema."""


class PromptRefusedError(InferenceError):
    """The prompt guard stopped the call: the outgoing text failed the leak check."""
