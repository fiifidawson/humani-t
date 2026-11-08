"""The search-planning conversation: from a review's question to an accepted plan.

Route A (architecture §4.3): the model asks clarifying questions (deep mode),
drafts themes, concepts and criteria, and redrafts on each change request,
until a person accepts a version. Route B: a person uploads their own terms,
parsed by code; the model is never called. The `queries` stage reads the
accepted plan (`accepted_plan`, the `PlanSource`) and asks for the vocabulary
gate through this module when there is none.

Every model call is masked first and goes through `GuardedInference`. Each use
case follows architecture §5.7: a short read, then masking and the model call
with no transaction open, then a short write that re-checks what it read. What
the model received is stored as a privacy receipt; everything stored is sealed
under the project's key; the audit log gets ids only.

What does not belong here: the rules themselves (`domain/plans.py`) and HTTP.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import structlog

from app.application.accounts import Current
from app.application.audit import SYSTEM, Actor, AuditLog
from app.application.encryption import ScopeEncryption
from app.application.inference import GuardedInference
from app.application.membership import require_member
from app.application.reviews import question_context
from app.domain.accounts import NotFoundError
from app.domain.encryption import EncryptionContext
from app.domain.inference import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    ChatRole,
    InferenceError,
    InvalidStructuredOutputError,
    PromptRefusedError,
)
from app.domain.masking import PLACEHOLDER, MaskingRule
from app.domain.plans import (
    AuthorKind,
    ChangeKind,
    ChangeRequest,
    ClarifyingQuestion,
    ConceptChange,
    Criterion,
    CriterionChange,
    CriterionKind,
    ModelCallPurpose,
    PlanAuthor,
    PlanConflictError,
    PlanConversation,
    PlanDiff,
    PlanMode,
    PrivacyReceipt,
    RedoStage,
    ReviewPlan,
    SentText,
    SentTextKind,
    StoredConversation,
    StoredPlanVersion,
    StoredReceipt,
    TermChange,
    TermKey,
    VocabularyFormat,
    diff_plans,
    first_plan,
    first_stage_to_redo,
    number_criteria,
    parse_strict_vocabulary,
    revision_from_gate,
    term_authors,
)
from app.domain.projects import Action
from app.domain.reviews import Review, ReviewStatus, ReviewTrigger
from app.domain.runs import GateResolution
from app.domain.vocabulary import Concept, VocabularyOrigin
from app.privacy.base import Sanitizer
from app.storage.base import StorageProvider, UnitOfWork

CLARIFY_PROMPT_VERSION = "plan.clarify@1"
DRAFT_PROMPT_VERSION = "plan.draft@1"
REVISE_PROMPT_VERSION = "plan.revise@1"

_MASKING_NOTE = (
    "Text in square brackets, such as [PERSON_1] or [LOCATION_2], replaces personal "
    "details that were masked. Never copy a placeholder into a search term."
)

CLARIFY_PROMPT = (
    "You help a humanitarian team plan a literature search. Read their question and ask "
    "the 2 to 4 short questions whose answers would most change which papers to look for "
    "(outcomes, populations, settings, study types, years). Offer up to 5 short suggested "
    "answers per question. " + _MASKING_NOTE
)

DRAFT_PROMPT = (
    "You plan a systematic literature search for a humanitarian decision. From the question "
    "and any answers, propose 2 to 5 themes. A paper must match every theme (AND); the "
    "concepts in a theme are alternatives (OR), and each concept lists synonyms and "
    "near-synonyms used in academic writing. Also propose inclusion and exclusion criteria "
    "for screening. Use plain search terms, not Boolean syntax. " + _MASKING_NOTE
)

REVISE_PROMPT = (
    "You revise a literature search plan. Apply the team's change request to the current "
    "plan and return the whole revised plan. Change only what the request asks for. "
    + _MASKING_NOTE
)

_TEXT = {"type": "string", "minLength": 1, "maxLength": 300}
_TERM = {"type": "string", "minLength": 1, "maxLength": 120}

_log = structlog.get_logger(__name__)

CLARIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "suggestions"],
                "properties": {
                    "text": _TEXT,
                    "suggestions": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                },
            },
        }
    },
}

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["themes", "criteria"],
    "properties": {
        "themes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "concepts"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "concepts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["term", "synonyms"],
                            "properties": {
                                "term": _TERM,
                                "synonyms": {"type": "array", "maxItems": 12, "items": _TERM},
                            },
                        },
                    },
                },
            },
        },
        "criteria": {
            "type": "object",
            "additionalProperties": False,
            "required": ["inclusion", "exclusion"],
            "properties": {
                "inclusion": {"type": "array", "minItems": 1, "maxItems": 8, "items": _TEXT},
                "exclusion": {"type": "array", "maxItems": 8, "items": _TEXT},
            },
        },
    },
}

# Each bound to its table, column and row (architecture §5.4).
_CONVERSATION_PURPOSE = "plan_conversations.body"
_VERSION_PURPOSE = "plan_versions.body"
_RECEIPT_PURPOSE = "plan_receipts.body"

_NOT_FOUND = "No such review."
_NO_VERSION = "No such plan version."
_DURING_RUN = "The plan cannot change while a run is going. Wait for it, or cancel it."
_MOVED_ON = "The draft changed while you were looking at it. Showing the latest."
_ALREADY_ACCEPTED = "Someone else already accepted this draft. Showing the latest."
_STRICT = (
    "Your own search terms are used exactly as given, so the model cannot change them. "
    "Upload a new list instead."
)

_AT_REST = frozenset(
    {ReviewStatus.ACCEPTED, ReviewStatus.DONE, ReviewStatus.FAILED, ReviewStatus.CANCELLED}
)
_RUN_GOING = frozenset({ReviewStatus.RUNNING, ReviewStatus.WAITING})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MaskingRuleSource(Protocol):
    """The project's "always mask this term" rules, read inside the caller's transaction."""

    async def rules_for(self, uow: UnitOfWork, project_id: UUID) -> Sequence[MaskingRule]: ...


class _NoProjectRules:
    async def rules_for(self, uow: UnitOfWork, project_id: UUID) -> Sequence[MaskingRule]:
        return ()


NO_PROJECT_RULES: MaskingRuleSource = _NoProjectRules()
"""No project rules: the sanitiser's detectors alone. For deployments without a rule store."""


