"""Response models for the search across projects (UI_UX_SPEC §7.6).

The query arrives as query parameters and becomes the domain's `SearchQuery` in the
route; these models carry the results back out.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.project_search import ProjectHits, SearchHit, SearchResults, SearchScope


class SearchHitOut(BaseModel):
    scope: SearchScope
    text: str = Field(description="Where it matched, on one line; cut with … when long.")
    review_id: UUID | None
    run_id: UUID | None
    brief_version: int | None = Field(description="Briefs: the brief's version.")
    paper_id: UUID | None
    tag: str | None = Field(description="Papers and passages: the tag a brief cites, P0012.")
    title: str | None = Field(description="Papers: the paper's title.")
    thread_id: UUID | None
    question_id: UUID | None

    @classmethod
    def from_domain(cls, hit: SearchHit) -> SearchHitOut:
        return cls(
            scope=hit.scope,
            text=hit.text,
            review_id=hit.review_id,
            run_id=hit.run_id,
            brief_version=hit.brief_version,
            paper_id=hit.paper_id,
            tag=hit.tag,
            title=hit.title,
            thread_id=hit.thread_id,
            question_id=hit.question_id,
        )


class SearchGroupOut(BaseModel):
    project_id: UUID
    project_name: str
    is_archived: bool
    hits: list[SearchHitOut]

    @classmethod
    def from_domain(cls, group: ProjectHits) -> SearchGroupOut:
        return cls(
            project_id=group.project_id,
            project_name=group.project_name,
            is_archived=group.is_archived,
            hits=[SearchHitOut.from_domain(h) for h in group.hits],
        )


class SearchOut(BaseModel):
    query: str = Field(description="The query as searched: trimmed, spaces collapsed.")
    scope: SearchScope | None = Field(description="null: every scope.")
    groups: list[SearchGroupOut] = Field(description="By project, newest project first.")
    is_truncated: bool = Field(description="A cap was reached, so there may be more.")

    @classmethod
    def from_domain(
        cls, query: str, scope: SearchScope | None, results: SearchResults
    ) -> SearchOut:
        return cls(
            query=query,
            scope=scope,
            groups=[SearchGroupOut.from_domain(g) for g in results.groups],
            is_truncated=results.is_truncated,
        )
