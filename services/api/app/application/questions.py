"""Follow-up questions in threads: asking, reading back, and answering in the background.

Only the question as sent is stored: masked, or with the project's masking off its rule
terms alone (ADR 0014). Answering can take minutes on a local model, so questions are
queued like runs and the browser polls. A thread answers one question at a time, so a
follow-up always sees the answer before it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from app.application.accounts import Current
from app.application.answer_loop import AnswerLoop, AnswerResult, Turn
from app.application.audit import AuditLog
from app.application.brief_loop import LoopFailedError
from app.application.briefs import BriefSeal, passage_from_dict, passage_to_dict
from app.application.encryption import ScopeEncryption
from app.application.inference import GuardedInference
from app.application.masking import MaskingRuleService, mask_as_chosen
from app.application.membership import require_member, require_review_member
from app.briefs.base import Retriever
from app.domain.accounts import NotFoundError
from app.domain.briefs import (
    AgentStep,
    Problem,
    RetrievedPassage,
    claim_from_dict,
    claim_to_dict,
)
from app.domain.encryption import EncryptionContext
from app.domain.errors import ConcurrentUpdateError, StorageUnavailableError
from app.domain.projects import Action
from app.domain.questions import (
    CONTEXT_TURNS,
    INTERRUPTED,
    Answer,
    QuestionConflictError,
    QuestionRecord,
    QuestionStatus,
    Thread,
    normalize_question,
)
from app.domain.runs import JsonValue
from app.domain.screening import EvidenceSource, Passage
from app.privacy.base import Sanitizer
from app.signals.base import Signals
from app.storage.base import StorageProvider, UnitOfWork

QUESTION_CHANNEL = "questions:queued"

# Table "questions"; the row is the question.
_QUESTION_PURPOSE = "questions.question"
_ANSWER_PURPOSE = "questions.answer"

_THREAD_NOT_FOUND = "No such thread."
_QUESTION_NOT_FOUND = "No such question."
_STILL_ANSWERING = (
    "The last question in this thread is still being answered. Wait for it, then ask again."
)
_ASKED_AT_ONCE = "Someone asked in this thread at the same moment. Ask again."
_UNEXPECTED = "The answer stopped because of an unexpected error. Ask again."
_MASKED_SINCE = (
    "This question was asked while masking was off, and masking is on now. Ask it again."
)
_RECOVERY_BATCH = 10

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AnswerView:
    """An answer in the clear: its claims, the evidence it drew on, and how it was made."""

    answer: Answer
    run_id: UUID | None
    evidence: tuple[RetrievedPassage, ...]
    steps: tuple[AgentStep, ...]
    rounds: int
    kept_round: int
    stopped_because: str | None
    warnings: tuple[Problem, ...]
    manifest: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class QuestionView:
    record: QuestionRecord
    question: str
    masked: Mapping[str, int]
    answer: AnswerView | None
    # False when the project's masking was off as it was asked.
    is_masked: bool = True


@dataclass(frozen=True, slots=True)
class ThreadView:
    thread: Thread
    questions: tuple[QuestionView, ...]


def _context(record: QuestionRecord, purpose: str) -> EncryptionContext:
    return EncryptionContext(record.project_id, purpose, str(record.id))


class _QuestionSeal:
    def __init__(self, encryption: ScopeEncryption) -> None:
        self._encryption = encryption

    async def seal(
        self,
        uow: UnitOfWork,
        record_project: UUID,
        record_id: UUID,
        purpose: str,
        value: object,
    ) -> bytes:
        context = EncryptionContext(record_project, purpose, str(record_id))
        data = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return await self._encryption.encrypt(uow, context, data)

    async def open(self, uow: UnitOfWork, record: QuestionRecord) -> QuestionView:
        """Raises `KeyDestroyedError` once the project is deleted."""
        asked = json.loads(
            await self._encryption.decrypt(
                uow, _context(record, _QUESTION_PURPOSE), record.question_sealed
            )
        )
        answer = None
        if record.answer_sealed is not None:
            answer = _answer_from_dict(
                json.loads(
                    await self._encryption.decrypt(
                        uow, _context(record, _ANSWER_PURPOSE), record.answer_sealed
                    )
                )
            )
        # Questions asked before masking became a choice were all masked.
        is_masked = bool(asked.get("is_masked", True))
        return QuestionView(record, asked["text"], asked["masked"], answer, is_masked)


def _answer_to_dict(result: AnswerResult, run_id: UUID | None) -> dict[str, Any]:
    return {
        "claims": [claim_to_dict(c) for c in result.answer.claims],
        "run_id": None if run_id is None else str(run_id),
        "evidence": [passage_to_dict(p) for p in result.evidence],
        "steps": [s.to_payload() for s in result.steps],
        "rounds": result.rounds,
        "kept_round": result.kept_round,
        "stopped_because": None
        if result.stopped_because is None
        else result.stopped_because.value,
        "warnings": [w.to_payload() for w in result.warnings],
        "manifest": result.manifest,
    }


def _answer_from_dict(data: Mapping[str, Any]) -> AnswerView:
    run_id = data["run_id"]
    return AnswerView(
        answer=Answer(tuple(claim_from_dict(c) for c in data["claims"])),
        run_id=None if run_id is None else UUID(run_id),
        evidence=tuple(passage_from_dict(p) for p in data["evidence"]),
        steps=tuple(AgentStep.from_payload(s) for s in data["steps"]),
        rounds=int(data["rounds"]),
        kept_round=int(data["kept_round"]),
        stopped_because=data["stopped_because"],
        warnings=tuple(Problem.from_payload(w) for w in data["warnings"]),
        manifest=data["manifest"],
    )


class QuestionService:
    """Asking and reading follow-up questions, for members of the review's project."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        sanitizer: Sanitizer,
        rules: MaskingRuleService,
        signals: Signals,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._seal = _QuestionSeal(encryption)
        self._audit = audit
        self._sanitizer = sanitizer
        self._rules = rules
        self._signals = signals
        self._clock = clock

    async def ask(
        self,
        current: Current,
        review_id: UUID,
        text: str,
        thread_id: UUID | None = None,
        request_id: str | None = None,
    ) -> QuestionView:
        """Mask the question, store it masked, and queue it to be answered.

        Raises `ValueError` for an unusable question, and `QuestionConflictError` while the
        thread's last question is still being answered.
        """
        async with self._storage.unit_of_work() as uow:
            review = await require_review_member(
                uow, current.user.id, review_id, Action.ASK_QUESTIONS
            )
            project_id = review.project_id
            question = normalize_question(text)
            if thread_id is not None:
                await self._thread(uow, current, thread_id, review_id)
            privacy = await self._rules.privacy_for(uow, project_id)
        masked = await mask_as_chosen(self._sanitizer, question, privacy)
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, Action.ASK_QUESTIONS)
            if thread_id is None:
                thread = Thread(uuid.uuid4(), project_id, review_id, now, current.user.id)
                await uow.questions.add_thread(thread)
                earlier: list[QuestionRecord] = []
            else:
                thread = await self._thread(uow, current, thread_id, review_id)
                earlier = await uow.questions.list_for_thread(thread.id)
                if any(q.is_open for q in earlier):
                    raise QuestionConflictError(_STILL_ANSWERING)
            question_id = uuid.uuid4()
            sealed = await self._seal.seal(
                uow,
                project_id,
                question_id,
                _QUESTION_PURPOSE,
                {
                    "text": masked.masked,
                    "masked": dict(masked.counts),
                    "is_masked": privacy.is_masked,
                },
            )
            record = QuestionRecord(
                id=question_id,
                thread_id=thread.id,
                project_id=project_id,
                review_id=review_id,
                position=len(earlier) + 1,
                asked_by=current.user.id,
                asked_at=now,
                status=QuestionStatus.PENDING,
                question_sealed=sealed,
            )
            try:
                await uow.questions.add(record)
            except ConcurrentUpdateError as exc:
                raise QuestionConflictError(_ASKED_AT_ONCE) from exc
            await self._audit.record(
                uow,
                current.actor(request_id),
                "question.asked",
                "question",
                str(question_id),
                scope_id=project_id,
                details={"thread_id": str(thread.id), "position": record.position},
            )
            await uow.commit()
        await self._signals.publish(QUESTION_CHANNEL)
        return QuestionView(record, masked.masked, dict(masked.counts), None, privacy.is_masked)

    async def threads(self, current: Current, review_id: UUID) -> list[ThreadView]:
        """The review's threads, newest first, each with its questions."""
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, Action.READ_PROJECT)
            return [
                await self._open_thread(uow, t)
                for t in await uow.questions.list_threads(review_id)
            ]

    async def thread(self, current: Current, thread_id: UUID) -> ThreadView:
        async with self._storage.unit_of_work() as uow:
            thread = await self._thread(uow, current, thread_id)
            return await self._open_thread(uow, thread)

    async def question(self, current: Current, question_id: UUID) -> QuestionView:
        async with self._storage.unit_of_work() as uow:
            record = await uow.questions.get(question_id)
            if record is None:
                raise NotFoundError(_QUESTION_NOT_FOUND)
            await require_member(
                uow,
                current.user.id,
                record.project_id,
                Action.READ_PROJECT,
                not_found=_QUESTION_NOT_FOUND,
            )
            return await self._seal.open(uow, record)

    async def authorize_thread(self, current: Current, thread_id: UUID) -> None:
        async with self._storage.unit_of_work() as uow:
            await self._thread(uow, current, thread_id)

    async def _open_thread(self, uow: UnitOfWork, thread: Thread) -> ThreadView:
        records = await uow.questions.list_for_thread(thread.id)
        return ThreadView(thread, tuple([await self._seal.open(uow, r) for r in records]))

    async def _thread(
        self, uow: UnitOfWork, current: Current, thread_id: UUID, review_id: UUID | None = None
    ) -> Thread:
        thread = await uow.questions.get_thread(thread_id)
        if thread is None or (review_id is not None and thread.review_id != review_id):
            raise NotFoundError(_THREAD_NOT_FOUND)
        await require_member(
            uow,
            current.user.id,
            thread.project_id,
            Action.READ_PROJECT,
            not_found=_THREAD_NOT_FOUND,
        )
        return thread


