"""The decision brief: the `answer` stage that writes it, and reading it back.

`AnswerStage` reads the run's screened-in passages and the review's question,
masks the question with the project's rules, runs the agent loop
(`brief_loop.py`) and stores one brief for the run, sealed under the project's
key. A stage run again after a crash finds the stored brief and returns it
instead of writing another (`runs/base.py`: a stage may run more than once).

`BriefService` reads a brief for a member: the brief, one cited source with
its passages and the claims citing it, and the trace. `ScreenedSources` says
what the pipeline knew of each cited paper when the brief was written.

No transaction is open while the model is called (architecture §5.7).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import SYSTEM, AuditLog
from app.application.brief_loop import BriefLoop, LoopFailedError, LoopResult
from app.application.encryption import ScopeEncryption
from app.application.inference import GuardedInference
from app.application.masking import MaskingRuleService
from app.application.membership import require_member
from app.application.papers import PaperStore
from app.application.reviews import question_context
from app.briefs.base import Retriever, SourceCatalogue
from app.domain.accounts import NotFoundError
from app.domain.briefs import (
    CAVEAT,
    AgentStep,
    BriefRecord,
    Claim,
    ClaimPlace,
    CriticScores,
    DecisionBrief,
    Problem,
    RetrievedPassage,
    SourceDetails,
    SourceKind,
    body_from_dict,
    body_to_dict,
)
from app.domain.encryption import EncryptionContext
from app.domain.projects import Action
from app.domain.runs import JsonValue
from app.domain.screening import EvidenceSource, Passage
from app.privacy.base import Sanitizer
from app.runs.base import StageContext, StageFailedError
from app.storage.base import StorageProvider, UnitOfWork

# Table "decision_briefs"; each column sealed separately, the row is the brief.
_CONTENT_PURPOSE = "decision_briefs.content"
_EVIDENCE_PURPOSE = "decision_briefs.evidence"
_TRACE_PURPOSE = "decision_briefs.trace"

_REVIEW_NOT_FOUND = "No such review."
_BRIEF_NOT_FOUND = "No such brief."
_NO_BRIEF_YET = "This review has no decision brief yet."
_NO_PASSAGE = "This citation has no passage in the brief's evidence."


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OpenedBrief:
    """A brief in the clear, with the evidence the Writer saw and the loop's trace."""

    brief: DecisionBrief
    evidence: tuple[RetrievedPassage, ...]
    sources: Mapping[UUID, SourceDetails]
    steps: tuple[AgentStep, ...]
    manifest: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CitedSource:
    """One tag of a brief: its passages, what is known of the source, and who cites it."""

    tag: str
    source_kind: SourceKind
    paper_id: UUID
    details: SourceDetails | None
    passages: tuple[RetrievedPassage, ...]
    cited_for: tuple[tuple[ClaimPlace, Claim], ...]


class ScreenedSources:
    """`SourceCatalogue` over the pipeline's papers: metadata and the run's screening."""

    def __init__(self, store: PaperStore) -> None:
        self._store = store

    async def describe(
        self, uow: UnitOfWork, run_id: UUID, paper_ids: Sequence[UUID]
    ) -> dict[UUID, SourceDetails]:
        run = await uow.runs.get(run_id)
        if run is None:
            return {}
        details: dict[UUID, SourceDetails] = {}
        for paper_id in dict.fromkeys(paper_ids):
            paper = await uow.papers.get(paper_id)
            if paper is None or paper.project_id != run.project_id:
                continue
            record = await self._store.open_paper(uow, paper)
            stored = await uow.screening.get(run_id, paper_id)
            decision = None if stored is None else await self._store.open_decision(uow, stored)
            details[paper_id] = SourceDetails(
                title=record.title,
                year=record.year,
                doi=record.doi,
                url=record.url,
                screening_label=None if decision is None else decision.label.value,
                rationale=None if decision is None else decision.rationale,
            )
        return details