@dataclass(frozen=True, slots=True)
class PlanView:
    """What the planning screens show for one review (UI_UX_SPEC §5.3.2 to 5.3.4, §6.5)."""

    review_id: UUID
    status: ReviewStatus
    mode: PlanMode | None
    questions: tuple[ClarifyingQuestion, ...]
    pending_change: ChangeRequest | None
    versions: tuple[ReviewPlan, ...]
    term_authors: Mapping[TermKey, PlanAuthor]
    receipts: tuple[PrivacyReceipt, ...]

    @property
    def current(self) -> ReviewPlan | None:
        return self.versions[-1] if self.versions else None

    @property
    def accepted_version(self) -> int | None:
        accepted = [p.version for p in self.versions if p.is_accepted]
        return accepted[-1] if accepted else None

    @property
    def rerun_from(self) -> RedoStage | None:
        """Where a run of the current version starts, against the version accepted before it.

        `queries` when none was accepted before: a first run does everything.
        """
        current = self.current
        if current is None:
            return None
        before = [p for p in self.versions if p.is_accepted and p.version < current.version]
        if not before:
            return "queries"
        return first_stage_to_redo(before[-1], current)


@dataclass(frozen=True, slots=True)
class PlanVersionView:
    plan: ReviewPlan
    diff: PlanDiff
    term_authors: Mapping[TermKey, PlanAuthor]


@dataclass(frozen=True, slots=True)
class _ReceiptLinks:
    plan_version: int | None
    change_request_id: UUID | None


@dataclass(frozen=True, slots=True)
class _Masked:
    question: SentText
    answers: tuple[tuple[ClarifyingQuestion, SentText], ...] = ()
    change: SentText | None = None

    @property
    def items(self) -> tuple[SentText, ...]:
        extra = () if self.change is None else (self.change,)
        return (self.question, *(sent for _, sent in self.answers), *extra)


