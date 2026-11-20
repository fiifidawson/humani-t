"""Collections, tags and pins: create and manage collections, file, tag and pin projects.

A collection is a shared label (ADR 0016): its name is sealed under its own key, and its
count covers only the caller's projects. Filing and tagging take the project's row lock,
so changes made at the same moment apply one after the other. Bulk actions run each
project as its own use case and report what they skipped.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.project_labels import read_tags, seal_tags
from app.application.projects import ProjectService
from app.domain.accounts import ForbiddenError, NotFoundError
from app.domain.collections import (
    Collection,
    CollectionNameTakenError,
    NoSuchCollectionError,
    Pin,
    ProjectPlacement,
    StoredTags,
    collection_name_key,
    may_manage_collection,
    normalize_collection_name,
    normalize_tag,
    normalize_tags,
)
from app.domain.encryption import EncryptionContext
from app.domain.projects import Action
from app.storage.base import StorageProvider, UnitOfWork

# Table "collections", column "name"; the row, and the key's scope, is the collection.
_NAME_PURPOSE = "collections.name"
_NO_PROJECT = "No such project."
_NO_COLLECTION = "No such collection."
_NAME_TAKEN = "A collection with this name already exists."


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CollectionView:
    """A collection as one person sees it: `project_count` counts their projects only."""

    id: UUID
    name: str
    project_count: int
    can_manage: bool
    created_at: datetime


class SkipReason(StrEnum):
    # A project the caller is not in reads exactly as one that does not exist.
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    TOO_MANY_TAGS = "too_many_tags"


@dataclass(frozen=True, slots=True)
class BulkOutcome:
    changed: tuple[UUID, ...]
    skipped: tuple[tuple[UUID, SkipReason], ...]


class CollectionService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        projects: ProjectService,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._projects = projects
        self._clock = clock

    async def list_collections(self, current: Current) -> list[CollectionView]:
        """Every collection, by name, each counting the caller's live projects in it."""
        async with self._storage.unit_of_work() as uow:
            mine = [
                p.id
                for p in await uow.projects.list_for_member(current.user.id)
                if not p.is_archived
            ]
            counts = Counter(
                p.collection_id for p in await uow.placements.list_for_projects(mine)
            )
            views = [
                await self._view(uow, current, c, counts[c.id])
                for c in await uow.collections.list_all()
            ]
        return sorted(views, key=lambda v: (v.name.casefold(), v.id))

    async def create(
        self, current: Current, name: str, request_id: str | None = None
    ) -> CollectionView:
        """Any signed-in user may; `CollectionNameTakenError` if the name is in use."""
        name = normalize_collection_name(name)
        collection_id = uuid.uuid4()
        async with self._storage.unit_of_work() as uow:
            await self._refuse_taken(uow, name, keep=None)
            await self._encryption.create_scope(uow, collection_id)
            collection = Collection(
                id=collection_id,
                name_sealed=await self._seal_name(uow, collection_id, name),
                created_by=current.user.id,
                created_at=self._clock(),
            )
            await uow.collections.add(collection)
            await self._record(uow, current, request_id, "collection.created", collection_id)
            await uow.commit()
        return CollectionView(collection_id, name, 0, True, collection.created_at)

    async def rename(
        self, current: Current, collection_id: UUID, name: str, request_id: str | None = None
    ) -> CollectionView:
        async with self._storage.unit_of_work() as uow:
            collection = await self._managed(uow, current, collection_id)
            name = normalize_collection_name(name)
            await self._refuse_taken(uow, name, keep=collection_id)
            renamed = collection.renamed(await self._seal_name(uow, collection_id, name))
            await uow.collections.save(renamed)
            await self._record(uow, current, request_id, "collection.renamed", collection_id)
            await uow.commit()
        (view,) = [v for v in await self.list_collections(current) if v.id == collection_id]
        return view

    async def delete(
        self, current: Current, collection_id: UUID, request_id: str | None = None
    ) -> None:
        """Remove the label: every project it held, anyone's, becomes "Not in a collection".

        Refusing while it held projects would tell the caller that projects they cannot
        see exist. Its key is destroyed, so its name is unreadable in any copy.
        """
        async with self._storage.unit_of_work() as uow:
            await self._managed(uow, current, collection_id)
            await uow.collections.delete(collection_id)
            await self._encryption.destroy_scope(uow, collection_id)
            await self._record(uow, current, request_id, "collection.deleted", collection_id)
            await uow.commit()

    async def move(
        self,
        current: Current,
        project_id: UUID,
        collection_id: UUID | None,
        request_id: str | None = None,
    ) -> None:
        """File the project in one collection, or in none. Owners only."""
        async with self._storage.unit_of_work() as uow:
            await self._owned(uow, current, project_id)
            before = await uow.placements.get(project_id)
            if (None if before is None else before.collection_id) == collection_id:
                return
            if collection_id is None:
                await uow.placements.remove(project_id)
            else:
                if await uow.collections.get(collection_id) is None:
                    raise NoSuchCollectionError(_NO_COLLECTION)
                await uow.placements.put(
                    ProjectPlacement(project_id, collection_id, current.user.id, self._clock())
                )
            await self._audit.record(
                uow,
                current.actor(request_id),
                "project.collection_changed",
                "project",
                str(project_id),
                scope_id=project_id,
                details={
                    "collection_id": None if collection_id is None else str(collection_id)
                },
            )
            await uow.commit()

    async def set_tags(
        self,
        current: Current,
        project_id: UUID,
        tags: Sequence[str],
        request_id: str | None = None,
    ) -> None:
        """Replace the project's tags. Owners only; the tags are sealed under its key."""
        async with self._storage.unit_of_work() as uow:
            await self._owned(uow, current, project_id)
            await self._store_tags(uow, current, project_id, normalize_tags(tags), request_id)
            await uow.commit()

    async def add_tag(
        self, current: Current, project_id: UUID, tag: str, request_id: str | None = None
    ) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._owned(uow, current, project_id)
            existing = await read_tags(uow, self._encryption, project_id)
            tags = normalize_tags([*existing, tag])
            if tags != existing:
                await self._store_tags(uow, current, project_id, tags, request_id)
                await uow.commit()

    async def pin(self, current: Current, project_id: UUID) -> None:
        """Pin for the caller alone. Pinning again keeps the first pin.

        Not audited: a pin is one person's bookmark, not a decision about the project.
        """
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            if await uow.pins.add(Pin(current.user.id, project_id, self._clock())):
                await uow.commit()

    async def unpin(self, current: Current, project_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            if await uow.pins.remove(current.user.id, project_id):
                await uow.commit()

    async def move_many(
        self,
        current: Current,
        project_ids: Sequence[UUID],
        collection_id: UUID | None,
        request_id: str | None = None,
    ) -> BulkOutcome:
        """Move each project the caller owns; an unknown collection refuses them all."""
        if collection_id is not None:
            async with self._storage.unit_of_work() as uow:
                if await uow.collections.get(collection_id) is None:
                    raise NoSuchCollectionError(_NO_COLLECTION)
        return await self._each(
            project_ids, lambda p: self.move(current, p, collection_id, request_id)
        )

    async def tag_many(
        self,
        current: Current,
        project_ids: Sequence[UUID],
        tag: str,
        request_id: str | None = None,
    ) -> BulkOutcome:
        tag = normalize_tag(tag)
        return await self._each(
            project_ids,
            lambda p: self.add_tag(current, p, tag, request_id),
            # The tag itself is valid, so the only refusal left is the project's tag limit.
            also_skip={ValueError: SkipReason.TOO_MANY_TAGS},
        )

    async def archive_many(
        self, current: Current, project_ids: Sequence[UUID], request_id: str | None = None
    ) -> BulkOutcome:
        async def archive(project_id: UUID) -> None:
            await self._projects.archive(current, project_id, request_id)

        return await self._each(project_ids, archive)

    @staticmethod
    async def _each(
        project_ids: Sequence[UUID],
        change: Callable[[UUID], Awaitable[None]],
        *,
        also_skip: Mapping[type[Exception], SkipReason] | None = None,
    ) -> BulkOutcome:
        """Apply `change` to each project in turn, one transaction each.

        A mixed selection is normal: projects the caller may change are changed, and the
        rest are reported, never refused as a whole.
        """
        reasons: dict[type[Exception], SkipReason] = {
            NotFoundError: SkipReason.NOT_FOUND,
            ForbiddenError: SkipReason.NOT_ALLOWED,
            **(also_skip or {}),
        }
        changed: list[UUID] = []
        skipped: list[tuple[UUID, SkipReason]] = []
        for project_id in dict.fromkeys(project_ids):
            try:
                await change(project_id)
            except tuple(reasons) as exc:
                reason = next(r for kind, r in reasons.items() if isinstance(exc, kind))
                skipped.append((project_id, reason))
            else:
                changed.append(project_id)
        return BulkOutcome(tuple(changed), tuple(skipped))

    async def _store_tags(
        self,
        uow: UnitOfWork,
        current: Current,
        project_id: UUID,
        tags: tuple[str, ...],
        request_id: str | None,
    ) -> None:
        if tags:
            sealed = await seal_tags(uow, self._encryption, project_id, tags)
            await uow.project_tags.put(
                StoredTags(project_id, sealed, current.user.id, self._clock())
            )
        else:
            await uow.project_tags.remove(project_id)
        await self._audit.record(
            uow,
            current.actor(request_id),
            "project.tags_changed",
            "project",
            str(project_id),
            scope_id=project_id,
            details={"count": len(tags)},
        )

    @staticmethod
    async def _owned(uow: UnitOfWork, current: Current, project_id: UUID) -> None:
        """Lock the project, then check the caller may change it: owners only."""
        await uow.projects.lock(project_id)
        await require_member(
            uow, current.user.id, project_id, Action.UPDATE_PROJECT, not_found=_NO_PROJECT
        )

    @staticmethod
    async def _managed(uow: UnitOfWork, current: Current, collection_id: UUID) -> Collection:
        collection = await uow.collections.get(collection_id)
        if collection is None:
            raise NotFoundError(_NO_COLLECTION)
        if not may_manage_collection(
            collection, user_id=current.user.id, is_admin=current.user.is_admin
        ):
            raise ForbiddenError("Only the person who made this collection can change it.")
        return collection

    async def _refuse_taken(self, uow: UnitOfWork, name: str, *, keep: UUID | None) -> None:
        """Raise unless no other collection uses `name`. Holds the collections lock."""
        key = collection_name_key(name)
        for other in await uow.collections.lock_all():
            if other.id != keep and collection_name_key(await self._name(uow, other)) == key:
                raise CollectionNameTakenError(_NAME_TAKEN)

    async def _view(
        self, uow: UnitOfWork, current: Current, collection: Collection, count: int
    ) -> CollectionView:
        return CollectionView(
            collection.id,
            await self._name(uow, collection),
            count,
            may_manage_collection(
                collection, user_id=current.user.id, is_admin=current.user.is_admin
            ),
            collection.created_at,
        )

    async def _name(self, uow: UnitOfWork, collection: Collection) -> str:
        opened = await self._encryption.decrypt(
            uow, _name_context(collection.id), collection.name_sealed
        )
        return opened.decode()

    async def _seal_name(self, uow: UnitOfWork, collection_id: UUID, name: str) -> bytes:
        return await self._encryption.encrypt(uow, _name_context(collection_id), name.encode())

    async def _record(
        self,
        uow: UnitOfWork,
        current: Current,
        request_id: str | None,
        action: str,
        collection_id: UUID,
    ) -> None:
        await self._audit.record(
            uow,
            current.actor(request_id),
            action,
            "collection",
            str(collection_id),
            scope_id=collection_id,
        )


def _name_context(collection_id: UUID) -> EncryptionContext:
    return EncryptionContext(collection_id, _NAME_PURPOSE, str(collection_id))
