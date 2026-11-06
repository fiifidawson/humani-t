"""An extractor that reads every file as UTF-8 text, or fails as it is told.

It reads the bytes for real, so what a test uploads is what masking sees; a
fake returning fixed text would pass whatever the file held. `failure` makes
every call fail with that reason, for the paths a real parser reaches only with
a crafted file. Each call records how many transactions were open, if given a
counter, so specs can prove no database connection is held while it runs.
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractedDocument,
    ExtractionFailedError,
    ExtractionLimits,
)


class FakeDocumentExtractor:
    def __init__(
        self,
        *,
        failure: DocumentFailure | None = None,
        open_units: Callable[[], int] | None = None,
    ) -> None:
        self.failure = failure
        self._open_units = open_units
        self.open_units_seen: list[int] = []
        self.extracted: list[DocumentType] = []

    @property
    def extractor_id(self) -> str:
        return "fake@1"

    async def extract(
        self, content: bytes, file_type: DocumentType, limits: ExtractionLimits
    ) -> ExtractedDocument:
        if self._open_units is not None:
            self.open_units_seen.append(self._open_units())
        self.extracted.append(file_type)
        if self.failure is not None:
            raise ExtractionFailedError(self.failure)
        text = content.decode("utf-8", errors="replace")[: limits.max_characters]
        if not text.strip():
            raise ExtractionFailedError(DocumentFailure.NO_TEXT)
        return ExtractedDocument(
            text=text,
            page_starts=(0,),
            is_truncated=len(content) > limits.max_characters,
            extractor=self.extractor_id,
        )
