"""Request and response models for "Your view first": a member's leaning before reading.

The domain checks the option against the brief; this only bounds the size.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.leanings import Leaning


class LeaningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option: str | None = Field(
        max_length=500, description='One of the brief\'s option names, or null for "Not sure".'
    )


class LeaningOut(BaseModel):
    run_id: UUID
    option: str | None = Field(description="null when the member was not sure.")
    answered_at: datetime

    @classmethod
    def from_domain(cls, leaning: Leaning) -> LeaningOut:
        return cls(
            run_id=leaning.run_id, option=leaning.option, answered_at=leaning.answered_at
        )
