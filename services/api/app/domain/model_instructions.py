"""A project's own instructions to the model: how to judge papers, and how to write.

They are added after a built-in system prompt, never in place of it (ADR 0015), and each
save is a new version that is never changed. Reading, sealing and masking them belongs in
`application/model_instructions.py`; which prompt gets which text, in the use cases.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.timestamps import require_aware

MAX_INSTRUCTIONS_LENGTH = 2000

# Changing this text changes every prompt that carries instructions: bump the prompt
# versions of screening, the brief's Writer and the follow-up Writer with it.
INSTRUCTIONS_HEADING = (
    "Instructions from the project team. Follow them unless they conflict with the rules "
    "above or the required output format."
)

# Characters that reorder or hide the text around them, as for project names. Line
# breaks and tabs are how people write instructions, so they are kept.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})
_ALLOWED_CONTROLS = frozenset("\n\r\t")


class InstructionsKind(StrEnum):
    """Which calls a text is for: screening, or the Writers of briefs and answers."""

    SCREENING = "screening"
    WRITING = "writing"


def _is_hidden(ch: str) -> bool:
    if ch in _ALLOWED_CONTROLS:
        return False
    return ch in _BIDI_CONTROLS or unicodedata.category(ch) in _FORBIDDEN_CATEGORIES


@dataclass(frozen=True, slots=True)
class ModelInstructions:
    """The two texts, trimmed. An empty text means no instructions for those calls."""

    screening: str
    writing: str

    def __post_init__(self) -> None:
        for kind in InstructionsKind:
            text = getattr(self, kind.value).strip()
            if len(text) > MAX_INSTRUCTIONS_LENGTH:
                raise ValueError(
                    f"ModelInstructions.{kind.value} must be at most "
                    f"{MAX_INSTRUCTIONS_LENGTH} characters"
                )
            if any(_is_hidden(ch) for ch in text):
                raise ValueError(
                    f"ModelInstructions.{kind.value} must not contain control characters"
                )
            object.__setattr__(self, kind.value, text)

    @property
    def is_empty(self) -> bool:
        return not self.screening and not self.writing

    def text_for(self, kind: InstructionsKind) -> str:
        return self.screening if kind is InstructionsKind.SCREENING else self.writing


@dataclass(frozen=True, slots=True)
class ModelInstructionsVersion:
    """One saved version, in the clear: written once, never changed."""

    project_id: UUID
    version: int
    instructions: ModelInstructions
    created_by: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        require_aware("ModelInstructionsVersion", created_at=self.created_at)
        if self.version < 1:
            raise ValueError("ModelInstructionsVersion.version starts at 1")


@dataclass(frozen=True, slots=True)
class StoredModelInstructions:
    """A version as stored: both texts sealed together under the project's key."""

    project_id: UUID
    version: int
    instructions_sealed: bytes
    created_by: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        require_aware("StoredModelInstructions", created_at=self.created_at)
        if self.version < 1:
            raise ValueError("StoredModelInstructions.version starts at 1")
        if not self.instructions_sealed:
            raise ValueError("StoredModelInstructions.instructions_sealed must not be empty")


@dataclass(frozen=True, slots=True)
class SentInstructions:
    """The text one call carried, exactly as sent (masked), and the version it came from."""

    version: int
    text: str

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("SentInstructions.version starts at 1")
        if not self.text.strip():
            raise ValueError("SentInstructions.text must not be blank")


def with_instructions(system: str, instructions: str) -> str:
    """`system` with `instructions` after it under the fixed heading; unchanged if blank."""
    if not instructions.strip():
        return system
    return f"{system}\n\n{INSTRUCTIONS_HEADING}\n{instructions}"