class BriefSeal:
    """Seals and opens the three sealed columns of a brief, bound to its row."""

    def __init__(self, encryption: ScopeEncryption) -> None:
        self._encryption = encryption

    async def seal(
        self, uow: UnitOfWork, project_id: UUID, brief_id: UUID, purpose: str, value: object
    ) -> bytes:
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        context = EncryptionContext(project_id, purpose, str(brief_id))
        return await self._encryption.encrypt(uow, context, plaintext)

    async def open(self, uow: UnitOfWork, record: BriefRecord, purpose: str) -> Any:
        sealed = {
            _CONTENT_PURPOSE: record.content_sealed,
            _EVIDENCE_PURPOSE: record.evidence_sealed,
            _TRACE_PURPOSE: record.trace_sealed,
        }[purpose]
        context = EncryptionContext(record.project_id, purpose, str(record.id))
        return json.loads(await self._encryption.decrypt(uow, context, sealed))

    async def open_brief(self, uow: UnitOfWork, record: BriefRecord) -> OpenedBrief:
        """Raises `KeyDestroyedError` once the project is deleted."""
        content = await self.open(uow, record, _CONTENT_PURPOSE)
        evidence = await self.open(uow, record, _EVIDENCE_PURPOSE)
        trace = await self.open(uow, record, _TRACE_PURPOSE)
        quality = content["quality"]
        brief = DecisionBrief(
            id=record.id,
            project_id=record.project_id,
            review_id=record.review_id,
            run_id=record.run_id,
            version=record.version,
            question=content["question"],
            body=body_from_dict(content["body"]),
            model_id=record.model_id,
            rounds=record.rounds,
            kept_round=record.kept_round,
            max_rounds=record.max_rounds,
            stopped_because=record.stopped_because,
            created_at=record.created_at,
            caveat=content["caveat"],
            warnings=tuple(Problem.from_payload(w) for w in content["warnings"]),
            quality=None if quality is None else CriticScores.from_payload(quality),
        )
        return OpenedBrief(
            brief=brief,
            evidence=tuple(passage_from_dict(p) for p in evidence["passages"]),
            sources={UUID(k): SourceDetails(**v) for k, v in evidence["sources"].items()},
            steps=tuple(AgentStep.from_payload(s) for s in trace["steps"]),
            manifest=trace["manifest"],
        )


def passage_to_dict(item: RetrievedPassage) -> dict[str, JsonValue]:
    p = item.passage
    return {
        "tag": p.tag,
        "paper_id": str(p.paper_id),
        "source_kind": p.source_kind.value,
        "text": p.text,
        "locator": p.locator,
        "found_by": item.found_by,
        "round": item.round_number,
    }


def passage_from_dict(data: Mapping[str, Any]) -> RetrievedPassage:
    passage = Passage(
        tag=data["tag"],
        paper_id=UUID(data["paper_id"]),
        source_kind=SourceKind(data["source_kind"]),
        text=data["text"],
        locator=data["locator"],
    )
    return RetrievedPassage(passage, data["found_by"], int(data["round"]))


def _details_to_dict(details: SourceDetails) -> dict[str, JsonValue]:
    return {
        "title": details.title,
        "year": details.year,
        "doi": details.doi,
        "url": details.url,
        "screening_label": details.screening_label,
        "rationale": details.rationale,
    }


def _summary(opened: OpenedBrief) -> dict[str, JsonValue]:
    """What `stage.finished` says: ids, counts and labels only, and the manifest's facts."""
    brief = opened.brief
    return {
        "brief_id": str(brief.id),
        "version": brief.version,
        "options": len(brief.body.options),
        "passages": len(opened.evidence),
        "rounds": brief.rounds,
        "kept_round": brief.kept_round,
        "stopped_because": brief.stopped_because.value,
        "warnings": len(brief.warnings),
        "manifest": dict(opened.manifest),
    }


