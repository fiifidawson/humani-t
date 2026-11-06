"""A paper as the system knows it, independent of which repository found it.

The hard part here is identity. Four sources will return the same paper with
different ids, different capitalisation and sometimes a different year, and they
must collapse into one record or every count downstream is wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


def normalise_doi(doi: str) -> str:
    """Strip resolver prefixes and case. DOIs are case-insensitive by spec."""
    cleaned = doi.strip().casefold()
    for prefix in _DOI_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned.strip("/")


def normalise_title(title: str) -> str:
    """Reduce a title to comparable form: lowercase, alphanumeric, single spaces."""
    return _NON_ALNUM.sub(" ", title.casefold()).strip()


@dataclass(frozen=True, slots=True)
class PaperRecord:
    """One paper, as reported by one source.

    `source_id` plus `external_id` says where it came from and is never used for
    equality — two sources describing the same paper are the same paper.
    """

    source_id: str
    external_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    is_open_access: bool = False
    abstract: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("PaperRecord.source_id must not be blank")
        if not self.external_id.strip():
            raise ValueError("PaperRecord.external_id must not be blank")
        if not self.title.strip():
            raise ValueError("PaperRecord.title must not be blank")

    @property
    def identity(self) -> str:
        """The key two records must share to be considered the same paper.

        DOI when there is one — it is the only globally unique identifier here.
        Otherwise normalised title plus year, which is imperfect: preprint and
        published versions differ in year and will not merge. That is a known
        limitation, and deliberately not hidden behind fuzzy matching, because a
        wrong merge is far harder to notice than a missed one.
        """
        if self.doi:
            return f"doi:{normalise_doi(self.doi)}"
        return f"title:{normalise_title(self.title)}|{self.year or ''}"

    @property
    def searchable_text(self) -> str:
        """Title and abstract, for matching against query terms."""
        return f"{self.title} {self.abstract or ''}".casefold()


def deduplicate(records: Iterable[PaperRecord]) -> tuple[PaperRecord, ...]:
    """Collapse records sharing an identity, keeping the first seen.

    Stable: input order decides which record wins, so callers control source
    precedence by the order they merge in. Field-level merging — taking the
    abstract from one source and the open-access flag from another — is a real
    improvement and a separate concern; it is not done here.
    """
    seen: dict[str, PaperRecord] = {}
    for record in records:
        seen.setdefault(record.identity, record)
    return tuple(seen.values())