class PlanService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        inference: GuardedInference,
        sanitizer: Sanitizer,
        rules: MaskingRuleSource,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._inference = inference
        self._sanitizer = sanitizer
        self._rules = rules
        self._clock = clock

    async def authorize_review(self, current: Current, review_id: UUID, action: Action) -> None:
        """Check access without acting, so the HTTP layer refuses before reading a body."""
        async with self._storage.unit_of_work() as uow:
            await self._review(uow, current, review_id, action)

    async def get(self, current: Current, review_id: UUID) -> PlanView:
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.READ_PROJECT)
            return await self._view(uow, review)

    async def version(self, current: Current, review_id: UUID, number: int) -> PlanVersionView:
        """One version, its diff against the one before, and who added each of its terms."""
        async with self._storage.unit_of_work() as uow:
            await self._review(uow, current, review_id, Action.READ_PROJECT)
            records = [
                r for r in await uow.plans.list_versions(review_id) if r.version <= number
            ]
            if not records or records[-1].version != number:
                raise NotFoundError(_NO_VERSION)
            opened = [await self.open_version(uow, r) for r in records]
        plan, diff = opened[-1]
        return PlanVersionView(plan, diff, term_authors([p for p, _ in opened]))

    async def start(
        self, current: Current, review_id: UUID, mode: PlanMode, request_id: str | None = None
    ) -> PlanView:
        """Ask clarifying questions (deep), or draft at once (shallow)."""
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status is not ReviewStatus.NEW or await uow.plans.get_conversation(
                review_id
            ):
                raise PlanConflictError("This review already has a search plan. Showing it.")
            question, rules = await self._question_and_rules(uow, review)
        masked = _Masked(await self._mask(SentTextKind.QUESTION, question, rules))
        now = self._clock()
        conversation = PlanConversation(
            review.id, review.project_id, mode, now, current.user.id
        )
        plan = None
        if mode is PlanMode.DEEP:
            result = await self._ask(review, ModelCallPurpose.CLARIFY, masked, None, current)
            conversation = replace(conversation, questions=_questions_from(result.output))
            trigger = ReviewTrigger.CLARIFY
        else:
            result = await self._ask(review, ModelCallPurpose.DRAFT, masked, None, current)
            plan = _model_plan(review.id, None, result, DRAFT_PROMPT_VERSION, now)
            trigger = ReviewTrigger.DRAFT
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status is not ReviewStatus.NEW:
                raise PlanConflictError("This review already has a search plan. Showing it.")
            await uow.plans.add_conversation(await self._seal_conversation(uow, conversation))
            if plan is not None:
                await self._add_version(uow, review, plan, None)
            await uow.reviews.save(review.transitioned(trigger))
            await self._record(uow, current.actor(request_id), "plan.started", review)
            await uow.commit()
        return await self.get(current, review_id)

    async def answer(
        self,
        current: Current,
        review_id: UUID,
        answers: Mapping[str, str | None],
        *,
        draft_now: bool = False,
        request_id: str | None = None,
    ) -> PlanView:
        """Record answers (`None` skips); once every question is settled, draft the plan."""
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            stored = await uow.plans.get_conversation(review_id)
            if review.status is not ReviewStatus.CLARIFYING or stored is None:
                raise PlanConflictError("This review is not waiting for answers.")
            conversation = (await self._open_conversation(uow, stored)).with_answers(
                answers, current.user.id, now
            )
            if draft_now:
                conversation = conversation.with_rest_skipped(current.user.id, now)
            await uow.plans.save_conversation(
                await self._seal_conversation(uow, conversation, stored.version)
            )
            await self._record(uow, current.actor(request_id), "plan.answered", review)
            question, rules = await self._question_and_rules(uow, review)
            await uow.commit()
        if not conversation.is_ready_to_draft:
            return await self.get(current, review_id)
        answered = [
            (q, await self._mask(SentTextKind.ANSWER, q.answer, rules, ref=q.id))
            for q in conversation.questions
            if q.answer is not None
        ]
        masked = _Masked(
            await self._mask(SentTextKind.QUESTION, question, rules), tuple(answered)
        )
        result = await self._ask(review, ModelCallPurpose.DRAFT, masked, None, current)
        plan = _model_plan(review.id, None, result, DRAFT_PROMPT_VERSION, self._clock())
        plan = replace(plan, clarifications=conversation.questions)
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status is not ReviewStatus.CLARIFYING:
                raise PlanConflictError(_MOVED_ON)
            await self._add_version(uow, review, plan, None)
            await uow.reviews.save(review.transitioned(ReviewTrigger.DRAFT))
            await self._record(uow, current.actor(request_id), "plan.drafted", review)
            await uow.commit()
        return await self.get(current, review_id)

    async def request_changes(
        self,
        current: Current,
        review_id: UUID,
        text: str,
        based_on_version: int,
        request_id: str | None = None,
    ) -> PlanView:
        """Ask the model for the next version. The request is recorded before the model runs."""
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status in _RUN_GOING:
                raise PlanConflictError(_DURING_RUN)
            latest = await self._latest(uow, review_id)
            if latest is None or review.status in (ReviewStatus.NEW, ReviewStatus.CLARIFYING):
                raise PlanConflictError("There is no draft to change yet.")
            if latest.version != based_on_version:
                raise PlanConflictError(_MOVED_ON)
            if latest.vocabulary.is_strict:
                raise PlanConflictError(_STRICT)
            change = ChangeRequest(uuid.uuid4(), text, current.user.id, now, based_on_version)
            conversation, version = await self._conversation(uow, review, current, now)
            await self._put_conversation(uow, conversation.with_pending_change(change), version)
            if review.status is not ReviewStatus.CHANGES_REQUESTED:
                await uow.reviews.save(review.transitioned(ReviewTrigger.REQUEST_CHANGES))
            await self._record(uow, current.actor(request_id), "plan.changes_requested", review)
            question, rules = await self._question_and_rules(uow, review)
            await uow.commit()
        masked = _Masked(
            await self._mask(SentTextKind.QUESTION, question, rules),
            change=await self._mask(SentTextKind.CHANGE_REQUEST, change.text, rules),
        )
        result = await self._ask(
            review, ModelCallPurpose.REVISE, masked, latest, current, change.id
        )
        concepts, criteria, notes = _draft_from(result.output)
        plan = latest.followed_by(
            concepts=concepts,
            criteria=criteria,
            origin=VocabularyOrigin.LLM_ASSISTED,
            author=PlanAuthor.model(),
            at=self._clock(),
            change_request=change,
            notes=notes,
            model_id=result.model_id,
            prompt_version=REVISE_PROMPT_VERSION,
        )
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            stored = await uow.plans.get_conversation(review_id)
            pending = None if stored is None else await self._open_conversation(uow, stored)
            if (
                review.status is not ReviewStatus.CHANGES_REQUESTED
                or stored is None
                or pending is None
                or pending.pending_change is None
                or pending.pending_change.id != change.id
            ):
                raise PlanConflictError(_MOVED_ON)
            await self._add_version(uow, review, plan, latest)
            await self._put_conversation(uow, pending.with_pending_change(None), stored.version)
            await uow.reviews.save(review.transitioned(ReviewTrigger.DRAFT))
            await self._record(uow, current.actor(request_id), "plan.revised", review)
            await uow.commit()
        return await self.get(current, review_id)

    async def accept(
        self, current: Current, review_id: UUID, version: int, request_id: str | None = None
    ) -> PlanView:
        """Accept the latest draft and freeze its vocabulary. The first accept wins."""
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status in _RUN_GOING:
                raise PlanConflictError(_DURING_RUN)
            latest = await uow.plans.latest_version(review_id)
            if latest is not None and latest.version == version and latest.is_accepted:
                raise PlanConflictError(_ALREADY_ACCEPTED)
            if latest is None or latest.version != version:
                raise PlanConflictError(_MOVED_ON)
            if review.status is not ReviewStatus.DRAFTING:
                raise PlanConflictError("A change is being drafted. Wait for the new draft.")
            if not await uow.plans.accept(review_id, version, current.user.id, self._clock()):
                raise PlanConflictError(_ALREADY_ACCEPTED)
            await uow.reviews.save(review.transitioned(ReviewTrigger.ACCEPT))
            await self._record(uow, current.actor(request_id), "plan.accepted", review)
            await uow.commit()
        return await self.get(current, review_id)

    async def upload_vocabulary(
        self,
        current: Current,
        review_id: UUID,
        content: str,
        format: VocabularyFormat,
        criteria: Sequence[tuple[CriterionKind, str]] | None = None,
        request_id: str | None = None,
    ) -> PlanView:
        """Route B: the person's own terms become the next version, parsed by code alone.

        No model and no sanitiser: nothing here leaves the deployment. Without
        `criteria`, the previous version's criteria carry over.
        """
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            review = await self._review(uow, current, review_id, Action.EDIT_REVIEW)
            if review.status in _RUN_GOING:
                raise PlanConflictError(_DURING_RUN)
            concepts = parse_strict_vocabulary(content, format)
            latest = await self._latest(uow, review_id)
            kept = () if latest is None else latest.criteria
            chosen = kept if criteria is None else number_criteria(criteria)
            fields: dict[str, Any] = {
                "concepts": concepts,
                "criteria": chosen,
                "origin": VocabularyOrigin.USER_SUPPLIED,
                "author": PlanAuthor.person(current.user.id),
                "at": now,
            }
            plan = (
                first_plan(review.id, **fields)
                if latest is None
                else latest.followed_by(**fields)
            )
            conversation, version = await self._conversation(uow, review, current, now)
            await self._put_conversation(uow, conversation.with_pending_change(None), version)
            await self._add_version(uow, review, plan, latest)
            for trigger in _to_drafting(review.status):
                review = await uow.reviews.save(review.transitioned(trigger))
            await self._record(
                uow, current.actor(request_id), "plan.vocabulary_uploaded", review
            )
            await uow.commit()
        return await self.get(current, review_id)

    async def accepted_plan(self, uow: UnitOfWork, review_id: UUID) -> ReviewPlan | None:
        """`PlanSource`: the most recently accepted version, or `None`."""
        record = await uow.plans.latest_accepted(review_id)
        return None if record is None else (await self.open_version(uow, record))[0]

    async def plan_for_run(self, review_id: UUID) -> ReviewPlan | None:
        """The latest version, accepted or not, for a run's `queries` stage."""
        async with self._storage.unit_of_work() as uow:
            return await self._latest(uow, review_id)

    async def draft_for_run(self, review_id: UUID) -> ReviewPlan:
        """Draft a first version for a run started before any plan existed.

        Safe to repeat: a version stored meanwhile is returned instead.
        """
        async with self._storage.unit_of_work() as uow:
            review = await uow.reviews.get(review_id)
            if review is None:
                raise NotFoundError(_NOT_FOUND)
            latest = await self._latest(uow, review_id)
            if latest is not None:
                return latest
            question, rules = await self._question_and_rules(uow, review)
        masked = _Masked(await self._mask(SentTextKind.QUESTION, question, rules))
        result = await self._ask(review, ModelCallPurpose.DRAFT, masked, None, None)
        now = self._clock()
        plan = _model_plan(review.id, None, result, DRAFT_PROMPT_VERSION, now)
        async with self._storage.unit_of_work() as uow:
            if await uow.plans.get_conversation(review_id) is None:
                conversation = PlanConversation(
                    review.id, review.project_id, PlanMode.SHALLOW, now, None
                )
                await uow.plans.add_conversation(
                    await self._seal_conversation(uow, conversation)
                )
            await self._add_version(uow, review, plan, None)
            await self._record(uow, SYSTEM, "plan.drafted", review)
            await uow.commit()
        return plan

    async def accept_at_gate(
        self, review_id: UUID, version: int, resolution: GateResolution
    ) -> ReviewPlan:
        """Apply an approved vocabulary gate through the same conditional accept.

        Revisions become a version written by the person who decided, which is
        then accepted. Safe to repeat: an accepted latest version is returned.
        """
        edit = revision_from_gate(resolution.revisions) if resolution.revisions else None
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            review = await uow.reviews.get(review_id)
            latest = await self._latest(uow, review_id)
            if review is None or latest is None:
                raise NotFoundError(_NOT_FOUND)
            if latest.is_accepted:
                return latest
            if latest.version != version:
                raise PlanConflictError(_MOVED_ON)
            target = latest
            if edit is not None:
                concepts, criteria = edit
                target = latest.followed_by(
                    concepts=latest.vocabulary.concepts if concepts is None else concepts,
                    criteria=latest.criteria if criteria is None else criteria,
                    origin=latest.vocabulary.origin,
                    author=PlanAuthor.person(resolution.resolved_by),
                    at=now,
                )
                await self._add_version(uow, review, target, latest)
            if not await uow.plans.accept(
                review_id, target.version, resolution.resolved_by, now
            ):
                raise PlanConflictError(_ALREADY_ACCEPTED)
            await self._record(uow, Actor(resolution.resolved_by), "plan.accepted", review)
            await uow.commit()
        return target.accepted(resolution.resolved_by, now)

    async def open_version(
        self, uow: UnitOfWork, record: StoredPlanVersion
    ) -> tuple[ReviewPlan, PlanDiff]:
        """Decrypt a stored version and the diff stored with it."""
        body = json.loads(
            await self._encryption.decrypt(uow, _version_context(record), record.body_sealed)
        )
        plan = first_plan(
            record.review_id,
            concepts=tuple(_concept(c) for c in body["concepts"]),
            criteria=tuple(_criterion(c) for c in body["criteria"]),
            origin=record.origin,
            author=record.author,
            at=record.created_at,
            version=record.version,
            clarifications=tuple(_question(q) for q in body["clarifications"]),
            change_request=_change(body["change_request"]),
            notes=tuple(body["notes"]),
            model_id=body["model_id"],
            prompt_version=body["prompt_version"],
        )
        if record.accepted_at is not None and record.accepted_by is not None:
            plan = plan.accepted(record.accepted_by, record.accepted_at)
        return plan, _diff_from_json(body["diff"])

    async def _review(
        self, uow: UnitOfWork, current: Current, review_id: UUID, action: Action
    ) -> Review:
        review = await uow.reviews.get(review_id)
        if review is None:
            raise NotFoundError(_NOT_FOUND)
        await require_member(
            uow, current.user.id, review.project_id, action, not_found=_NOT_FOUND
        )
        return review

    async def _view(self, uow: UnitOfWork, review: Review) -> PlanView:
        stored = await uow.plans.get_conversation(review.id)
        conversation = None if stored is None else await self._open_conversation(uow, stored)
        versions = tuple(
            [
                (await self.open_version(uow, r))[0]
                for r in await uow.plans.list_versions(review.id)
            ]
        )
        receipts = tuple(
            [await self._open_receipt(uow, r) for r in await uow.plans.list_receipts(review.id)]
        )
        return PlanView(
            review_id=review.id,
            status=review.status,
            mode=None if conversation is None else conversation.mode,
            questions=() if conversation is None else conversation.questions,
            pending_change=None if conversation is None else conversation.pending_change,
            versions=versions,
            term_authors=term_authors(versions),
            receipts=receipts,
        )

    async def _latest(self, uow: UnitOfWork, review_id: UUID) -> ReviewPlan | None:
        record = await uow.plans.latest_version(review_id)
        return None if record is None else (await self.open_version(uow, record))[0]

    async def _question_and_rules(
        self, uow: UnitOfWork, review: Review
    ) -> tuple[str, Sequence[MaskingRule]]:
        question = await self._encryption.decrypt(
            uow, question_context(review.project_id, review.id), review.question_sealed
        )
        return question.decode(), await self._rules.rules_for(uow, review.project_id)

    async def _mask(
        self,
        kind: SentTextKind,
        text: str,
        rules: Sequence[MaskingRule],
        *,
        ref: str | None = None,
    ) -> SentText:
        masked = await self._sanitizer.mask(text, rules=rules)
        return SentText(kind, masked.masked, dict(masked.counts), ref)

    async def _ask(
        self,
        review: Review,
        purpose: ModelCallPurpose,
        masked: _Masked,
        current_plan: ReviewPlan | None,
        current: Current | None,
        change_request_id: UUID | None = None,
    ) -> ChatResult:
        """Call the model with masked text only, and store a receipt of what it received.

        The receipt names the version being drafted (the one after `current_plan`;
        none for clarifying questions) and the change request behind a revision.
        """
        system, schema, prompt_version = _PROMPTS[purpose]
        request = ChatRequest(
            messages=(
                ChatMessage(ChatRole.SYSTEM, system),
                ChatMessage(ChatRole.USER, _user_message(masked, current_plan)),
            ),
            output_schema=schema,
        )
        by = None if current is None else current.user.id
        drafting = None
        if purpose is not ModelCallPurpose.CLARIFY:
            drafting = 1 if current_plan is None else current_plan.version + 1
        links = _ReceiptLinks(drafting, change_request_id)
        # A reasoning model's answers vary, so an unusable one is asked for again
        # once. Each attempt sent the text, so each leaves its own receipt.
        for attempt in (1, 2):
            try:
                result = await self._inference.chat(request)
            except PromptRefusedError:
                raise
            except InferenceError as exc:
                await self._store_receipt(
                    review,
                    purpose,
                    prompt_version,
                    masked,
                    by,
                    self._inference.model_id,
                    False,
                    links,
                )
                if not isinstance(exc, InvalidStructuredOutputError):
                    raise
                # The reason names a schema path and rule, never the text.
                _log.warning(
                    "plan_answer_refused",
                    review_id=str(review.id),
                    purpose=purpose.value,
                    attempt=attempt,
                    reason=str(exc),
                )
                if attempt == 2:
                    raise
                continue
            await self._store_receipt(
                review, purpose, prompt_version, masked, by, result.model_id, True, links
            )
            return result
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def _store_receipt(
        self,
        review: Review,
        purpose: ModelCallPurpose,
        prompt_version: str,
        masked: _Masked,
        by: UUID | None,
        model_id: str,
        is_delivered: bool,
        links: _ReceiptLinks,
    ) -> None:
        receipt_id = uuid.uuid4()
        body = [
            {"kind": i.kind.value, "sent": i.sent, "counts": dict(i.counts), "ref": i.ref}
            for i in masked.items
        ]
        async with self._storage.unit_of_work() as uow:
            await uow.plans.add_receipt(
                StoredReceipt(
                    id=receipt_id,
                    review_id=review.id,
                    project_id=review.project_id,
                    purpose=purpose,
                    created_at=self._clock(),
                    created_by=by,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    is_delivered=is_delivered,
                    body_sealed=await self._encryption.encrypt(
                        uow,
                        EncryptionContext(review.project_id, _RECEIPT_PURPOSE, str(receipt_id)),
                        _dumps(body),
                    ),
                    plan_version=links.plan_version,
                    change_request_id=links.change_request_id,
                )
            )
            await uow.commit()

    async def _open_receipt(self, uow: UnitOfWork, record: StoredReceipt) -> PrivacyReceipt:
        context = EncryptionContext(record.project_id, _RECEIPT_PURPOSE, str(record.id))
        body = json.loads(await self._encryption.decrypt(uow, context, record.body_sealed))
        return PrivacyReceipt(
            id=record.id,
            review_id=record.review_id,
            project_id=record.project_id,
            purpose=record.purpose,
            created_at=record.created_at,
            created_by=record.created_by,
            model_id=record.model_id,
            prompt_version=record.prompt_version,
            is_delivered=record.is_delivered,
            items=tuple(
                SentText(SentTextKind(i["kind"]), i["sent"], i["counts"], i["ref"])
                for i in body
            ),
            plan_version=record.plan_version,
            change_request_id=record.change_request_id,
        )

    async def _add_version(
        self, uow: UnitOfWork, review: Review, plan: ReviewPlan, previous: ReviewPlan | None
    ) -> None:
        record = StoredPlanVersion(
            review_id=review.id,
            project_id=review.project_id,
            version=plan.version,
            origin=plan.vocabulary.origin,
            author=plan.author,
            created_at=plan.created_at,
            body_sealed=b"pending",
        )
        body = _version_body(plan, diff_plans(previous, plan))
        sealed = await self._encryption.encrypt(uow, _version_context(record), _dumps(body))
        await uow.plans.add_version(replace(record, body_sealed=sealed))

    async def _conversation(
        self, uow: UnitOfWork, review: Review, current: Current, now: datetime
    ) -> tuple[PlanConversation, int | None]:
        """The review's conversation and stored version; a new one and `None` if it has none."""
        stored = await uow.plans.get_conversation(review.id)
        if stored is None:
            fresh = PlanConversation(
                review.id, review.project_id, PlanMode.SHALLOW, now, current.user.id
            )
            return fresh, None
        return await self._open_conversation(uow, stored), stored.version

    async def _put_conversation(
        self, uow: UnitOfWork, conversation: PlanConversation, version: int | None
    ) -> None:
        if version is None:
            await uow.plans.add_conversation(await self._seal_conversation(uow, conversation))
        else:
            await uow.plans.save_conversation(
                await self._seal_conversation(uow, conversation, version)
            )

    async def _seal_conversation(
        self, uow: UnitOfWork, conversation: PlanConversation, version: int = 0
    ) -> StoredConversation:
        body = {
            "questions": [_question_json(q) for q in conversation.questions],
            "pending_change": _change_json(conversation.pending_change),
        }
        context = EncryptionContext(
            conversation.project_id, _CONVERSATION_PURPOSE, str(conversation.review_id)
        )
        return StoredConversation(
            review_id=conversation.review_id,
            project_id=conversation.project_id,
            mode=conversation.mode,
            created_at=conversation.created_at,
            created_by=conversation.created_by,
            body_sealed=await self._encryption.encrypt(uow, context, _dumps(body)),
            version=version,
        )

    async def _open_conversation(
        self, uow: UnitOfWork, stored: StoredConversation
    ) -> PlanConversation:
        context = EncryptionContext(
            stored.project_id, _CONVERSATION_PURPOSE, str(stored.review_id)
        )
        body = json.loads(await self._encryption.decrypt(uow, context, stored.body_sealed))
        return PlanConversation(
            review_id=stored.review_id,
            project_id=stored.project_id,
            mode=stored.mode,
            created_at=stored.created_at,
            created_by=stored.created_by,
            questions=tuple(_question(q) for q in body["questions"]),
            pending_change=_change(body["pending_change"]),
            version=stored.version,
        )

    async def _record(self, uow: UnitOfWork, actor: Actor, action: str, review: Review) -> None:
        await self._audit.record(
            uow, actor, action, "review", str(review.id), scope_id=review.project_id
        )


