"""What a run found and read: its papers, the sources it asked, and the text kept for screening.

Titles, abstracts and queries arrive already sealed under the project's key, and a paper's
identity is stored only as a keyed digest. Deduplicating one record is `paper.py`'s job.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.paper import PaperRecord, deduplicate


class TextKind(StrEnum):
    """Which text of a paper was kept for masking and screening."""

    ABSTRACT = "abstract"
    TITLE = "title"


class SourceOutcome(StrEnum):
    SEARCHED = "searched"
    FAILED = "failed"


def _require_aware(owner: str, name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{owner}.{name} must be timezone-aware")


def identity_digest(key: bytes, identity: str) -> bytes:
    """A paper's identity as stored: HMAC-SHA256 under a subkey of the project's data key.

    Lets a re-run find a paper it already has without storing the DOI or title
    in the clear, and dies with the project's key like everything else.
    """
    return hmac.new(key, identity.encode(), hashlib.sha256).digest()


def screening_text(record: PaperRecord) -> tuple[TextKind, str]:
    """The text a paper is masked and screened on: title and abstract, or the title alone."""
    abstract = (record.abstract or "").strip()
    title = record.title.strip()
    if abstract:
        return TextKind.ABSTRACT, f"{title}\n\n{abstract}"
    return TextKind.TITLE, title


@dataclass(frozen=True, slots=True)
class FoundPaper:
    """One paper after merging, and every source that returned it, in source order."""

    record: PaperRecord
    found_by: tuple[str, ...]


def merge_found(
    results: Sequence[tuple[str, Sequence[PaperRecord]]],
) -> tuple[FoundPaper, ...]:
    """Merge what each source returned into one list of distinct papers.

    The order of `results` is the order of precedence: when two sources return
    the same paper, the first source's record is kept (`deduplicate`).
    """
    found_by: dict[str, list[str]] = {}
    for source_id, records in results:
        for record in records:
            sources = found_by.setdefault(record.identity, [])
            if source_id not in sources:
                sources.append(source_id)
    every = [record for _, records in results for record in records]
    return tuple(
        FoundPaper(record=record, found_by=tuple(found_by[record.identity]))
        for record in deduplicate(every)
    )


@dataclass(frozen=True, slots=True)
class StoredPaper:
    """A paper known to one review, kept once however many runs find it again.

    `source_id` is the source whose record was kept; `first_run_id` the run that
    first found it, so a later run can say which of its papers are new.
    """

    id: UUID
    project_id: UUID
    review_id: UUID
    first_run_id: UUID
    identity_digest: bytes
    source_id: str
    record_sealed: bytes
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.identity_digest:
            raise ValueError("StoredPaper.identity_digest must not be empty")
        if not self.source_id.strip():
            raise ValueError("StoredPaper.source_id must not be blank")
        if not self.record_sealed:
            raise ValueError("StoredPaper.record_sealed must not be empty")
        _require_aware("StoredPaper", "created_at", self.created_at)


@dataclass(frozen=True, slots=True)
class PaperSet:
    """Which run's discovery a run works from.

    A run started without `discover` (a retry from Screen, say) adopts the review's newest.
    """

    run_id: UUID
    basis_run_id: UUID
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_aware("PaperSet", "recorded_at", self.recorded_at)

    @property
    def is_own(self) -> bool:
        return self.run_id == self.basis_run_id


@dataclass(frozen=True, slots=True)
class RunPaper:
    """One paper in a run's discovery, at its place in the merged list (from 1)."""

    run_id: UUID
    paper_id: UUID
    position: int
    found_by: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.position < 1:
            raise ValueError("RunPaper.position counts from 1")
        if not self.found_by or any(not s.strip() for s in self.found_by):
            raise ValueError("RunPaper.found_by must name the sources that returned the paper")


@dataclass(frozen=True, slots=True)
class RunSource:
    """What happened when a run asked one source: searched, with a count, or failed."""

    run_id: UUID
    source_id: str
    outcome: SourceOutcome
    found: int
    plan_version: int
    vocabulary_version: int
    query_sealed: bytes
    searched_at: datetime

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("RunSource.source_id must not be blank")
        if self.found < 0:
            raise ValueError("RunSource.found must not be negative")
        if self.outcome is SourceOutcome.FAILED and self.found:
            raise ValueError("RunSource.found must be 0 for a source that failed")
        if self.plan_version < 1:
            raise ValueError("RunSource.plan_version starts at 1")
        if self.vocabulary_version < 1:
            raise ValueError("RunSource.vocabulary_version starts at 1")
        if not self.query_sealed:
            raise ValueError("RunSource.query_sealed must not be empty")
        _require_aware("RunSource", "searched_at", self.searched_at)


@dataclass(frozen=True, slots=True)
class PaperText:
    """The text kept for a paper, sealed. Raw: the mask stage derives what models see."""

    paper_id: UUID
    project_id: UUID
    kind: TextKind
    text_sealed: bytes
    ingested_at: datetime

    def __post_init__(self) -> None:
        if not self.text_sealed:
            raise ValueError("PaperText.text_sealed must not be empty")
        _require_aware("PaperText", "ingested_at", self.ingested_at)


@dataclass(frozen=True, slots=True)
class IngestedText:
    """A paper's kept text in the clear, as the mask stage reads it. Never sent to a model."""

    paper_id: UUID
    kind: TextKind
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("IngestedText.text must not be blank")


class IngestedTextSource[UnitOfWorkT](Protocol):
    """Where the mask stage reads the text the ingest stage kept.

    Generic in the transaction type so this module stays free of storage; the
    application layer implements it with its `UnitOfWork`.
    """

    async def texts_for_run(
        self, uow: UnitOfWorkT, project_id: UUID, run_id: UUID
    ) -> tuple[IngestedText, ...]:
        """Every kept text of the run's papers, in the run's order. Empty before ingest."""
        ...
