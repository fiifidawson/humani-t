"""The PaperSearchProvider Protocol.

Screening, the brief and the audit trail all inherit the provenance this seam records.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery

DEFAULT_LIMIT = 25


@runtime_checkable
class PaperSearchProvider(Protocol):
    """One academic repository, behind a uniform interface."""

    @property
    def source_id(self) -> str:
        """Stable short name, e.g. 'openalex', stored on every PaperRecord: never change it."""
        ...

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        """Run a compiled query and return what this source knows.

        Never add, expand or reinterpret terms: a strict vocabulary stays strict.
        """
        ...

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        """Return full text, or None when the document is not openly available.

        None is a normal outcome, not an error.
        """
        ...
