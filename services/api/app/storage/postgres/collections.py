"""PostgreSQL repositories for collections, project placements, tags and pins.

A placement's foreign key cascades from its collection, so deleting a collection unfiles
its projects in the same statement. Placements and tags are upserts made under the
project's row lock; a pin is `INSERT … ON CONFLICT DO NOTHING`, so the first pin stays.
"""

from __future__ import annotations

from collections.abc import Collection as Many
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.collections import (
    Collection,
    NoSuchCollectionError,
    Pin,
    ProjectPlacement,
    StoredTags,
)
from app.domain.errors import ConcurrentUpdateError
from app.storage.postgres.tables import collections, project_collections, project_pins
from app.storage.postgres.tables import project_tags as tags_table


def _to_collection(row: Row[Any]) -> Collection:
    return Collection(
        id=row.id,
        name_sealed=row.name_sealed,
        created_by=row.created_by,
        created_at=row.created_at,
        version=row.version,
    )


def _to_placement(row: Row[Any]) -> ProjectPlacement:
    return ProjectPlacement(
        project_id=row.project_id,
        collection_id=row.collection_id,
        moved_by=row.moved_by,
        moved_at=row.moved_at,
    )


class PostgresCollectionRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, collection_id: UUID) -> Collection | None:
        row = (
            await self._connection.execute(
                select(collections).where(collections.c.id == collection_id)
            )
        ).one_or_none()
        return None if row is None else _to_collection(row)

    async def list_all(self) -> list[Collection]:
        rows = await self._connection.execute(
            select(collections).order_by(collections.c.created_at, collections.c.id)
        )
        return [_to_collection(row) for row in rows]

    async def lock_all(self) -> list[Collection]:
        # SHARE ROW EXCLUSIVE conflicts with itself and with writes, never with reads:
        # namers queue behind each other while lists keep working.
        await self._connection.execute(
            text("LOCK TABLE collections IN SHARE ROW EXCLUSIVE MODE")
        )
        return await self.list_all()

    async def add(self, collection: Collection, /) -> None:
        await self._connection.execute(
            insert(collections).values(
                id=collection.id,
                name_sealed=collection.name_sealed,
                created_by=collection.created_by,
                created_at=collection.created_at,
                version=collection.version,
            )
        )

    async def save(self, collection: Collection, /) -> None:
        result = await self._connection.execute(
            update(collections)
            .where(
                collections.c.id == collection.id, collections.c.version == collection.version
            )
            .values(name_sealed=collection.name_sealed, version=collection.version + 1)
        )
        if result.rowcount != 1:
            if await self.get(collection.id) is None:
                raise ValueError(f"collection {collection.id} does not exist")
            raise ConcurrentUpdateError(f"collection {collection.id} changed concurrently")

    async def delete(self, collection_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(collections).where(collections.c.id == collection_id)
        )
        return result.rowcount == 1


class PostgresProjectPlacementRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, project_id: UUID) -> ProjectPlacement | None:
        row = (
            await self._connection.execute(
                select(project_collections).where(
                    project_collections.c.project_id == project_id
                )
            )
        ).one_or_none()
        return None if row is None else _to_placement(row)

    async def list_for_projects(self, project_ids: Many[UUID]) -> list[ProjectPlacement]:
        if not project_ids:
            return []
        rows = await self._connection.execute(
            select(project_collections).where(
                project_collections.c.project_id.in_(list(project_ids))
            )
        )
        return [_to_placement(row) for row in rows]

    async def put(self, placement: ProjectPlacement, /) -> None:
        values = {
            "project_id": placement.project_id,
            "collection_id": placement.collection_id,
            "moved_by": placement.moved_by,
            "moved_at": placement.moved_at,
        }
        statement = insert(project_collections).values(**values)
        # A savepoint, so a collection deleted meanwhile fails only this insert.
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["project_id"],
                        set_={k: statement.excluded[k] for k in values if k != "project_id"},
                    )
                )
        except IntegrityError as exc:
            raise NoSuchCollectionError("That collection no longer exists.") from exc

    async def remove(self, project_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(project_collections).where(project_collections.c.project_id == project_id)
        )
        return result.rowcount == 1


class PostgresProjectTagsRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, project_id: UUID) -> StoredTags | None:
        row = (
            await self._connection.execute(
                select(tags_table).where(tags_table.c.project_id == project_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return StoredTags(row.project_id, row.tags_sealed, row.updated_by, row.updated_at)

    async def put(self, tags: StoredTags, /) -> None:
        statement = insert(tags_table).values(
            project_id=tags.project_id,
            tags_sealed=tags.tags_sealed,
            updated_by=tags.updated_by,
            updated_at=tags.updated_at,
        )
        await self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=["project_id"],
                set_={
                    "tags_sealed": statement.excluded.tags_sealed,
                    "updated_by": statement.excluded.updated_by,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    async def remove(self, project_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(tags_table).where(tags_table.c.project_id == project_id)
        )
        return result.rowcount == 1


class PostgresPinRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, user_id: UUID, project_id: UUID) -> Pin | None:
        row = (
            await self._connection.execute(
                select(project_pins).where(
                    project_pins.c.user_id == user_id, project_pins.c.project_id == project_id
                )
            )
        ).one_or_none()
        return None if row is None else Pin(row.user_id, row.project_id, row.pinned_at)

    async def add(self, pin: Pin, /) -> bool:
        result = await self._connection.execute(
            insert(project_pins)
            .values(user_id=pin.user_id, project_id=pin.project_id, pinned_at=pin.pinned_at)
            .on_conflict_do_nothing(index_elements=["user_id", "project_id"])
        )
        return result.rowcount == 1

    async def remove(self, user_id: UUID, project_id: UUID) -> bool:
        result = await self._connection.execute(
            delete(project_pins).where(
                project_pins.c.user_id == user_id, project_pins.c.project_id == project_id
            )
        )
        return result.rowcount == 1

    async def list_for_user(self, user_id: UUID) -> list[Pin]:
        rows = await self._connection.execute(
            select(project_pins)
            .where(project_pins.c.user_id == user_id)
            .order_by(project_pins.c.pinned_at.desc(), project_pins.c.project_id.desc())
        )
        return [Pin(row.user_id, row.project_id, row.pinned_at) for row in rows]

    async def remove_for_project(self, project_id: UUID) -> int:
        result = await self._connection.execute(
            delete(project_pins).where(project_pins.c.project_id == project_id)
        )
        return result.rowcount
