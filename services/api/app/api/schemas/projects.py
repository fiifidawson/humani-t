"""Request and response models for projects and their members.

Every request model forbids unknown fields, so a client cannot set the owner,
the id or a date by adding them to the body. A member's email address is sent to
owners only, and left out of the body entirely for everyone else.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.application.projects import MemberView, ProjectView
from app.domain.projects import MAX_PROJECT_NAME_LENGTH, ProjectStatus, Role

# The domain trims and checks the name properly; this only bounds the size.
_Name = Field(min_length=1, max_length=MAX_PROJECT_NAME_LENGTH)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(_Strict):
    name: str = _Name


class ProjectRenameIn(_Strict):
    name: str = _Name


class ProjectMaskingIn(_Strict):
    is_masking_on: bool


class MemberIn(_Strict):
    email: EmailStr
    role: Role


class MemberRoleIn(_Strict):
    role: Role


class ProjectOut(BaseModel):
    id: UUID
    name: str
    role: Role = Field(description="The caller's own role in this project.")
    created_at: datetime
    archived_at: datetime | None
    status: ProjectStatus = Field(
        description="UI_UX_SPEC §11.1: derived from the newest review and its latest run."
    )
    is_masking_on: bool = Field(description="The owner's switch, as stored (ADR 0014).")
    is_masking_effective: bool = Field(
        description=(
            "Whether text is masked before it reaches the model now: the switch, unless "
            "the deployment's hosted model requires masking."
        )
    )
    can_turn_masking_off: bool = Field(
        description="false while the deployment requires masking for every project."
    )

    @classmethod
    def from_view(cls, view: ProjectView) -> ProjectOut:
        return cls(
            id=view.id,
            name=view.name,
            role=view.role,
            created_at=view.created_at,
            archived_at=view.archived_at,
            status=view.status,
            is_masking_on=view.masking.is_on,
            is_masking_effective=view.masking.is_effective,
            can_turn_masking_off=view.masking.can_turn_off,
        )


class MemberOut(BaseModel):
    user_id: UUID
    display_name: str
    role: Role
    added_at: datetime
    email: str | None = Field(
        default=None, description="Sent to owners only; absent for everyone else."
    )

    @classmethod
    def from_view(cls, view: MemberView) -> MemberOut:
        return cls(
            user_id=view.user_id,
            display_name=view.display_name,
            role=view.role,
            added_at=view.added_at,
            email=view.email,
        )