_PROMPTS: Mapping[ModelCallPurpose, tuple[str, Mapping[str, Any], str]] = {
    ModelCallPurpose.CLARIFY: (CLARIFY_PROMPT, CLARIFY_SCHEMA, CLARIFY_PROMPT_VERSION),
    ModelCallPurpose.DRAFT: (DRAFT_PROMPT, DRAFT_SCHEMA, DRAFT_PROMPT_VERSION),
    ModelCallPurpose.REVISE: (REVISE_PROMPT, DRAFT_SCHEMA, REVISE_PROMPT_VERSION),
}


def _to_drafting(status: ReviewStatus) -> tuple[ReviewTrigger, ...]:
    """The triggers that bring a review at `status` to drafting a new version."""
    if status is ReviewStatus.DRAFTING:
        return ()
    if status in _AT_REST:
        return (ReviewTrigger.REQUEST_CHANGES, ReviewTrigger.DRAFT)
    return (ReviewTrigger.DRAFT,)


def _user_message(masked: _Masked, current_plan: ReviewPlan | None) -> str:
    lines = [f"Question: {masked.question.sent}"]
    if masked.answers:
        lines.append("Answers to clarifying questions:")
        lines += [f"- {q.text} {sent.sent}" for q, sent in masked.answers]
    if current_plan is not None:
        lines.append("Current plan (JSON): " + json.dumps(_draft_json(current_plan)))
    if masked.change is not None:
        lines.append(f"Change request: {masked.change.sent}")
    return "\n".join(lines)


