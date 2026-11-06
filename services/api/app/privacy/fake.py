"""A detector that finds a fixed list of values, for real. No model, no I/O.

It searches the text for every value it was given, ignoring case, so a test
that changes the text changes what is found. A fake that returned fixed spans
would pass whatever the text said.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.domain.masking import EntityType, MaskSpan, detector_source


class FakeDetector:
    """Report every occurrence of each known value as its entity type."""

    def __init__(
        self, values: Mapping[str, EntityType], *, name: str = "fake", version: str = "1"
    ) -> None:
        self._values = dict(values)
        self._source = detector_source(name, version)

    @property
    def source(self) -> str:
        return self._source

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return frozenset(self._values.values())

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        return tuple(
            sorted(
                MaskSpan(match.start(), match.end(), kind, self._source)
                for value, kind in self._values.items()
                for match in re.finditer(re.escape(value), text, re.IGNORECASE)
            )
        )
