"""Leanings: which option a member leaned towards before reading a brief (UI_UX_SPEC §6.9).

Asked only where the project turned on "Your view first". One per member and run, written
once, and seen by that member alone. Who may answer and when is decided in
`application/leanings.py`; the sealing of the option is not this module's business.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.timestamps import require_aware


@dataclass(frozen=True, slots=True)
class Leaning:
    """A member's answer in the clear. `option` is `None` for "Not sure"."""

    project_id: UUID
    run_id: UUID
    user_id: UUID
    option: str | None
    answered_at: datetime

    def __post_init__(self) -> None:
        require_aware("Leaning", answered_at=self.answered_at)
        if self.option is not None and not self.option.strip():
            raise ValueError("Leaning.option must not be blank")


@dataclass(frozen=True, slots=True)
class StoredLeaning:
    """A leaning as stored: the option sealed under the project's key."""

    project_id: UUID
    run_id: UUID
    user_id: UUID
    option_sealed: bytes
    answered_at: datetime

    def __post_init__(self) -> None:
        require_aware("StoredLeaning", answered_at=self.answered_at)


class LeaningRefusedError(Exception):
    """The answer cannot be taken now: already given, or the project does not ask."""


def require_offered(option: str | None, offered: Sequence[str]) -> None:
    """Refuse an option the brief does not offer; `None` ("Not sure") is always allowed."""
    if option is not None and option not in offered:
        raise ValueError("Leaning.option must be one of the brief's options")