def _draft_json(plan: ReviewPlan) -> dict[str, Any]:
    themes = [
        {
            "name": theme,
            "concepts": [
                {"term": c.term, "synonyms": list(c.synonyms)}
                for c in plan.vocabulary.concepts_in(theme)
            ],
        }
        for theme in plan.vocabulary.themes
    ]
    by_kind = {
        kind.value: [c.text for c in plan.criteria if c.kind is kind] for kind in CriterionKind
    }
    return {"themes": themes, "criteria": by_kind}


def _questions_from(output: Mapping[str, Any]) -> tuple[ClarifyingQuestion, ...]:
    return tuple(
        ClarifyingQuestion(
            f"q{n}", q["text"].strip(), tuple(s.strip() for s in q["suggestions"])
        )
        for n, q in enumerate(output["questions"], start=1)
    )


def _model_plan(
    review_id: UUID,
    previous: ReviewPlan | None,
    result: ChatResult,
    prompt_version: str,
    at: datetime,
) -> ReviewPlan:
    concepts, criteria, notes = _draft_from(result.output)
    return first_plan(
        review_id,
        concepts=concepts,
        criteria=criteria,
        origin=VocabularyOrigin.LLM_ASSISTED,
        author=PlanAuthor.model(),
        at=at,
        version=1 if previous is None else previous.version + 1,
        notes=notes,
        model_id=result.model_id,
        prompt_version=prompt_version,
    )


