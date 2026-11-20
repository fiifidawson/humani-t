"""Search across a person's projects: what a query is, what matches it, and the caps.

Matching runs on text the application has already decrypted; nothing here reads storage
or decides who may see what. A text matches when it holds every word of the query,
ignoring case and accents (ADR 0018).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 200
# Per scope in one project, then in all: enough to choose from in a palette, and a bound
# on the response however many projects match.
MAX_HITS_PER_SCOPE = 5
MAX_HITS = 50
# Projects searched per request, newest first. Each is decrypted to be searched.
MAX_PROJECTS = 100
EXCERPT_LENGTH = 160
_LEAD = 50


class SearchScope(StrEnum):
    """What a hit is, in the order a project's hits are listed."""

    PROJECTS = "projects"
    BRIEFS = "briefs"
    PAPERS = "papers"
    PASSAGES = "passages"
    QUESTIONS = "questions"


_SCOPE_ORDER = {scope: n for n, scope in enumerate(SearchScope)}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _folded_with_positions(text: str) -> tuple[str, list[int]]:
    """`text` folded, and for each folded character the index of the one it came from."""
    folded: list[str] = []
    origin: list[int] = []
    for index, ch in enumerate(text):
        part = _fold(ch)
        folded.append(part)
        origin.extend([index] * len(part))
    return "".join(folded), origin


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A few words to look for, in one scope or (`scope` `None`) in all of them."""

    text: str
    scope: SearchScope | None = None

    def __post_init__(self) -> None:
        text = " ".join(self.text.split())
        if not MIN_QUERY_LENGTH <= len(text) <= MAX_QUERY_LENGTH:
            raise ValueError(
                f"SearchQuery.text must be {MIN_QUERY_LENGTH} to {MAX_QUERY_LENGTH} characters"
            )
        if any(unicodedata.category(ch) == "Cc" for ch in text):
            raise ValueError("SearchQuery.text must not contain control characters")
        object.__setattr__(self, "text", text)

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_fold(self.text).split()))

    def covers(self, scope: SearchScope) -> bool:
        return self.scope is None or self.scope is scope

    def excerpt(self, text: str) -> str | None:
        """The part of `text` around its first match, on one line; `None` if it has no match."""
        line = " ".join(text.split())
        folded, origin = _folded_with_positions(line)
        found = [folded.find(term) for term in self.terms]
        if not found or min(found) < 0:
            return None
        if len(line) <= EXCERPT_LENGTH:
            return line
        start = max(0, min(origin[min(found)] - _LEAD, len(line) - EXCERPT_LENGTH))
        end = start + EXCERPT_LENGTH
        before = "…" if start > 0 else ""
        after = "…" if end < len(line) else ""
        return f"{before}{line[start:end].strip()}{after}"


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One match and where it leads. Ids not relevant to the scope are `None`.

    `text` is the excerpt holding the match. For a paper, `title` is its title and `tag`
    its place in the run (`P0012`); for a passage, `tag` is the tag the brief cites.
    """

    scope: SearchScope
    text: str
    review_id: UUID | None = None
    run_id: UUID | None = None
    brief_version: int | None = None
    paper_id: UUID | None = None
    tag: str | None = None
    title: str | None = None
    thread_id: UUID | None = None
    question_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("SearchHit.text must not be blank")


@dataclass(frozen=True, slots=True)
class ProjectHits:
    project_id: UUID
    project_name: str
    is_archived: bool
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True, slots=True)
class SearchResults:
    """Hits grouped by project. `is_truncated`: a cap was reached, so there may be more."""

    groups: tuple[ProjectHits, ...]
    is_truncated: bool


class ProjectTagSource[UnitOfWorkT](Protocol):
    """A project's tags, for the `projects` scope. Tags are owned elsewhere (§7.3)."""

    async def tags(self, uow: UnitOfWorkT, project_id: UUID) -> tuple[str, ...]: ...


def per_scope(hits: Iterable[SearchHit]) -> tuple[tuple[SearchHit, ...], bool]:
    """Keep up to `MAX_HITS_PER_SCOPE` per scope, in scope order; True if any was dropped."""
    kept: list[SearchHit] = []
    counts: dict[SearchScope, int] = {}
    is_truncated = False
    for hit in hits:
        count = counts.get(hit.scope, 0)
        if count >= MAX_HITS_PER_SCOPE:
            is_truncated = True
            continue
        counts[hit.scope] = count + 1
        kept.append(hit)
    kept.sort(key=lambda h: _SCOPE_ORDER[h.scope])
    return tuple(kept), is_truncated


def assemble(groups: Sequence[ProjectHits], *, is_partial: bool) -> SearchResults:
    """Cap each project's hits per scope, leave out projects without any, stop at `MAX_HITS`.

    `is_partial` says the caller stopped before searching every project.
    """
    kept: list[ProjectHits] = []
    room = MAX_HITS
    is_truncated = is_partial
    for group in groups:
        hits, is_capped = per_scope(group.hits)
        is_truncated = is_truncated or is_capped
        if not hits:
            continue
        if room <= 0:
            is_truncated = True
            break
        if len(hits) > room:
            is_truncated = True
            hits = hits[:room]
        kept.append(ProjectHits(group.project_id, group.project_name, group.is_archived, hits))
        room -= len(hits)
    return SearchResults(tuple(kept), is_truncated)
