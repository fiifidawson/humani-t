"""Request and response models for a review's label rules.

`LabelRulesIn` checks shape and is turned into the domain's `LabelRules` by `to_domain()`,
which checks meaning. Responses are built from application views.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.label_rules import AppliedRules, RulesHistory, RulesPreview
from app.domain.label_rules import LabelRules, LabelRulesVersion, UnclearCountsAs
from app.domain.screening import ScreeningTier


class LabelRulesIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"possibly_min_met": 1, "unclear_counts_as": "not_met"}},
    )

    possibly_min_met: int = Field(
        ge=1, le=100, description="Inclusion criteria met for Possibly relevant."
    )
    unclear_counts_as: UnclearCountsAs = Field(
        description="What an unclear inclusion answer counts as; uncertain asks a person."
    )

    def to_domain(self) -> LabelRules:
        return LabelRules(self.possibly_min_met, self.unclear_counts_as)


class LabelRulesVersionOut(BaseModel):
    version: int
    possibly_min_met: int
    unclear_counts_as: UnclearCountsAs
    changed_by: UUID
    changed_at: datetime
    papers_moved: int = Field(description="Papers of the newest run that changed tier.")

    @classmethod
    def from_domain(cls, version: LabelRulesVersion) -> LabelRulesVersionOut:
        return cls(
            version=version.version,
            possibly_min_met=version.rules.possibly_min_met,
            unclear_counts_as=version.rules.unclear_counts_as,
            changed_by=version.created_by,
            changed_at=version.created_at,
            papers_moved=version.papers_moved,
        )


class LabelRulesOut(BaseModel):
    current: LabelRulesVersionOut | None = Field(
        description="The rules in force; null means the model's labels are shown."
    )
    versions: list[LabelRulesVersionOut] = Field(description="Every version, oldest first.")

    @classmethod
    def from_view(cls, history: RulesHistory) -> LabelRulesOut:
        return cls(
            current=None
            if history.current is None
            else LabelRulesVersionOut.from_domain(history.current),
            versions=[LabelRulesVersionOut.from_domain(v) for v in history.versions],
        )


class TierMoveOut(BaseModel):
    from_tier: ScreeningTier
    to_tier: ScreeningTier
    count: int


class RulesPreviewOut(BaseModel):
    run_id: UUID | None = Field(description="The review's newest run with papers, if any.")
    moves: list[TierMoveOut]
    moved: int
    untouchable: int = Field(
        description="Decisions rules cannot relabel: no per-criterion answers."
    )

    @classmethod
    def from_view(cls, preview: RulesPreview) -> RulesPreviewOut:
        return cls(
            run_id=preview.run_id,
            moves=[
                TierMoveOut(from_tier=m.from_tier, to_tier=m.to_tier, count=m.count)
                for m in preview.moves
            ],
            moved=preview.moved,
            untouchable=preview.untouchable,
        )


class RulesAppliedOut(RulesPreviewOut):
    version: LabelRulesVersionOut

    @classmethod
    def from_applied(cls, applied: AppliedRules) -> RulesAppliedOut:
        preview = RulesPreviewOut.from_view(applied.preview)
        return cls(
            **preview.model_dump(),
            version=LabelRulesVersionOut.from_domain(applied.version),
        )
