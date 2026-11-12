"""The masking seams: a `Detector` finds spans, a `Sanitizer` masks text with them.

Every implementation runs inside the deployment. A detector that sends text away to
be analysed (mmore's `openai_filter`, a hosted NER API) defeats masking and must never
be written against these Protocols. Applying spans belongs in `domain/masking.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.masking import EntityType, MaskedText, MaskingRule, MaskSpan


@runtime_checkable
class Detector(Protocol):
    @property
    def source(self) -> str:
        """`name@version`, recorded on every span and in the run manifest."""
        ...

    @property
    def entity_types(self) -> frozenset[EntityType]:
        """Every type this detector can report."""
        ...

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        """Spans over `text`, each naming this detector as its source. May overlap."""
        ...


@runtime_checkable
class Sanitizer(Protocol):
    async def mask(self, text: str, *, rules: Sequence[MaskingRule]) -> MaskedText:
        """Mask `text` with this sanitiser's detectors and the project's `rules`."""
        ...
