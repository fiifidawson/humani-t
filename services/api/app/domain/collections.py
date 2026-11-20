"""Collections, tags and pins: how people group and find their projects (UI_UX_SPEC §7.3).

A collection is a label shared across the deployment, with no access rules of its own
(ADR 0016): it never grants or reveals anything about a project. Tags are a project's own
free labels; a pin is one person's bookmark. Sealing text belongs in the use cases; which
projects a list shows, in `domain/project_list.py`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.timestamps import require_aware

MAX_COLLECTION_NAME_LENGTH = 80
MAX_TAG_LENGTH = 40
MAX_TAGS_PER_PROJECT = 20

# As for project names: characters that reorder or hide the text around them.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


def _is_hidden(ch: str) -> bool:
    return ch in _BIDI_CONTROLS or unicodedata.category(ch) in _FORBIDDEN_CATEGORIES


def _label(text: str, field: str, limit: int) -> str:
    if any(_is_hidden(ch) for ch in text):
        raise ValueError(f"{field} must not contain control characters")
    collapsed = " ".join(text.split())
    if not collapsed:
        raise ValueError(f"{field} must not be blank")
    if len(collapsed) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return collapsed


def normalize_collection_name(name: str) -> str:
    """Trim the name and collapse its spaces; refuse one blank, too long or hiding text."""
    return _label(name, "Collection.name", MAX_COLLECTION_NAME_LENGTH)


def collection_name_key(name: str) -> str:
    """What two names are compared by: one collection per name, however it is spelled."""
    return normalize_collection_name(name).casefold()


def normalize_tag(tag: str) -> str:
    return _label(tag, "tag", MAX_TAG_LENGTH)


def normalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Each tag cleaned, repeats dropped (ignoring case) with the first spelling kept."""
    kept: dict[str, str] = {}
    for tag in tags:
        clean = normalize_tag(tag)
        kept.setdefault(clean.casefold(), clean)
    if len(kept) > MAX_TAGS_PER_PROJECT:
        raise ValueError(f"A project may have at most {MAX_TAGS_PER_PROJECT} tags")
    return tuple(kept.values())


@dataclass(frozen=True, slots=True)
class Collection:
    """A shared label. Its name is sealed under the collection's own key (ADR 0016)."""

    id: UUID
    name_sealed: bytes
    created_by: UUID
    created_at: datetime
    # Optimistic locking: a stale rename can never overwrite a newer one.
    version: int = 0

    def __post_init__(self) -> None:
        require_aware("Collection", created_at=self.created_at)
        if not self.name_sealed:
            raise ValueError("Collection.name_sealed must not be empty")

    def renamed(self, name_sealed: bytes) -> Collection:
        return replace(self, name_sealed=name_sealed)


def may_manage_collection(collection: Collection, *, user_id: UUID, is_admin: bool) -> bool:
    """Rename or delete: its creator, or an administrator tidying the deployment's labels."""
    return is_admin or collection.created_by == user_id


@dataclass(frozen=True, slots=True)
class ProjectPlacement:
    """The one collection a project is filed in. No row means "Not in a collection"."""

    project_id: UUID
    collection_id: UUID
    moved_by: UUID
    moved_at: datetime

    def __post_init__(self) -> None:
        require_aware("ProjectPlacement", moved_at=self.moved_at)


@dataclass(frozen=True, slots=True)
class StoredTags:
    """A project's tags as stored: the whole list sealed together under its key."""

    project_id: UUID
    tags_sealed: bytes
    updated_by: UUID
    updated_at: datetime

    def __post_init__(self) -> None:
        require_aware("StoredTags", updated_at=self.updated_at)
        if not self.tags_sealed:
            raise ValueError("StoredTags.tags_sealed must not be empty")


@dataclass(frozen=True, slots=True)
class Pin:
    """One person's bookmark on a project they belong to."""

    user_id: UUID
    project_id: UUID
    pinned_at: datetime

    def __post_init__(self) -> None:
        require_aware("Pin", pinned_at=self.pinned_at)


class CollectionError(Exception):
    """Base for the refusals below. Messages are safe to show to the user."""


class CollectionNameTakenError(CollectionError):
    """Another collection already uses this name, in some spelling."""


class NoSuchCollectionError(CollectionError):
    """The collection named for a project does not exist (any more)."""