def _draft_from(
    output: Mapping[str, Any],
) -> tuple[tuple[Concept, ...], tuple[Criterion, ...], tuple[str, ...]]:
    """Concepts and criteria from a schema-valid draft, and notes on what was changed.

    A term holding a masked placeholder is dropped: it would search for
    "[LOCATION_1]", and the plan's terms reach the paper sources.
    """
    concepts: list[Concept] = []
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()
    for theme in output["themes"]:
        name = theme["name"].strip()
        for item in theme["concepts"]:
            terms = [t.strip() for t in (item["term"], *item["synonyms"])]
            kept = [t for t in terms if t and not PLACEHOLDER.search(t)]
            if len(kept) < len(terms):
                notes.append(f"{len(terms) - len(kept)} term(s) in '{name}' held masked text")
            if not kept:
                continue
            key = (name.casefold(), kept[0].casefold())
            if key in seen:
                notes.append(f"'{kept[0]}' appeared twice in '{name}'; kept once")
                continue
            seen.add(key)
            concepts.append(Concept(kept[0], tuple(kept[1:]), name))
    if not concepts:
        raise InvalidStructuredOutputError("the draft has no usable search terms")
    criteria = number_criteria(
        [(CriterionKind.INCLUSION, t) for t in output["criteria"]["inclusion"]]
        + [(CriterionKind.EXCLUSION, t) for t in output["criteria"]["exclusion"]]
    )
    return tuple(concepts), criteria, tuple(notes)


