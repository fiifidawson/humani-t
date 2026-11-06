"""The PaperSearchProvider Protocol.

This is the most important interface in the system. Everything downstream —
screening, the decision brief, the audit trail — inherits the provenance it
establishes.

A Protocol rather than an ABC: implementations do not inherit from anything, so
a provider can be written, tested and swapped without importing this module at
runtime. Structural typing means conformance is checked by shape.
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
        """Stable short name, e.g. 'openalex'. Stored on every PaperRecord.

        Must not change once results referencing it have been persisted.
        """
        ...

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        """Run a compiled query and return what this source knows.

        The implementation compiles `query` into its own dialect. It must not
        add, expand or reinterpret terms — a strict vocabulary stays strict all
        the way down.
        """
        ...

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        """Return full text, or None when the document is not openly available.

        None is a normal outcome, not an error. Much of the corpus will be
        metadata-only, and screening has to work on abstracts alone.
        """
        ...
