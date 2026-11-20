"""Search across the caller's projects (UI_UX_SPEC §7.6): decrypt each one's text, then match.

Project text is sealed under each project's key, so it cannot be searched where it is
stored; each project is opened in its own short transaction and matched here (ADR 0018).
Only text every member already reads is searched; raw document and paper text never is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

from app.application.accounts import Current
from app.application.briefs import BriefSeal
from app.application.encryption import ScopeEncryption
from app.application.papers import PaperStore, newest_run_with_papers
from app.application.projects import name_context
from app.application.questions import asked_text
from app.application.reviews import question_context
from app.domain.accounts import ThrottledError
from app.domain.discovery import screening_text
from app.domain.encryption import KeyDestroyedError
from app.domain.project_search import (
    MAX_HITS,
    MAX_PROJECTS,
    ProjectHits,
    ProjectTagSource,
    SearchHit,
    SearchQuery,
    SearchResults,
    SearchScope,
    assemble,
    per_scope,
)
from app.domain.projects import Action, Project, can
from app.domain.reviews import Review
from app.domain.screening import passage_tag
from app.ratelimit.base import RateLimiter
from app.storage.base import StorageProvider, UnitOfWork

# Every search decrypts all of the caller's projects, so it is the costliest read there
# is. A palette searching as someone types sends a few a second at most.
SEARCHES_PER_MINUTE = 60
_WINDOW = timedelta(minutes=1)


class ProjectSearchService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        store: PaperStore,
        limiter: RateLimiter,
        tags: ProjectTagSource[UnitOfWork] | None = None,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._briefs = BriefSeal(encryption)
        self._store = store
        self._limiter = limiter
        self._tags = tags

    async def search(self, current: Current, query: SearchQuery) -> SearchResults:
        """Hits from the caller's projects, newest project first. Never audited: ids only."""
        decision = await self._limiter.hit(
            f"search:{current.user.id}", SEARCHES_PER_MINUTE, _WINDOW
        )
        if not decision.allowed:
            raise ThrottledError(decision.retry_after)
        async with self._storage.unit_of_work() as uow:
            projects = await uow.projects.list_for_member(current.user.id)
        groups: list[ProjectHits] = []
        found = 0
        is_partial = len(projects) > MAX_PROJECTS
        for project in projects[:MAX_PROJECTS]:
            if found >= MAX_HITS:
                is_partial = True
                break
            group = await self._search_project(current, project.id, query)
            if group is not None:
                groups.append(group)
                found += len(per_scope(group.hits)[0])
        return assemble(groups, is_partial=is_partial)

    async def _search_project(
        self, current: Current, project_id: UUID, query: SearchQuery
    ) -> ProjectHits | None:
        """`None` when the caller is no longer a member, or the project is gone."""
        async with self._storage.unit_of_work() as uow:
            # Checked again here: membership may have ended since the list was read.
            project = await uow.projects.get(project_id)
            membership = await uow.memberships.get(project_id, current.user.id)
            if project is None or project.is_deleted or membership is None:
                return None
            if not can(membership.role, Action.READ_PROJECT):
                return None
            try:
                name = (
                    await self._encryption.decrypt(
                        uow, name_context(project_id), project.name_sealed
                    )
                ).decode()
                hits = [hit async for hit in self._hits(uow, project, name, query)]
            except KeyDestroyedError:
                return None
        return ProjectHits(project_id, name, project.is_archived, tuple(hits))

    async def _hits(
        self, uow: UnitOfWork, project: Project, name: str, query: SearchQuery
    ) -> AsyncIterator[SearchHit]:
        if query.covers(SearchScope.PROJECTS):
            for text in (name, *(await self._project_tags(uow, project.id))):
                if (excerpt := query.excerpt(text)) is not None:
                    yield SearchHit(SearchScope.PROJECTS, excerpt)
        for review in await uow.reviews.list_for_project(project.id):
            async for hit in self._review_hits(uow, review, query):
                yield hit

    async def _project_tags(self, uow: UnitOfWork, project_id: UUID) -> tuple[str, ...]:
        return () if self._tags is None else await self._tags.tags(uow, project_id)

    async def _review_hits(
        self, uow: UnitOfWork, review: Review, query: SearchQuery
    ) -> AsyncIterator[SearchHit]:
        if query.covers(SearchScope.PROJECTS):
            question = await self._encryption.decrypt(
                uow, question_context(review.project_id, review.id), review.question_sealed
            )
            if (excerpt := query.excerpt(question.decode())) is not None:
                yield SearchHit(SearchScope.PROJECTS, excerpt, review_id=review.id)
        if query.covers(SearchScope.BRIEFS) or query.covers(SearchScope.PASSAGES):
            async for hit in self._brief_hits(uow, review, query):
                yield hit
        if query.covers(SearchScope.PAPERS):
            async for hit in self._paper_hits(uow, review, query):
                yield hit
        if query.covers(SearchScope.QUESTIONS):
            async for hit in self._question_hits(uow, review, query):
                yield hit

    async def _brief_hits(
        self, uow: UnitOfWork, review: Review, query: SearchQuery
    ) -> AsyncIterator[SearchHit]:
        record = await uow.briefs.latest_for_review(review.id)
        if record is None:
            return
        opened = await self._briefs.open_brief(uow, record)
        if query.covers(SearchScope.BRIEFS):
            for _, claim in opened.brief.body.claims():
                if (excerpt := query.excerpt(claim.text)) is not None:
                    yield SearchHit(
                        SearchScope.BRIEFS,
                        excerpt,
                        review_id=review.id,
                        run_id=record.run_id,
                        brief_version=record.version,
                    )
        if query.covers(SearchScope.PASSAGES):
            seen: set[str] = set()
            for retrieved in opened.evidence:
                passage = retrieved.passage
                if passage.tag in seen:
                    continue
                if (excerpt := query.excerpt(passage.text)) is not None:
                    seen.add(passage.tag)
                    yield SearchHit(
                        SearchScope.PASSAGES,
                        excerpt,
                        review_id=review.id,
                        run_id=record.run_id,
                        tag=passage.tag,
                    )

    async def _paper_hits(
        self, uow: UnitOfWork, review: Review, query: SearchQuery
    ) -> AsyncIterator[SearchHit]:
        run = await newest_run_with_papers(uow, review.id)
        if run is None:
            return
        for paper in await self._store.run_papers(uow, run):
            record = await self._store.open_record(uow, run.project_id, paper.paper_id)
            _, text = screening_text(record)
            if (excerpt := query.excerpt(text)) is not None:
                yield SearchHit(
                    SearchScope.PAPERS,
                    excerpt,
                    review_id=review.id,
                    run_id=run.id,
                    paper_id=paper.paper_id,
                    tag=passage_tag(paper.position),
                    title=record.title,
                )

    async def _question_hits(
        self, uow: UnitOfWork, review: Review, query: SearchQuery
    ) -> AsyncIterator[SearchHit]:
        for thread in await uow.questions.list_threads(review.id):
            for record in await uow.questions.list_for_thread(thread.id):
                text = await asked_text(self._encryption, uow, record)
                if (excerpt := query.excerpt(text)) is not None:
                    yield SearchHit(
                        SearchScope.QUESTIONS,
                        excerpt,
                        review_id=review.id,
                        thread_id=thread.id,
                        question_id=record.id,
                    )
