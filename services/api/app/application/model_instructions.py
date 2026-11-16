"""A project's instructions to the model: reading, saving, and what a call is given (ADR 0015).

Both texts are sealed together under the project's key; each save is a new version. They
are person-typed text bound for a model, so saving refuses the project's always-masked
terms, and `as_sent` masks them as typed questions are masked. Which prompt carries which
text is decided by the use cases that build prompts.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.masking import MaskingRuleService, mask_as_chosen
from app.application.membership import require_member
from app.domain.encryption import EncryptionContext
from app.domain.errors import ConcurrentUpdateError
from app.domain.masking import ProjectPrivacy
from app.domain.model_instructions import (
    InstructionsKind,
    ModelInstructions,
    ModelInstructionsVersion,
    SentInstructions,
    StoredModelInstructions,
)
from app.domain.plans import refuse_rule_terms
from app.domain.projects import Action
from app.privacy.base import Sanitizer
from app.storage.base import StorageProvider, UnitOfWork

# Table, column; the row is the project's version.
_PURPOSE = "model_instruction_versions.instructions"
_NO_PROJECT = "No such project."
# As the members list shows an account that no longer exists.
_UNKNOWN_AUTHOR = "Unknown account"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _context(project_id: UUID, version: int) -> EncryptionContext:
    return EncryptionContext(project_id, _PURPOSE, str(version))


@dataclass(frozen=True, slots=True)
class SavedInstructions:
    """A version in the clear, with the name of who saved it."""

    version: ModelInstructionsVersion
    saved_by_name: str


@dataclass(frozen=True, slots=True)
class InstructionsHistory:
    """Every version of a project's instructions, oldest first; the last one is in force."""

    versions: tuple[SavedInstructions, ...]

    @property
    def current(self) -> SavedInstructions | None:
        return self.versions[-1] if self.versions else None


class ModelInstructionsService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        sanitizer: Sanitizer,
        rules: MaskingRuleService,
        audit: AuditLog,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._sanitizer = sanitizer
        self._rules = rules
        self._audit = audit
        self._clock = clock

    async def history(self, current: Current, project_id: UUID) -> InstructionsHistory:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.READ_PROJECT, not_found=_NO_PROJECT
            )
            records = await uow.model_instructions.list_for_project(project_id)
            return InstructionsHistory(tuple([await self._saved(uow, r) for r in records]))

    async def save(
        self,
        current: Current,
        project_id: UUID,
        instructions: ModelInstructions,
        based_on: int | None,
        request_id: str | None = None,
    ) -> SavedInstructions:
        """Store `instructions` as the next version, and audit it.

        `based_on` is the version the person edited, `None` if there was none. Raises
        `ConcurrentUpdateError` when it is no longer the latest, and `ValueError` when a
        text holds a term the project always masks.
        """
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.UPDATE_PROJECT, not_found=_NO_PROJECT
            )
            refuse_rule_terms(
                ((kind.value, instructions.text_for(kind)) for kind in InstructionsKind),
                await self._rules.rules_for(uow, project_id),
            )
            latest = await uow.model_instructions.latest(project_id)
            if (None if latest is None else latest.version) != based_on:
                raise ConcurrentUpdateError("model instructions changed since they were read")
            version = ModelInstructionsVersion(
                project_id=project_id,
                version=1 if latest is None else latest.version + 1,
                instructions=instructions,
                created_by=current.user.id,
                created_at=self._clock(),
            )
            if not await uow.model_instructions.add(await self._seal(uow, version)):
                raise ConcurrentUpdateError("model instructions changed while saving")
            await self._audit.record(
                uow,
                current.actor(request_id),
                "project.model_instructions_changed",
                "project",
                str(project_id),
                scope_id=project_id,
                details={"version": version.version},
            )
            await uow.commit()
        return SavedInstructions(version, current.user.display_name)

    async def in_force(
        self, uow: UnitOfWork, project_id: UUID, at: datetime
    ) -> ModelInstructionsVersion | None:
        """The version saved last at or before `at`, in the clear; `None` if none was yet.

        A run passes the moment it was started, so every call in it reads the same text.
        """
        record = await uow.model_instructions.in_force(project_id, at)
        return None if record is None else await self._open(uow, record)

    async def as_sent(
        self,
        version: ModelInstructionsVersion | None,
        kind: InstructionsKind,
        privacy: ProjectPrivacy,
    ) -> SentInstructions | None:
        """The text for `kind`, masked as the project chose; `None` if there is none.

        Masking may run a local model, so call it with no transaction open.
        """
        if version is None or not (text := version.instructions.text_for(kind)):
            return None
        masked = await mask_as_chosen(self._sanitizer, text, privacy)
        return SentInstructions(version.version, masked.masked)

    async def _seal(
        self, uow: UnitOfWork, version: ModelInstructionsVersion
    ) -> StoredModelInstructions:
        texts = {kind.value: version.instructions.text_for(kind) for kind in InstructionsKind}
        sealed = await self._encryption.encrypt(
            uow,
            _context(version.project_id, version.version),
            json.dumps(texts, separators=(",", ":"), sort_keys=True).encode(),
        )
        return StoredModelInstructions(
            project_id=version.project_id,
            version=version.version,
            instructions_sealed=sealed,
            created_by=version.created_by,
            created_at=version.created_at,
        )

    async def _open(
        self, uow: UnitOfWork, record: StoredModelInstructions
    ) -> ModelInstructionsVersion:
        texts = json.loads(
            await self._encryption.decrypt(
                uow, _context(record.project_id, record.version), record.instructions_sealed
            )
        )
        return ModelInstructionsVersion(
            project_id=record.project_id,
            version=record.version,
            instructions=ModelInstructions(
                screening=str(texts["screening"]), writing=str(texts["writing"])
            ),
            created_by=record.created_by,
            created_at=record.created_at,
        )

    async def _saved(
        self, uow: UnitOfWork, record: StoredModelInstructions
    ) -> SavedInstructions:
        author = await uow.users.get(record.created_by)
        name = _UNKNOWN_AUTHOR if author is None else author.display_name
        return SavedInstructions(await self._open(uow, record), name)
