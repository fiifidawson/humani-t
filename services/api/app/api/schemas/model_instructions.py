"""Request and response models for a project's instructions to the model.

`ModelInstructionsIn` checks shape and is turned into the domain's `ModelInstructions` by
`to_domain()`, which checks meaning. Responses are built from application views.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.runs import UserRefOut
from app.application.model_instructions import InstructionsHistory, SavedInstructions
from app.domain.model_instructions import ModelInstructions

# Generous on purpose: the domain trims and counts, and names the field it refuses.
_MAX_RAW_LENGTH = 10_000


class ModelInstructionsIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "screening": "Treat studies in refugee camps as displaced settings.",
                "writing": "Write for a field manager: short sentences, no jargon.",
                "based_on_version": None,
            }
        },
    )

    screening: str = Field(
        max_length=_MAX_RAW_LENGTH, description="How to judge papers; blank for none."
    )
    writing: str = Field(
        max_length=_MAX_RAW_LENGTH,
        description="How to write briefs and answers; blank for none.",
    )
    based_on_version: int | None = Field(
        ge=1, description="The version being edited; null if the project has none yet."
    )

    def to_domain(self) -> ModelInstructions:
        return ModelInstructions(screening=self.screening, writing=self.writing)


class ModelInstructionsVersionOut(BaseModel):
    version: int
    screening: str = Field(description="As typed; masked only when sent to a model.")
    writing: str = Field(description="As typed; masked only when sent to a model.")
    saved_by: UserRefOut
    saved_at: datetime

    @classmethod
    def from_view(cls, saved: SavedInstructions) -> ModelInstructionsVersionOut:
        version = saved.version
        return cls(
            version=version.version,
            screening=version.instructions.screening,
            writing=version.instructions.writing,
            saved_by=UserRefOut(id=version.created_by, display_name=saved.saved_by_name),
            saved_at=version.created_at,
        )


class ModelInstructionsOut(BaseModel):
    current: ModelInstructionsVersionOut | None = Field(
        description="The instructions in force; null means none were ever saved."
    )
    versions: list[ModelInstructionsVersionOut] = Field(
        description="Every version, oldest first."
    )

    @classmethod
    def from_view(cls, history: InstructionsHistory) -> ModelInstructionsOut:
        current = history.current
        return cls(
            current=None if current is None else ModelInstructionsVersionOut.from_view(current),
            versions=[ModelInstructionsVersionOut.from_view(v) for v in history.versions],
        )
