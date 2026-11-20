"""Your view first: record and read the option a member leaned towards before reading.

Keyed by the run that wrote the brief and by the member, so each person sees only their
own answer. The option is sealed under the project's key and bound to its row; the audit
log gets the run id only. Any member may answer, once, while the project asks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.briefs import BriefSeal
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.domain.accounts import NotFoundError
from app.domain.encryption import EncryptionContext
from app.domain.leanings import Leaning, LeaningRefusedError, StoredLeaning, require_offered
from app.domain.projects import Action
from app.domain.runs import Run
from app.storage.base import StorageProvider, UnitOfWork

# Table "leanings", column "option"; the row is the run and the member.
_PURPOSE = "leanings.option"
_NO_RUN = "No such run."
_NO_BRIEF = "This run has no decision brief."
_NOT_ASKED = "This project does not ask for your view first."
_ALREADY = "You already said which option you leaned towards for this brief."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _context(project_id: UUID, run_id: UUID, user_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _PURPOSE, f"{run_id}:{user_id}")


class LeaningService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._seal = BriefSeal(encryption)
        self._audit = audit
        self._clock = clock

    async def authorize(self, current: Current, run_id: UUID) -> None:
        """Check access before the body is read: a non-member gets the missing run's 404."""
        async with self._storage.unit_of_work() as uow:
            await self._run(uow, current, run_id)

    async def mine(self, current: Current, run_id: UUID) -> Leaning | None:
        """The caller's own answer for this run; `None` until they give one."""
        async with self._storage.unit_of_work() as uow:
            await self._run(uow, current, run_id)
            stored = await uow.leanings.get(run_id, current.user.id)
            return None if stored is None else await self._open(uow, stored)

    async def record(
        self,
        current: Current,
        run_id: UUID,
        option: str | None,
        request_id: str | None = None,
    ) -> Leaning:
        """Store the caller's answer once. `option` is `None` for "Not sure".

        Raises `ValueError` for an option the brief does not offer, and
        `LeaningRefusedError` once answered or while the project does not ask.
        """
        async with self._storage.unit_of_work() as uow:
            run = await self._run(uow, current, run_id)
            project = await uow.projects.get(run.project_id)
            if project is None or not project.is_view_first_on:
                raise LeaningRefusedError(_NOT_ASKED)
            record = await uow.briefs.for_run(run_id)
            if record is None:
                raise NotFoundError(_NO_BRIEF)
            opened = await self._seal.open_brief(uow, record)
            require_offered(option, [o.name for o in opened.brief.body.options])
            leaning = Leaning(run.project_id, run_id, current.user.id, option, self._clock())
            if not await uow.leanings.add(await self._sealed(uow, leaning)):
                raise LeaningRefusedError(_ALREADY)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "brief.leaning_recorded",
                "run",
                str(run_id),
                scope_id=run.project_id,
            )
            await uow.commit()
        return leaning

    @staticmethod
    async def _run(uow: UnitOfWork, current: Current, run_id: UUID) -> Run:
        run = await uow.runs.get(run_id)
        if run is None:
            raise NotFoundError(_NO_RUN)
        await require_member(
            uow, current.user.id, run.project_id, Action.READ_PROJECT, not_found=_NO_RUN
        )
        return run

    async def _sealed(self, uow: UnitOfWork, leaning: Leaning) -> StoredLeaning:
        context = _context(leaning.project_id, leaning.run_id, leaning.user_id)
        sealed = await self._encryption.encrypt(
            uow, context, json.dumps(leaning.option).encode()
        )
        return StoredLeaning(
            leaning.project_id, leaning.run_id, leaning.user_id, sealed, leaning.answered_at
        )

    async def _open(self, uow: UnitOfWork, stored: StoredLeaning) -> Leaning:
        context = _context(stored.project_id, stored.run_id, stored.user_id)
        option = json.loads(await self._encryption.decrypt(uow, context, stored.option_sealed))
        return Leaning(
            stored.project_id, stored.run_id, stored.user_id, option, stored.answered_at
        )
