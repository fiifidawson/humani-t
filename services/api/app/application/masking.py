"""Masking use cases: whether a project masks, its rules, the papers' mask store, the stage.

Finding spans belongs in `app/privacy/` and the masking rules in `domain/masking.py`.
Raw text is read only through `IngestedTextSource`, and only text masked as the project
now chooses leaves `PaperMaskStore`.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member
from app.domain.accounts import NotFoundError
from app.domain.discovery import IngestedTextSource
from app.domain.encryption import EncryptionContext
from app.domain.masking import (
    MASKING_OFF_SOURCE,
    MAX_MASKING_RULES_PER_PROJECT,
    DocumentAwaitingReview,
    DuplicateMaskingRuleError,
    EntityType,
    MaskedText,
    MaskingPolicy,
    MaskingRule,
    MaskingRuleRecord,
    PaperMask,
    ProjectPrivacy,
    is_masking_applied,
    is_unmasked_fingerprint,
    mask_rules_only,
    masking_fingerprint,
    normalize_masking_term,
    spans_to_json,
)
from app.domain.projects import Action
from app.domain.runs import GateDecision, GateKind, JsonValue
from app.privacy.base import Sanitizer
from app.runs.base import StageContext, StageFailedError
from app.storage.base import StorageProvider, UnitOfWork

# Table, column; the row is the rule or the paper.
_TERM_PURPOSE = "masking_rules.term"
_MASKED_PURPOSE = "paper_masks.masked_text"
_SPANS_PURPOSE = "paper_masks.spans"
_NO_PROJECT = "No such project."
_NO_RULE = "No such masking rule."

REVIEW_REJECTED = (
    "The privacy review was not approved, so the run stopped before any document "
    "was used. Correct the masking and start the run again."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _term_context(project_id: UUID, rule_id: UUID) -> EncryptionContext:
    return EncryptionContext(project_id, _TERM_PURPOSE, str(rule_id))


class MaskingChoice:
    """Whether a project's text is masked right now: its owner's switch under the policy.

    Asked each time text is about to leave (ADR 0014). A project that cannot be read
    counts as switched on, so a lookup that fails never unmasks anything.
    """

    def __init__(self, policy: MaskingPolicy) -> None:
        self._policy = policy

    async def is_masked(self, uow: UnitOfWork, project_id: UUID) -> bool:
        project = await uow.projects.get(project_id)
        is_on = project is None or project.is_masking_on
        return is_masking_applied(is_masking_on=is_on, policy=self._policy)


async def mask_as_chosen(
    sanitizer: Sanitizer, text: str, privacy: ProjectPrivacy
) -> MaskedText:
    """Mask with the sanitiser's detectors, or with the project's rules alone when off."""
    if privacy.is_masked:
        return await sanitizer.mask(text, rules=privacy.rules)
    return mask_rules_only(text, privacy.rules)


class MaskingRuleService:
    """Owner-only "always mask this term" rules, and the source that masking reads them from.

    A term is usually a person's name, so it is treated as raw text: owners
    only, sealed at rest, and never in the audit log.
    """

    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        choice: MaskingChoice,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._choice = choice
        self._clock = clock

    async def rules_for(self, uow: UnitOfWork, project_id: UUID) -> Sequence[MaskingRule]:
        """Every rule of the project in the clear, inside the caller's transaction."""
        return [
            MaskingRule(record.id, await self._term(uow, record), record.entity_type)
            for record in await uow.masking_rules.list_for_project(project_id)
        ]

    async def privacy_for(self, uow: UnitOfWork, project_id: UUID) -> ProjectPrivacy:
        """The project's rules and whether to mask, decided now, in the caller's transaction."""
        return ProjectPrivacy(
            tuple(await self.rules_for(uow, project_id)),
            await self._choice.is_masked(uow, project_id),
        )

    async def list(
        self, current: Current, project_id: UUID, request_id: str | None = None
    ) -> list[tuple[MaskingRuleRecord, str]]:
        """Each rule with its term. Every listing is audited: it shows raw text."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow, current.user.id, project_id, Action.VIEW_RAW_TEXT, not_found=_NO_PROJECT
            )
            records = await uow.masking_rules.list_for_project(project_id)
            listed = [(r, await self._term(uow, r)) for r in records]
            await self._audit.record(
                uow,
                current.actor(request_id),
                "masking_rule.listed",
                "project",
                str(project_id),
                scope_id=project_id,
            )
            await uow.commit()
        return listed

    async def add(
        self,
        current: Current,
        project_id: UUID,
        term: str,
        entity_type: EntityType,
        request_id: str | None = None,
    ) -> tuple[MaskingRuleRecord, str]:
        """Raise `ValueError` for an unusable term, `DuplicateMaskingRuleError` for a repeat."""
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow,
                current.user.id,
                project_id,
                Action.MANAGE_DOCUMENTS,
                not_found=_NO_PROJECT,
            )
            term = normalize_masking_term(term)
            # The project's row lock serialises adds, so two concurrent requests
            # cannot both pass the duplicate check (ADR-0002).
            await uow.projects.lock(project_id)
            existing = await self.rules_for(uow, project_id)
            if any(r.term.casefold() == term.casefold() for r in existing):
                raise DuplicateMaskingRuleError("This project already masks that term.")
            if len(existing) >= MAX_MASKING_RULES_PER_PROJECT:
                raise ValueError(
                    f"A project can have at most {MAX_MASKING_RULES_PER_PROJECT} masking rules"
                )
            rule_id = uuid.uuid4()
            record = MaskingRuleRecord(
                id=rule_id,
                project_id=project_id,
                term_sealed=await self._encryption.encrypt(
                    uow, _term_context(project_id, rule_id), term.encode()
                ),
                entity_type=entity_type,
                created_by=current.user.id,
                created_at=self._clock(),
            )
            await uow.masking_rules.add(record)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "masking_rule.created",
                "masking_rule",
                str(rule_id),
                scope_id=project_id,
            )
            await uow.commit()
        return record, term

    async def remove(
        self, current: Current, project_id: UUID, rule_id: UUID, request_id: str | None = None
    ) -> None:
        async with self._storage.unit_of_work() as uow:
            await require_member(
                uow,
                current.user.id,
                project_id,
                Action.MANAGE_DOCUMENTS,
                not_found=_NO_PROJECT,
            )
            record = await uow.masking_rules.get(rule_id)
            if record is None or record.project_id != project_id:
                raise NotFoundError(_NO_RULE)
            if not await uow.masking_rules.delete(rule_id):
                raise NotFoundError(_NO_RULE)
            await self._audit.record(
                uow,
                current.actor(request_id),
                "masking_rule.deleted",
                "masking_rule",
                str(rule_id),
                scope_id=project_id,
            )
            await uow.commit()

    async def _term(self, uow: UnitOfWork, record: MaskingRuleRecord) -> str:
        context = _term_context(record.project_id, record.id)
        return (await self._encryption.decrypt(uow, context, record.term_sealed)).decode()


class PaperMaskStore:
    """Papers' masked text, sealed and bound to the paper's row.

    It implements the pipeline's `MaskedTextSource`: the screen stage and the
    paper routes read masked text here, and nothing here can return raw text.
    """

    def __init__(
        self,
        encryption: ScopeEncryption,
        choice: MaskingChoice,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._encryption = encryption
        self._choice = choice
        self._clock = clock

    async def masked_text(
        self, uow: UnitOfWork, project_id: UUID, paper_id: UUID
    ) -> str | None:
        """The paper's masked text, or `None` if it is not masked as the project now needs.

        Text kept with masking off is withheld once masking applies again, until the
        mask stage masks it.
        """
        mask = await uow.paper_masks.get(paper_id)
        if mask is None or mask.project_id != project_id:
            return None
        if is_unmasked_fingerprint(mask.masked_with) and await self._choice.is_masked(
            uow, project_id
        ):
            return None
        context = EncryptionContext(project_id, _MASKED_PURPOSE, str(paper_id))
        return (await self._encryption.decrypt(uow, context, mask.masked_sealed)).decode()

    async def masked_with(self, uow: UnitOfWork, paper_id: UUID) -> tuple[str, ...] | None:
        """What the stored masking was made with, or `None` if there is none."""
        mask = await uow.paper_masks.get(paper_id)
        return None if mask is None else mask.masked_with

    async def save(
        self,
        uow: UnitOfWork,
        project_id: UUID,
        paper_id: UUID,
        masked: MaskedText,
        masked_with: tuple[str, ...],
        *,
        replaces: tuple[str, ...] | None,
    ) -> bool:
        """Store a new masking if the stored one is still `replaces`. False if it lost."""
        row = str(paper_id)
        mask = PaperMask(
            paper_id=paper_id,
            project_id=project_id,
            masked_sealed=await self._encryption.encrypt(
                uow, EncryptionContext(project_id, _MASKED_PURPOSE, row), masked.masked.encode()
            ),
            spans_sealed=await self._encryption.encrypt(
                uow,
                EncryptionContext(project_id, _SPANS_PURPOSE, row),
                json.dumps(spans_to_json(masked.spans)).encode(),
            ),
            masked_with=masked_with,
            masked_at=self._clock(),
        )
        return await uow.paper_masks.put(mask, replaces=replaces)


class DocumentsAwaitingReview(Protocol):
    """Uploaded documents whose masking no person has approved yet (P2: uploads)."""

    async def awaiting_review(self, project_id: UUID) -> tuple[DocumentAwaitingReview, ...]: ...


class MaskStage:
    """Mask every kept paper text of the run with the narrow list and the project's rules.

    A paper already masked with the same detectors and rules is skipped, so a rerun redoes
    nothing. Documents waiting for review stop the run at the privacy_review gate first.
    With the project's masking off, only the rules apply and there is no gate (ADR 0014).
    """

    def __init__(
        self,
        *,
        storage: StorageProvider,
        texts: IngestedTextSource[UnitOfWork],
        masks: PaperMaskStore,
        rules: MaskingRuleService,
        sanitizer: Sanitizer,
        detector_sources: Sequence[str],
        entity_types: Sequence[EntityType],
        documents: DocumentsAwaitingReview | None = None,
    ) -> None:
        self._storage = storage
        self._texts = texts
        self._masks = masks
        self._rules = rules
        self._sanitizer = sanitizer
        self._detector_sources = tuple(sorted(detector_sources))
        self._entity_types = sorted(t.value for t in entity_types)
        self._documents = documents

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._storage.unit_of_work() as uow:
            privacy = await self._rules.privacy_for(uow, context.project_id)
        if privacy.is_masked:
            await self._review_documents(context)
        async with self._storage.unit_of_work() as uow:
            texts = await self._texts.texts_for_run(uow, context.project_id, context.run_id)
        rules = privacy.rules
        detectors = self._detector_sources if privacy.is_masked else (MASKING_OFF_SOURCE,)
        fingerprint = masking_fingerprint(detectors, rules)
        masked_items: Counter[str] = Counter()
        masked = 0
        for done, text in enumerate(texts, start=1):
            await context.raise_if_cancelled()
            async with self._storage.unit_of_work() as uow:
                previous = await self._masks.masked_with(uow, text.paper_id)
            if previous != fingerprint:
                # Detection runs outside any transaction: a slow model must not
                # hold a database connection.
                result = await mask_as_chosen(self._sanitizer, text.text, privacy)
                async with self._storage.unit_of_work() as uow:
                    if await self._masks.save(
                        uow,
                        context.project_id,
                        text.paper_id,
                        result,
                        fingerprint,
                        replaces=previous,
                    ):
                        await uow.commit()
                        masked += 1
                        masked_items.update(result.counts)
            await context.progress(done, len(texts), f"Paper {done} of {len(texts)}")
        return {
            "papers": len(texts),
            "masked": masked,
            "skipped": len(texts) - masked,
            "masked_items": dict(sorted(masked_items.items())),
            "manifest": {
                "is_masked": privacy.is_masked,
                "detectors": list(detectors),
                "entity_types": [*self._entity_types] if privacy.is_masked else [],
                "masking_rules": len(rules),
            },
        }

    async def _review_documents(self, context: StageContext) -> None:
        if self._documents is None:
            return
        waiting = await self._documents.awaiting_review(context.project_id)
        if not waiting:
            return
        documents: list[JsonValue] = [
            {
                "document_id": str(d.document_id),
                "masked_items": {k.value: n for k, n in sorted(d.counts.items())},
            }
            for d in waiting
        ]
        resolution = await context.require_gate(
            GateKind.PRIVACY_REVIEW, {"documents": documents}
        )
        if resolution.decision is not GateDecision.APPROVE:
            raise StageFailedError(REVIEW_REJECTED)
