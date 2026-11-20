"""Brief versions, and comparing two versions of a brief or two runs of one review.

Read-only, for any member of the project. Each comparison is computed here from what was
stored when each brief and run was made (`domain/comparisons.py` holds the rules); none is
stored. Two ids the caller cannot both see answer 404, as a missing one does.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from app.application.accounts import Current
from app.application.briefs import BriefSeal, OpenedBrief
from app.application.encryption import ScopeEncryption
from app.application.membership import require_member, require_review_member
from app.application.papers import PaperService
from app.domain.accounts import NotFoundError
from app.domain.briefs import BriefRecord
from app.domain.comparisons import (
    BriefComparison,
    BriefSource,
    ComparedRun,
    RunComparison,
    compare_briefs,
    compare_discovery,
    compare_screening,
    compare_setup,
)
from app.domain.manifest import RunManifest
from app.domain.projects import Action
from app.domain.screening import Passage
from app.storage.base import StorageProvider, UnitOfWork

_BRIEF_NOT_FOUND = "No such brief."
_SAME_VERSION = "Choose two different versions to compare."
_OTHER_REVIEW_BRIEF = "Only versions of the same review's brief can be compared."
_SAME_RUN = "Choose two different runs to compare."
_OTHER_REVIEW_RUN = "Only runs of the same review can be compared."


@dataclass(frozen=True, slots=True)
class ComparedBriefs:
    """Two versions of a review's brief, the older first, and what changed between them."""

    before: BriefRecord
    after: BriefRecord
    changes: BriefComparison


def brief_sources(opened: OpenedBrief) -> tuple[BriefSource, ...]:
    """The papers and documents a brief cites, once each, by first citation.

    A tag with no passage (one the checks flagged) names no source, so it is left out.
    """
    first_passage: dict[str, Passage] = {}
    for item in opened.evidence:
        first_passage.setdefault(item.passage.tag, item.passage)
    sources: dict[UUID, BriefSource] = {}
    for tag in opened.brief.body.cited_tags():
        passage = first_passage.get(tag)
        if passage is None or passage.paper_id in sources:
            continue
        details = opened.sources.get(passage.paper_id)
        sources[passage.paper_id] = BriefSource(
            passage.paper_id,
            passage.source_kind,
            tag,
            None if details is None else details.title,
        )
    return tuple(sources.values())


def _changes(before: OpenedBrief, after: OpenedBrief) -> BriefComparison:
    return compare_briefs(
        before.brief.body,
        after.brief.body,
        sources_before=brief_sources(before),
        sources_after=brief_sources(after),
    )


class ComparisonService:
    def __init__(
        self, *, storage: StorageProvider, encryption: ScopeEncryption, papers: PaperService
    ) -> None:
        self._storage = storage
        self._seal = BriefSeal(encryption)
        self._papers = papers

    async def brief_versions(
        self, current: Current, review_id: UUID
    ) -> tuple[BriefRecord, ...]:
        """Every version of the review's brief, newest first; empty before the first."""
        async with self._storage.unit_of_work() as uow:
            await require_review_member(uow, current.user.id, review_id, Action.READ_PROJECT)
            return tuple(await uow.briefs.list_for_review(review_id))

    async def compare_briefs(
        self, current: Current, brief_id: UUID, other_id: UUID
    ) -> ComparedBriefs:
        """Raises `ValueError` for one version twice, or versions of two reviews."""
        async with self._storage.unit_of_work() as uow:
            first = await self._readable_brief(uow, current, brief_id)
            second = await self._readable_brief(uow, current, other_id)
            if first.id == second.id:
                raise ValueError(_SAME_VERSION)
            if first.review_id != second.review_id:
                raise ValueError(_OTHER_REVIEW_BRIEF)
            before, after = sorted((first, second), key=lambda b: b.version)
            changes = _changes(
                await self._seal.open_brief(uow, before),
                await self._seal.open_brief(uow, after),
            )
        return ComparedBriefs(before, after, changes)

    async def compare_runs(
        self, current: Current, run_id: UUID, other_id: UUID
    ) -> RunComparison:
        """The two runs part by part, the older first, as numbered in their review.

        Raises `ValueError` for one run twice, or runs of two reviews.
        """
        first = await self._papers.manifest(current, run_id)
        second = await self._papers.manifest(current, other_id)
        if first.run_id == second.run_id:
            raise ValueError(_SAME_RUN)
        if first.review_id != second.review_id:
            raise ValueError(_OTHER_REVIEW_RUN)
        async with self._storage.unit_of_work() as uow:
            newest_first = await uow.runs.list_for_review(first.review_id)
        number = {run.id: len(newest_first) - i for i, run in enumerate(newest_first)}
        before, after = sorted((first, second), key=lambda m: number[m.run_id])
        compared = RunComparison(_side(before, number), _side(after, number))
        if not compared.is_comparable:
            return compared
        papers_before = await self._papers.labelled_papers(current, before.run_id)
        papers_after = await self._papers.labelled_papers(current, after.run_id)
        return replace(
            compared,
            setup=compare_setup(before, after),
            discovery=compare_discovery(papers_before, papers_after),
            screening=compare_screening(papers_before, papers_after),
            brief=await self._brief_changes(before.run_id, after.run_id),
        )

    async def _readable_brief(
        self, uow: UnitOfWork, current: Current, brief_id: UUID
    ) -> BriefRecord:
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
        return record

    async def _brief_changes(self, before: UUID, after: UUID) -> BriefComparison | None:
        """`None` unless both runs wrote a brief. The caller has checked membership."""
        async with self._storage.unit_of_work() as uow:
            older = await uow.briefs.for_run(before)
            newer = await uow.briefs.for_run(after)
            if older is None or newer is None:
                return None
            return _changes(
                await self._seal.open_brief(uow, older), await self._seal.open_brief(uow, newer)
            )


def _side(manifest: RunManifest, number: dict[UUID, int]) -> ComparedRun:
    return ComparedRun(
        manifest.run_id, number[manifest.run_id], manifest.created_at, manifest.has_record
    )