class AnswerStage:
    """The `answer` stage: write this run's decision brief, once."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        inference: GuardedInference,
        retriever: Retriever,
        evidence: EvidenceSource[UnitOfWork],
        catalogue: SourceCatalogue,
        sanitizer: Sanitizer,
        rules: MaskingRuleService,
        max_rounds: int = 3,
        clock: Callable[[], datetime] = _utc_now,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._seal = BriefSeal(encryption)
        self._audit = audit
        self._inference = inference
        self._retriever = retriever
        self._evidence = evidence
        self._catalogue = catalogue
        self._sanitizer = sanitizer
        self._rules = rules
        self._clock = clock
        self._timer = timer
        # Built once so a bad setting fails at start-up, not at the first run.
        self._max_rounds = BriefLoop(inference, retriever, max_rounds=max_rounds).max_rounds

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        async with self._storage.unit_of_work() as uow:
            stored = await uow.briefs.for_run(context.run_id)
            if stored is not None:
                return _summary(await self._seal.open_brief(uow, stored))
            review = await uow.reviews.get(context.review_id)
            if review is None:
                raise StageFailedError("This run's review no longer exists.")
            question = await self._encryption.decrypt(
                uow, question_context(review.project_id, review.id), review.question_sealed
            )
            rules = await self._rules.rules_for(uow, review.project_id)
            passages = await self._evidence.included_passages(
                uow, context.review_id, context.run_id
            )
        masked = await self._sanitizer.mask(question.decode(), rules=rules)
        loop = BriefLoop(
            self._inference.with_rules(rules),
            self._retriever,
            max_rounds=self._max_rounds,
            timer=self._timer,
        )

        async def report(step: AgentStep) -> None:
            await context.agent_step(step.to_payload())
            await context.raise_if_cancelled()

        try:
            result = await loop.run(masked.masked, passages, report)
        except LoopFailedError as exc:
            raise StageFailedError(str(exc)) from exc
        return await self._store(context, masked.masked, result)

    async def _store(
        self, context: StageContext, question: str, result: LoopResult
    ) -> dict[str, JsonValue]:
        brief_id = uuid.uuid4()
        project_id = context.project_id
        async with self._storage.unit_of_work() as uow:
            stored = await uow.briefs.for_run(context.run_id)
            if stored is not None:
                return _summary(await self._seal.open_brief(uow, stored))
            paper_ids = [r.passage.paper_id for r in result.evidence]
            sources = await self._catalogue.describe(uow, context.run_id, paper_ids)
            content = {
                "question": question,
                "body": body_to_dict(result.body),
                "caveat": CAVEAT,
                "warnings": [w.to_payload() for w in result.warnings],
                "quality": None if result.quality is None else result.quality.to_payload(),
            }
            evidence = {
                "passages": [passage_to_dict(r) for r in result.evidence],
                "sources": {str(k): _details_to_dict(v) for k, v in sources.items()},
            }
            trace = {
                "steps": [s.to_payload() for s in result.steps],
                "manifest": result.manifest,
            }
            record = BriefRecord(
                id=brief_id,
                project_id=project_id,
                review_id=context.review_id,
                run_id=context.run_id,
                version=await uow.briefs.count_for_review(context.review_id) + 1,
                created_at=self._clock(),
                model_id=result.model_id,
                rounds=result.rounds,
                kept_round=result.kept_round,
                max_rounds=self._max_rounds,
                stopped_because=result.stopped_because,
                content_sealed=await self._seal.seal(
                    uow, project_id, brief_id, _CONTENT_PURPOSE, content
                ),
                evidence_sealed=await self._seal.seal(
                    uow, project_id, brief_id, _EVIDENCE_PURPOSE, evidence
                ),
                trace_sealed=await self._seal.seal(
                    uow, project_id, brief_id, _TRACE_PURPOSE, trace
                ),
            )
            if not await uow.briefs.add(record):
                winner = await uow.briefs.for_run(context.run_id)
                if winner is None:
                    raise StageFailedError("The brief could not be stored. Try the run again.")
                return _summary(await self._seal.open_brief(uow, winner))
            await self._audit.record(
                uow,
                SYSTEM,
                "brief.created",
                "decision_brief",
                str(brief_id),
                scope_id=project_id,
                details={"run_id": str(context.run_id), "version": record.version},
            )
            opened = await self._seal.open_brief(uow, record)
            await uow.commit()
        return _summary(opened)


class BriefService:
    """Reading briefs, for any member of the brief's project."""

    def __init__(self, *, storage: StorageProvider, encryption: ScopeEncryption) -> None:
        self._storage = storage
        self._seal = BriefSeal(encryption)

    async def authorize_review(self, current: Current, review_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._review_project(uow, current, review_id)

    async def for_review(
        self, current: Current, review_id: UUID, run_id: UUID | None = None
    ) -> OpenedBrief:
        """The review's latest brief, or the one `run_id` wrote."""
        async with self._storage.unit_of_work() as uow:
            await self._review_project(uow, current, review_id)
            if run_id is None:
                record = await uow.briefs.latest_for_review(review_id)
            else:
                record = await uow.briefs.for_run(run_id)
            if record is None or record.review_id != review_id:
                raise NotFoundError(_NO_BRIEF_YET)
            return await self._seal.open_brief(uow, record)

    async def open(self, current: Current, brief_id: UUID) -> OpenedBrief:
        async with self._storage.unit_of_work() as uow:
            record = await uow.briefs.get(brief_id)
            if record is None:
                raise NotFoundError(_BRIEF_NOT_FOUND)
            await require_member(
                uow,
                current.user.id,
                record.project_id,
                Action.READ_PROJECT,
                not_found=_BRIEF_NOT_FOUND,
            )
            return await self._seal.open_brief(uow, record)

    async def cited_source(self, current: Current, brief_id: UUID, tag: str) -> CitedSource:
        """One tag's passages and the claims citing it; 404 if no passage has the tag."""
        opened = await self.open(current, brief_id)
        passages = tuple(r for r in opened.evidence if r.passage.tag == tag)
        if not passages:
            raise NotFoundError(_NO_PASSAGE)
        first = passages[0].passage
        return CitedSource(
            tag=tag,
            source_kind=first.source_kind,
            paper_id=first.paper_id,
            details=opened.sources.get(first.paper_id),
            passages=passages,
            cited_for=tuple(
                (place, claim)
                for place, claim in opened.brief.body.claims()
                if any(c.tag == tag for c in claim.citations)
            ),
        )

    async def _review_project(self, uow: UnitOfWork, current: Current, review_id: UUID) -> None:
        review = await uow.reviews.get(review_id)
        if review is None:
            raise NotFoundError(_REVIEW_NOT_FOUND)
        await require_member(
            uow,
            current.user.id,
            review.project_id,
            Action.READ_PROJECT,
            not_found=_REVIEW_NOT_FOUND,
        )
