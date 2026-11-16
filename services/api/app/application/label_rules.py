"""Reading, previewing and applying a review's label rules (UI_UX_SPEC §6.8, ADR 0013).

No model is called here: rules relabel from the per-criterion answers already stored, and
the model's decisions are never changed. Labels themselves are computed where they are
read, in `application/papers.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import Actor, AuditLog
from app.application.membership import require_review_member
from app.application.papers import PaperStore, newest_run_with_papers, relabel
from app.domain.errors import ConcurrentUpdateError
from app.domain.label_rules import (
    CriteriaSource,
    LabelRules,
    LabelRulesVersion,
    TierMove,
    count_moves,
)
from app.domain.plans import Criterion, PlanDiff, ReviewPlan, StoredPlanVersion
from app.domain.projects import Action
from app.storage.base import StorageProvider, UnitOfWork


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RulesHistory:
    """Every version of a review's rules, oldest first; the last one is in force."""

    versions: tuple[LabelRulesVersion, ...]

    @property
    def current(self) -> LabelRulesVersion | None:
        return self.versions[-1] if self.versions else None


@dataclass(frozen=True, slots=True)
class RulesPreview:
    """What rules would do to the review's newest run: `None` when it has no papers yet.

    `untouchable` counts the decisions no rule can reach, which keep the model's label.
    """

    run_id: UUID | None
    moves: tuple[TierMove, ...]
    untouchable: int

    @property
    def moved(self) -> int:
        return sum(move.count for move in self.moves)


@dataclass(frozen=True, slots=True)
class AppliedRules:
    version: LabelRulesVersion
    preview: RulesPreview


class LabelRulesService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        store: PaperStore,
        criteria: CriteriaSource[UnitOfWork],
        audit: AuditLog,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._store = store
        self._criteria = criteria
        self._audit = audit
        self._clock = clock

    async def history(self, current: Current, review_id: UUID) -> RulesHistory:
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, Action.READ_PROJECT)
            return RulesHistory(tuple(await uow.label_rules.list_for_review(review_id)))

    async def preview(
        self, current: Current, review_id: UUID, rules: LabelRules
    ) -> RulesPreview:
        """How many papers `rules` would move from the labels shown now. Changes nothing."""
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, Action.EDIT_LABELS)
            return await self._preview(uow, review_id, rules)

    async def apply(
        self,
        current: Current,
        review_id: UUID,
        rules: LabelRules,
        request_id: str | None = None,
    ) -> AppliedRules:
        """Store `rules` as the review's next version, and audit it.

        Raises `ConcurrentUpdateError` when someone else applied rules at the same moment.
        """
        async with self._storage.unit_of_work() as uow:
            review = await require_review_member(
                uow, current.user.id, review_id, Action.EDIT_LABELS
            )
            preview = await self._preview(uow, review_id, rules)
            latest = await uow.label_rules.latest(review_id)
            version = LabelRulesVersion(
                review_id=review_id,
                project_id=review.project_id,
                version=1 if latest is None else latest.version + 1,
                rules=rules,
                created_by=current.user.id,
                created_at=self._clock(),
                papers_moved=preview.moved,
            )
            if not await uow.label_rules.add(version):
                raise ConcurrentUpdateError("label rules changed while applying")
            await self._audit.record(
                uow,
                Actor(current.user.id, request_id),
                "review.label_rules_changed",
                "review",
                str(review_id),
                scope_id=review.project_id,
                details={
                    "version": version.version,
                    "possibly_min_met": rules.possibly_min_met,
                    "unclear_counts_as": rules.unclear_counts_as.value,
                    "papers_moved": preview.moved,
                },
            )
            await uow.commit()
        return AppliedRules(version, preview)

    async def _preview(
        self, uow: UnitOfWork, review_id: UUID, rules: LabelRules
    ) -> RulesPreview:
        run = await newest_run_with_papers(uow, review_id)
        if run is None:
            return RulesPreview(None, (), 0)
        decisions = await uow.screening.list_for_run(run.id)
        latest = await uow.label_rules.latest(review_id)
        shown = await relabel(
            uow,
            self._store,
            self._criteria,
            review_id,
            decisions,
            None if latest is None else latest.rules,
        )
        proposed = await relabel(uow, self._store, self._criteria, review_id, decisions, rules)
        return RulesPreview(
            run.id,
            count_moves(
                (before.tier, after.tier)
                for before, after in zip(shown.decisions, proposed.decisions, strict=True)
            ),
            proposed.untouchable,
        )


class _PlanVersions(Protocol):
    """What `PlanCriteria` needs of `PlanService`: a stored version, opened."""

    async def open_version(
        self, uow: UnitOfWork, record: StoredPlanVersion
    ) -> tuple[ReviewPlan, PlanDiff]: ...


class PlanCriteria:
    """`CriteriaSource` over the stored plan versions, opened by the plan service."""

    def __init__(self, plans: _PlanVersions) -> None:
        self._plans = plans

    async def criteria(
        self, uow: UnitOfWork, review_id: UUID, version: int
    ) -> tuple[Criterion, ...] | None:
        record = await uow.plans.get_version(review_id, version)
        if record is None:
            return None
        plan, _ = await self._plans.open_version(uow, record)
        return plan.criteria
