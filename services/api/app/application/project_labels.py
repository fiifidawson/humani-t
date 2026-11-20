"""A project's place in a person's lists: its collection, its tags, and their pin.

Tags are project text, sealed under the project's key and bound to its row; sealing and
opening them lives here so every use case does it one way. Changing them belongs in
`application/collections.py`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.encryption import ScopeEncryption
from app.domain.encryption import EncryptionContext
from app.storage.base import UnitOfWork

# Table "project_tags", column "tags"; the row is the project's.
_TAGS_PURPOSE = "project_tags.tags"


@dataclass(frozen=True, slots=True)
class ProjectLabels:
    """`pinned_at` is the caller's own pin; `None` when they have not pinned it."""

    collection_id: UUID | None = None
    tags: tuple[str, ...] = ()
    pinned_at: datetime | None = None

    @property
    def is_pinned(self) -> bool:
        return self.pinned_at is not None


NO_LABELS = ProjectLabels()


def _tags_context(project_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _TAGS_PURPOSE, str(project_id))


async def seal_tags(
    uow: UnitOfWork, encryption: ScopeEncryption, project_id: UUID, tags: Sequence[str]
) -> bytes:
    return await encryption.encrypt(
        uow, _tags_context(project_id), json.dumps(list(tags)).encode()
    )


async def read_tags(
    uow: UnitOfWork, encryption: ScopeEncryption, project_id: UUID
) -> tuple[str, ...]:
    stored = await uow.project_tags.get(project_id)
    if stored is None:
        return ()
    opened = await encryption.decrypt(uow, _tags_context(project_id), stored.tags_sealed)
    return tuple(str(tag) for tag in json.loads(opened))


class SealedTags:
    """Tags read the one way every use case reads them, for search's `ProjectTagSource`."""

    def __init__(self, encryption: ScopeEncryption) -> None:
        self._encryption = encryption

    async def tags(self, uow: UnitOfWork, project_id: UUID) -> tuple[str, ...]:
        return await read_tags(uow, self._encryption, project_id)


async def read_labels(
    uow: UnitOfWork, encryption: ScopeEncryption, project_id: UUID, user_id: UUID
) -> ProjectLabels:
    placement = await uow.placements.get(project_id)
    pin = await uow.pins.get(user_id, project_id)
    return ProjectLabels(
        collection_id=None if placement is None else placement.collection_id,
        tags=await read_tags(uow, encryption, project_id),
        pinned_at=None if pin is None else pin.pinned_at,
    )
