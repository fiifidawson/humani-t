"""In-memory collections, placements, tags and pins.

Kept beside `memory.py`, as `memory_label_rules.py` is: `memory.py` holds one
`CollectionState` and copies it with the rest, so these commit and roll back with
everything else.
"""

from __future__ import annotations

from collections.abc import Collection as Many
from dataclasses import replace
from uuid import UUID

from app.domain.collections import (
    Collection,
    NoSuchCollectionError,
    Pin,
    ProjectPlacement,
    StoredTags,
)
from app.domain.errors import ConcurrentUpdateError


class CollectionState:
    def __init__(self) -> None:
        self.collections: dict[UUID, Collection] = {}
        self.placements: dict[UUID, ProjectPlacement] = {}
        self.tags: dict[UUID, StoredTags] = {}
        self.pins: dict[tuple[UUID, UUID], Pin] = {}

    def copy(self) -> CollectionState:
        clone = CollectionState()
        clone.collections = dict(self.collections)
        clone.placements = dict(self.placements)
        clone.tags = dict(self.tags)
        clone.pins = dict(self.pins)
        return clone


class MemoryCollectionRepository:
    def __init__(self, state: CollectionState) -> None:
        self._state = state

    async def get(self, collection_id: UUID) -> Collection | None:
        return self._state.collections.get(collection_id)

    async def list_all(self) -> list[Collection]:
        return sorted(self._state.collections.values(), key=lambda c: (c.created_at, c.id))

    async def lock_all(self) -> list[Collection]:
        # Nothing to lock: in-memory transactions do not interleave.
        return await self.list_all()

    async def add(self, collection: Collection, /) -> None:
        if collection.id in self._state.collections:
            raise ValueError(f"collection {collection.id} already exists")
        self._state.collections[collection.id] = collection

    async def save(self, collection: Collection, /) -> None:
        stored = self._state.collections.get(collection.id)
        if stored is None:
            raise ValueError(f"collection {collection.id} does not exist")
        if stored.version != collection.version:
            raise ConcurrentUpdateError(f"collection {collection.id} changed concurrently")
        self._state.collections[collection.id] = replace(
            collection, version=collection.version + 1
        )

    async def delete(self, collection_id: UUID) -> bool:
        if self._state.collections.pop(collection_id, None) is None:
            return False
        self._state.placements = {
            project_id: placement
            for project_id, placement in self._state.placements.items()
            if placement.collection_id != collection_id
        }
        return True


class MemoryProjectPlacementRepository:
    def __init__(self, state: CollectionState) -> None:
        self._state = state

    async def get(self, project_id: UUID) -> ProjectPlacement | None:
        return self._state.placements.get(project_id)

    async def list_for_projects(self, project_ids: Many[UUID]) -> list[ProjectPlacement]:
        return [p for p in self._state.placements.values() if p.project_id in project_ids]

    async def put(self, placement: ProjectPlacement, /) -> None:
        if placement.collection_id not in self._state.collections:
            raise NoSuchCollectionError("That collection no longer exists.")
        self._state.placements[placement.project_id] = placement

    async def remove(self, project_id: UUID) -> bool:
        return self._state.placements.pop(project_id, None) is not None


class MemoryProjectTagsRepository:
    def __init__(self, state: CollectionState) -> None:
        self._state = state

    async def get(self, project_id: UUID) -> StoredTags | None:
        return self._state.tags.get(project_id)

    async def put(self, tags: StoredTags, /) -> None:
        self._state.tags[tags.project_id] = tags

    async def remove(self, project_id: UUID) -> bool:
        return self._state.tags.pop(project_id, None) is not None


class MemoryPinRepository:
    def __init__(self, state: CollectionState) -> None:
        self._state = state

    async def get(self, user_id: UUID, project_id: UUID) -> Pin | None:
        return self._state.pins.get((user_id, project_id))

    async def add(self, pin: Pin, /) -> bool:
        key = (pin.user_id, pin.project_id)
        if key in self._state.pins:
            return False
        self._state.pins[key] = pin
        return True

    async def remove(self, user_id: UUID, project_id: UUID) -> bool:
        return self._state.pins.pop((user_id, project_id), None) is not None

    async def list_for_user(self, user_id: UUID) -> list[Pin]:
        mine = [p for p in self._state.pins.values() if p.user_id == user_id]
        return sorted(mine, key=lambda p: (p.pinned_at, p.project_id), reverse=True)

    async def remove_for_project(self, project_id: UUID) -> int:
        keys = [key for key in self._state.pins if key[1] == project_id]
        for key in keys:
            del self._state.pins[key]
        return len(keys)
