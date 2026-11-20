"""Seen and saved marks: which papers and briefs a person has opened, and what they saved.

Marks are per person. A paper mark keeps the paper's identity (`PaperRecord.identity`: the
DOI, else title and year) sealed under the paper's own project key, so the same paper can be
recognised in the person's other projects and the mark dies with its project. Deciding which
projects a person may see is the application's job; this module only combines marks.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.timestamps import require_aware


class PaperMarkKind(StrEnum):
    SEEN = "seen"
    SAVED = "saved"


@dataclass(frozen=True, slots=True)
class PaperMark:
    """A person opened, or saved, one paper of one project."""

    user_id: UUID
    paper_id: UUID
    kind: PaperMarkKind
    project_id: UUID
    identity_sealed: bytes
    marked_at: datetime

    def __post_init__(self) -> None:
        require_aware("PaperMark", marked_at=self.marked_at)
        if not self.identity_sealed:
            raise ValueError("PaperMark.identity_sealed must not be empty")


@dataclass(frozen=True, slots=True)
class BriefRead:
    """A person opened a decision brief, so Needs you stops listing it for them."""

    user_id: UUID
    brief_id: UUID
    project_id: UUID
    read_at: datetime

    def __post_init__(self) -> None:
        require_aware("BriefRead", read_at=self.read_at)


@dataclass(frozen=True, slots=True)
class PaperMarks:
    """What one paper of a project shows the person: seen, saved, and seen where else."""

    paper_id: UUID
    is_seen: bool
    is_saved: bool
    seen_in_other_projects: tuple[UUID, ...]


def marks_on_papers(
    project_id: UUID,
    seen_in: Mapping[UUID, Collection[UUID]],
    saved: Collection[UUID],
) -> tuple[PaperMarks, ...]:
    """The marks on a project's papers, for every paper that has one.

    `seen_in` maps a paper of `project_id` to the projects where the person opened the same
    paper; `saved` holds the papers of `project_id` they saved.
    """
    papers = sorted(set(seen_in) | set(saved))
    return tuple(
        PaperMarks(
            paper_id=paper,
            is_seen=bool(seen_in.get(paper)),
            is_saved=paper in saved,
            seen_in_other_projects=tuple(
                sorted(p for p in seen_in.get(paper, ()) if p != project_id)
            ),
        )
        for paper in papers
    )
