"""The extraction seam: an uploaded file's bytes in, its text and page layout out.

The files may be hostile, so every implementation keeps to `ExtractionLimits`, refuses
external references and makes no network call. A failure carries a fixed reason,
never the parser's own message, which can quote the file.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.documents import DocumentType, ExtractedDocument, ExtractionLimits


class DocumentExtractor(Protocol):
    @property
    def extractor_id(self) -> str:
        """`name@version`, recorded on every document it read."""
        ...

    async def extract(
        self, content: bytes, file_type: DocumentType, limits: ExtractionLimits
    ) -> ExtractedDocument:
        """The file's text. Raise `ExtractionFailedError` if it cannot be read safely."""
        ...
