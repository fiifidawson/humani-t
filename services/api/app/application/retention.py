"""The retention period in use: a project's date, an owner's Keep, and deletion when due.

The date is worked out on every read from the project's last activity and its latest Keep
(ADR 0020); nothing stores it. Deleting goes through the same `shred` as an owner's
delete, under the project's row lock, after checking again that the date has passed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from app.application.accounts import Current
from app.application.audit import SYSTEM, AuditLog
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.application.project_list import last_activity_at
from app.application.projects import shred
from app.domain.accounts import NotFoundError
from app.domain.errors import StorageUnavailableError
from app.domain.projects import Action, Project
from app.domain.retention import RetentionOffError, RetentionPolicy, days_left, is_due
from app.storage.base import StorageProvider, UnitOfWork

SWEEP_INTERVAL = timedelta(hours=1)

_NO_PROJECT = "No such project."
_OFF = "This deployment does not delete projects for age, so there is nothing to keep."

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def deletion_date(
    uow: UnitOfWork, project: Project, policy: RetentionPolicy
) -> datetime | None:
    """When the project is deleted for age; `None` when the deployment sets no period."""
    if not policy.is_on:
        return None
    last = await last_activity_at(
        uow, project.id, created_at=project.created_at, archived_at=project.archived_at
    )
    if project.read_at is not None:
        last = max(last, project.read_at)
    return policy.deletes_at(last_activity_at=last, kept_at=project.kept_at)


@dataclass(frozen=True, slots=True)
class RetentionView:
    """A project's date as members see it. All `None` but `kept_at` when retention is off."""

    period_days: int | None
    deletes_at: datetime | None
    days_left: int | None
    kept_at: datetime | None


class RetentionService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        policy: RetentionPolicy,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self.policy = policy
        self._clock = clock

    async def view(self, current: Current, project_id: UUID) -> RetentionView:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            project = await uow.projects.get(project_id)
            if project is None:
                raise NotFoundError(_NO_PROJECT)
            return await self._view(uow, project)

    async def keep(
        self, current: Current, project_id: UUID, request_id: str | None = None
    ) -> RetentionView:
        """Restart the project's clock. `RetentionOffError` when there is no period."""
        async with self._storage.unit_of_work() as uow:
            # The lock orders a Keep and a deletion of the same project: the later one
            # sees what the earlier one did.
            project = await uow.projects.lock(project_id)
            await require_member(
                uow, current.user.id, project_id, Action.UPDATE_PROJECT, not_found=_NO_PROJECT
            )
            if project is None:
                raise NotFoundError(_NO_PROJECT)
            if not self.policy.is_on:
                raise RetentionOffError(_OFF)
            kept = project.kept(self._clock())
            await uow.projects.save(kept)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "project.kept",
                "project",
                str(project_id),
                scope_id=project_id,
            )
            view = await self._view(uow, kept)
            await uow.commit()
        return view

    async def due_projects(self) -> tuple[UUID, ...]:
        """The live projects whose date has passed, read without locking any of them."""
        if not self.policy.is_on:
            return ()
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            due: list[UUID] = []
            for project in await uow.projects.list_live():
                deletes_at = await deletion_date(uow, project, self.policy)
                if deletes_at is not None and is_due(deletes_at, now):
                    due.append(project.id)
        return tuple(due)

    async def delete_if_due(self, project_id: UUID) -> bool:
        """Delete the project if, under its lock, its date has still passed. True if deleted.

        A Keep or new work since it was found due moves the date on, so it is spared; a
        project already deleted is left as it is.
        """
        async with self._storage.unit_of_work() as uow:
            project = await uow.projects.lock(project_id)
            if project is None or project.is_deleted:
                return False
            now = self._clock()
            deletes_at = await deletion_date(uow, project, self.policy)
            if deletes_at is None or not is_due(deletes_at, now):
                return False
            await self._audit.record(
                uow,
                SYSTEM,
                "project.retention_expired",
                "project",
                str(project_id),
                scope_id=project_id,
            )
            await shred(uow, self._encryption, self._audit, project, SYSTEM, now)
            await uow.commit()
        _log.info("project_deleted_for_retention", project_id=str(project_id))
        return True

    async def delete_due(self) -> int:
        """Delete every project whose date has passed. Safe to run twice, or in two workers."""
        deleted = 0
        for project_id in await self.due_projects():
            try:
                deleted += await self.delete_if_due(project_id)
            except StorageUnavailableError:
                raise
            except Exception:
                # One project that cannot be deleted must not keep the others past their date.
                _log.exception("retention_delete_failed", project_id=str(project_id))
        return deleted

    async def serve(self, stop: asyncio.Event, interval: timedelta = SWEEP_INTERVAL) -> None:
        """Sweep for due projects until `stop` is set. Returns at once when retention is off."""
        if not self.policy.is_on:
            return
        while not stop.is_set():
            try:
                await self.delete_due()
            except StorageUnavailableError:
                _log.warning("retention_storage_unavailable")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval.total_seconds())

    async def _view(self, uow: UnitOfWork, project: Project) -> RetentionView:
        deletes_at = await deletion_date(uow, project, self.policy)
        return RetentionView(
            period_days=self.policy.period_days,
            deletes_at=deletes_at,
            days_left=None if deletes_at is None else days_left(deletes_at, self._clock()),
            kept_at=project.kept_at,
        )
