"""The combining `Sanitizer`: every detector, plus the project's rules, over one text.

A span found by any detector is masked (architecture §3.9.1): over-masking
costs some quality, under-masking leaks. Which entity types are masked depends
on the kind of text — the narrow list for papers, the broad one for uploaded
documents (`domain/masking.py`) — and is fixed when the sanitiser is built in
`api/dependencies.py`. Project rules always apply, whatever the list.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.domain.masking import EntityType, MaskedText, MaskingRule, apply_spans, rule_spans
from app.privacy.base import Detector


class DetectorSanitizer:
    def __init__(
        self, detectors: Sequence[Detector], *, entity_types: Collection[EntityType]
    ) -> None:
        if not detectors:
            raise ValueError("DetectorSanitizer.detectors must not be empty")
        self._entity_types = frozenset(entity_types)
        # A detector that can report none of the wanted types is never run, so
        # a model too slow for every paper costs nothing where it is not needed.
        self._detectors = tuple(d for d in detectors if d.entity_types & self._entity_types)

    @property
    def sources(self) -> tuple[str, ...]:
        """The detectors that look at the text, as `name@version`: the run manifest's record."""
        return tuple(sorted(d.source for d in self._detectors))

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return self._entity_types

    async def mask(self, text: str, *, rules: Sequence[MaskingRule]) -> MaskedText:
        spans = [
            span
            for detector in self._detectors
            for span in await detector.detect(text)
            if span.entity_type in self._entity_types
        ]
        return apply_spans(text, [*spans, *rule_spans(text, rules)])