@dataclass(frozen=True, slots=True)
class _Work:
    question: str
    earlier: tuple[Turn, ...]
    run_id: UUID | None
    corpus: tuple[Passage, ...]


class QuestionWorker:
    """Answers queued questions one at a time, under a lease, like `RunWorker` runs."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        inference: GuardedInference,
        retriever: Retriever,
        evidence: EvidenceSource[UnitOfWork],
        rules: MaskingRuleService,
        worker_id: str,
        signals: Signals | None = None,
        max_rounds: int = 2,
        clock: Callable[[], datetime] = _utc_now,
        lease: timedelta = timedelta(minutes=10),
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._seal = _QuestionSeal(encryption)
        self._briefs = BriefSeal(encryption)
        self._inference = inference
        self._retriever = retriever
        self._evidence = evidence
        self._rules = rules
        self._worker_id = worker_id
        self._signals = signals
        self._max_rounds = max_rounds
        self._clock = clock
        self._lease = lease
        self._timer = timer

    async def answer_once(self) -> bool:
        """Fail questions whose attempts ran out, then answer the oldest waiting one.

        True if there was anything to do.
        """
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            stuck = await uow.questions.lock_exhausted(now, _RECOVERY_BATCH)
            for record in stuck:
                await uow.questions.save(record.failed(INTERRUPTED, now))
            record = await uow.questions.claim_next(self._worker_id, now, self._lease)
            await uow.commit()
        if record is None:
            return bool(stuck)
        try:
            await self._answer(record)
        except LoopFailedError as exc:
            await self._finish(record, error=str(exc))
        except Exception:
            # Members see a plain sentence; the details stay in the operator's log.
            _log.exception("question_failed", question_id=str(record.id))
            await self._finish(record, error=_UNEXPECTED)
        return True

    async def serve(self, stop: asyncio.Event, poll_seconds: float = 5.0) -> None:
        """Answer questions until `stop` is set, waking on a new question or every poll."""
        while not stop.is_set():
            try:
                if await self.answer_once():
                    continue
            except StorageUnavailableError:
                _log.warning("question_worker_storage_unavailable")
            if self._signals is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), poll_seconds)
                continue
            await self._signals.wait(QUESTION_CHANNEL, poll_seconds)

    async def _answer(self, record: QuestionRecord) -> None:
        async with self._storage.unit_of_work() as uow:
            privacy = await self._rules.privacy_for(uow, record.project_id)
            work = await self._gather(uow, record, is_masked=privacy.is_masked)
        if work is None:
            await self._finish(record, error=_MASKED_SINCE)
            return
        loop = AnswerLoop(
            self._inference.for_project(privacy),
            self._retriever,
            max_rounds=self._max_rounds,
            timer=self._timer,
        )

        async def keep_lease(step: AgentStep) -> None:
            await self._renew(record)

        result = await loop.run(work.question, work.earlier, work.corpus, keep_lease)
        result = replace(result, manifest={**result.manifest, "is_masked": privacy.is_masked})
        await self._finish(record, result=result, run_id=work.run_id)

    async def _gather(
        self, uow: UnitOfWork, record: QuestionRecord, *, is_masked: bool
    ) -> _Work | None:
        """What the model will read; `None` if the question itself was sent unmasked.

        Once masking is on again, earlier turns and brief passages kept with it off are
        left out rather than sent (ADR 0014).
        """
        opened = await self._seal.open(uow, record)
        if is_masked and not opened.is_masked:
            return None
        earlier: list[Turn] = []
        for previous in await uow.questions.list_for_thread(record.thread_id):
            if previous.position >= record.position or previous.answer_sealed is None:
                continue
            seen = await self._seal.open(uow, previous)
            if seen.answer is not None and (seen.is_masked or not is_masked):
                earlier.append(Turn(seen.question, _answer_text(seen.answer.answer)))
        run_id, corpus = await self._corpus(uow, record, is_masked=is_masked)
        return _Work(opened.question, tuple(earlier[-CONTEXT_TURNS:]), run_id, corpus)

    async def _corpus(
        self, uow: UnitOfWork, record: QuestionRecord, *, is_masked: bool
    ) -> tuple[UUID | None, tuple[Passage, ...]]:
        brief = await uow.briefs.latest_for_review(record.review_id)
        cited: tuple[Passage, ...] = ()
        if brief is not None:
            opened = await self._briefs.open_brief(uow, brief)
            if opened.manifest.get("is_masked", True) or not is_masked:
                cited = tuple(r.passage for r in opened.evidence)
            candidates = [brief.run_id]
        else:
            candidates = [r.id for r in await uow.runs.list_for_review(record.review_id)]
        for run_id in candidates:
            included = await self._evidence.included_passages(uow, record.review_id, run_id)
            if included or cited:
                return run_id, _merged((*included, *cited))
        return None, ()

    async def _renew(self, record: QuestionRecord) -> None:
        async with self._storage.unit_of_work() as uow:
            kept = await uow.questions.renew_lease(
                record.id, self._worker_id, self._clock() + self._lease
            )
            await uow.commit()
        if not kept:
            raise LoopFailedError("This question is being answered elsewhere.")

    async def _finish(
        self,
        record: QuestionRecord,
        *,
        result: AnswerResult | None = None,
        run_id: UUID | None = None,
        error: str | None = None,
    ) -> None:
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            current = await uow.questions.get(record.id)
            if (
                current is None
                or current.status is not QuestionStatus.ANSWERING
                or current.lease_owner != self._worker_id
            ):
                return
            if result is None:
                await uow.questions.save(current.failed(error or _UNEXPECTED, now))
            else:
                sealed = await self._seal.seal(
                    uow,
                    current.project_id,
                    current.id,
                    _ANSWER_PURPOSE,
                    _answer_to_dict(result, run_id),
                )
                await uow.questions.save(current.answered(sealed, result.model_id, now))
            await uow.commit()


def _merged(passages: Sequence[Passage]) -> tuple[Passage, ...]:
    seen: dict[tuple[str, str, str], Passage] = {}
    for p in passages:
        seen.setdefault((p.tag, p.text, p.locator), p)
    return tuple(seen.values())


def _answer_text(answer: Answer) -> str:
    return " ".join(
        f"{c.text} " + " ".join(f"[{t.tag}]" for t in c.citations) for c in answer.claims
    ).strip()
