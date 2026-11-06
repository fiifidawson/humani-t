"""Request and response models for projects and their members.

Every request model forbids unknown fields, so a client cannot set the owner,
the id or a date by adding them to the body. Response models carry no email
addresses: members are shown by display name.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.application.projects import MemberView, ProjectView
from app.domain.projects import MAX_PROJECT_NAME_LENGTH, Role

# The domain trims and checks the name properly; this only bounds the size.
_Name = Field(min_length=1, max_length=MAX_PROJECT_NAME_LENGTH)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(_Strict):
    name: str = _Name


class ProjectRenameIn(_Strict):
    name: str = _Name


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

    @classmethod
    def from_view(cls, view: ProjectView) -> ProjectOut:
        return cls(
            id=view.id,
            name=view.name,
            role=view.role,
            created_at=view.created_at,
            archived_at=view.archived_at,
        )


class MemberOut(BaseModel):
    user_id: UUID
    display_name: str
    role: Role
    added_at: datetime

    @classmethod
    def from_view(cls, view: MemberView) -> MemberOut:
        return cls(
            user_id=view.user_id,
            display_name=view.display_name,
            role=view.role,
            added_at=view.added_at,
        )