def _dumps(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _version_context(record: StoredPlanVersion) -> EncryptionContext:
    return EncryptionContext(
        record.project_id, _VERSION_PURPOSE, f"{record.review_id}:{record.version}"
    )


def _author_json(author: PlanAuthor) -> dict[str, Any]:
    return {
        "kind": author.kind.value,
        "user_id": None if author.user_id is None else str(author.user_id),
    }


def _author(value: Mapping[str, Any]) -> PlanAuthor:
    user_id = value["user_id"]
    return PlanAuthor(AuthorKind(value["kind"]), None if user_id is None else UUID(user_id))


def _question_json(q: ClarifyingQuestion) -> dict[str, Any]:
    return {
        "id": q.id,
        "text": q.text,
        "suggestions": list(q.suggestions),
        "answer": q.answer,
        "skipped": q.is_skipped,
        "answered_by": None if q.answered_by is None else str(q.answered_by),
        "answered_at": None if q.answered_at is None else q.answered_at.isoformat(),
    }


def _question(value: Mapping[str, Any]) -> ClarifyingQuestion:
    return ClarifyingQuestion(
        id=value["id"],
        text=value["text"],
        suggestions=tuple(value["suggestions"]),
        answer=value["answer"],
        is_skipped=value["skipped"],
        answered_by=None if value["answered_by"] is None else UUID(value["answered_by"]),
        answered_at=(
            None
            if value["answered_at"] is None
            else datetime.fromisoformat(value["answered_at"])
        ),
    )


def _change_json(change: ChangeRequest | None) -> dict[str, Any] | None:
    if change is None:
        return None
    return {
        "id": str(change.id),
        "text": change.text,
        "requested_by": str(change.requested_by),
        "requested_at": change.requested_at.isoformat(),
        "based_on_version": change.based_on_version,
    }


def _change(value: Mapping[str, Any] | None) -> ChangeRequest | None:
    if value is None:
        return None
    return ChangeRequest(
        id=UUID(value["id"]),
        text=value["text"],
        requested_by=UUID(value["requested_by"]),
        requested_at=datetime.fromisoformat(value["requested_at"]),
        based_on_version=value["based_on_version"],
    )


def _concept(value: Mapping[str, Any]) -> Concept:
    return Concept(value["term"], tuple(value["synonyms"]), value["theme"])


def _criterion(value: Mapping[str, Any]) -> Criterion:
    return Criterion(value["id"], CriterionKind(value["kind"]), value["text"])


def _version_body(plan: ReviewPlan, diff: PlanDiff) -> dict[str, Any]:
    return {
        "concepts": [
            {"term": c.term, "synonyms": list(c.synonyms), "theme": c.theme}
            for c in plan.vocabulary.concepts
        ],
        "criteria": [{"id": c.id, "kind": c.kind.value, "text": c.text} for c in plan.criteria],
        "clarifications": [_question_json(q) for q in plan.clarifications],
        "change_request": _change_json(plan.change_request),
        "notes": list(plan.notes),
        "model_id": plan.model_id,
        "prompt_version": plan.prompt_version,
        "diff": _diff_json(diff),
    }


def _diff_json(diff: PlanDiff) -> dict[str, Any]:
    return {
        "from_version": diff.from_version,
        "to_version": diff.to_version,
        "concepts": [
            {
                "theme": c.theme,
                "term": c.term,
                "change": c.change.value,
                "by": _author_json(c.by),
            }
            for c in diff.concepts
        ],
        "terms": [
            {
                "theme": t.theme,
                "concept": t.concept,
                "term": t.term,
                "change": t.change.value,
                "by": _author_json(t.by),
            }
            for t in diff.terms
        ],
        "criteria": [
            {
                "kind": c.kind.value,
                "text": c.text,
                "change": c.change.value,
                "by": _author_json(c.by),
            }
            for c in diff.criteria
        ],
    }


def _diff_from_json(value: Mapping[str, Any]) -> PlanDiff:
    return PlanDiff(
        from_version=value["from_version"],
        to_version=value["to_version"],
        concepts=tuple(
            ConceptChange(c["theme"], c["term"], ChangeKind(c["change"]), _author(c["by"]))
            for c in value["concepts"]
        ),
        terms=tuple(
            TermChange(
                t["theme"], t["concept"], t["term"], ChangeKind(t["change"]), _author(t["by"])
            )
            for t in value["terms"]
        ),
        criteria=tuple(
            CriterionChange(
                CriterionKind(c["kind"]), c["text"], ChangeKind(c["change"]), _author(c["by"])
            )
            for c in value["criteria"]
        ),
    )
