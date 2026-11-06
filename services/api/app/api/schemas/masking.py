"""Request and response models for project masking rules.

Pydantic checks shape and size; `normalize_masking_term` checks meaning, and
its `ValueError` becomes a 422 in the route.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.masking import MAX_MASKING_TERM_LENGTH, EntityType, MaskingRuleRecord


class MaskingRuleIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"term": "Kofi Mensah", "entity_type": "PERSON"}},
    )

    term: str = Field(min_length=1, max_length=MAX_MASKING_TERM_LENGTH * 2)
    entity_type: EntityType = Field(
        default=EntityType.PERSON, description="The placeholder it becomes: [PERSON_1]."
    )


class MaskingRuleOut(BaseModel):
    id: UUID
    term: str = Field(
        description="Raw text: shown only to owners, and every listing is audited."
    )
    entity_type: EntityType
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, record: MaskingRuleRecord, term: str) -> MaskingRuleOut:
        return cls(
            id=record.id,
            term=term,
            entity_type=record.entity_type,
            created_by=record.created_by,
            created_at=record.created_at,
        )
