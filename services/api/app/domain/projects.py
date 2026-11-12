"""Projects, membership and the role policy: who may do what inside a project.

A project is the unit of access, encryption and deletion. Its name is held here only
sealed under the project's own key.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

MAX_PROJECT_NAME_LENGTH = 200

# Characters that can reorder or hide the text around them. A name such as
# "Gao‮esnopser" would display as something other than what is stored.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
# Control characters and line or paragraph separators break logs and exports.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


class Role(StrEnum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Action(StrEnum):
    """Everything a member might do inside a project."""

    READ_PROJECT = "project.read"
    ASK_QUESTIONS = "question.ask"
    EDIT_REVIEW = "review.edit"
    MANAGE_RUNS = "run.manage"
    CONFIRM_GATE = "gate.confirm"
    EDIT_LABELS = "label.edit"
    MANAGE_DOCUMENTS = "document.manage"
    VIEW_RAW_TEXT = "raw_text.view"
    MANAGE_MEMBERS = "member.manage"
    UPDATE_PROJECT = "project.update"
    DELETE_PROJECT = "project.delete"
    LEAVE_PROJECT = "project.leave"


_EVERYONE = frozenset(Role)
_CONTRIBUTORS = frozenset({Role.OWNER, Role.REVIEWER})
_OWNERS = frozenset({Role.OWNER})

_PERMISSIONS: Mapping[Action, frozenset[Role]] = {
    Action.READ_PROJECT: _EVERYONE,
    Action.ASK_QUESTIONS: _CONTRIBUTORS,
    Action.EDIT_REVIEW: _CONTRIBUTORS,
    Action.MANAGE_RUNS: _CONTRIBUTORS,
    Action.CONFIRM_GATE: _CONTRIBUTORS,
    Action.EDIT_LABELS: _CONTRIBUTORS,
    Action.MANAGE_DOCUMENTS: _OWNERS,
    Action.VIEW_RAW_TEXT: _OWNERS,
    Action.MANAGE_MEMBERS: _OWNERS,
    Action.UPDATE_PROJECT: _OWNERS,
    Action.DELETE_PROJECT: _OWNERS,
    Action.LEAVE_PROJECT: _EVERYONE,
}


def can(role: Role | None, action: Action) -> bool:
    """Whether a member with `role` may take `action`. `None` means not a member.

    Deny by default: an action missing from the table is refused, never allowed.
    """
    if role is None:
        return False
    return role in _PERMISSIONS.get(action, frozenset[Role]())


def normalize_project_name(name: str) -> str:
    """Trim the name and refuse one that is blank, too long or hides its own text."""
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("Project.name must not be blank")
    if len(trimmed) > MAX_PROJECT_NAME_LENGTH:
        raise ValueError(f"Project.name must be at most {MAX_PROJECT_NAME_LENGTH} characters")
    if any(
        ch in _BIDI_CONTROLS or unicodedata.category(ch) in _FORBIDDEN_CATEGORIES
        for ch in trimmed
    ):
        raise ValueError("Project.name must not contain control characters")
    return trimmed


def _require_aware(owner: str, **timestamps: datetime | None) -> None:
    for name, value in timestamps.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Project:
    """A project's record. A deleted project stays as a tombstone row.

    The tombstone keeps the id from ever being reused and keeps the dates the
    audit log refers to; its ciphertext is unreadable once the key is destroyed.
    """

    id: UUID
    name_sealed: bytes
    created_at: datetime
    created_by: UUID
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    # Optimistic locking, as for users: a stale copy can never overwrite a newer one.
    version: int = 0

    def __post_init__(self) -> None:
        _require_aware(
            "Project",
            created_at=self.created_at,
            archived_at=self.archived_at,
            deleted_at=self.deleted_at,
        )
        if not self.name_sealed:
            raise ValueError("Project.name_sealed must not be empty")

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def renamed(self, name_sealed: bytes) -> Project:
        return replace(self, name_sealed=name_sealed)

    def archived(self, at: datetime) -> Project:
        """Archive; archiving again keeps the first date."""
        return self if self.is_archived else replace(self, archived_at=at)

    def unarchived(self) -> Project:
        return replace(self, archived_at=None)

    def deleted(self, at: datetime) -> Project:
        return replace(self, deleted_at=at)


@dataclass(frozen=True, slots=True)
class Membership:
    project_id: UUID
    user_id: UUID
    role: Role
    added_at: datetime
    # `None` for the creator's own membership, which nobody granted.
    added_by: UUID | None

    def __post_init__(self) -> None:
        _require_aware("Membership", added_at=self.added_at)

    def with_role(self, role: Role) -> Membership:
        return replace(self, role=role)


class ProjectError(Exception):
    """Base for the refusals below. Messages are safe to show to the user."""


class LastOwnerError(ProjectError):
    """The change would leave the project without an owner."""


class AlreadyMemberError(ProjectError):
    """The user already belongs to the project."""


class NoSuchAccountError(ProjectError):
    """No active account uses the email given for a new member."""


def require_an_owner_remains(
    memberships: Sequence[Membership], user_id: UUID, new_role: Role | None
) -> None:
    """Refuse a change of `user_id` to `new_role` that would leave no owner.

    `new_role` is `None` for a removal. `memberships` must be every membership
    of the project, read under the project's lock, or the count can be stale.
    """
    target = next((m for m in memberships if m.user_id == user_id), None)
    if target is None or target.role is not Role.OWNER or new_role is Role.OWNER:
        return
    if sum(m.role is Role.OWNER for m in memberships) <= 1:
        raise LastOwnerError(
            "A project needs at least one owner. Make someone else an owner first."
        )
