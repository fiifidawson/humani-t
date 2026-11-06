"""The extraction seam: an uploaded file's bytes in, its text and page layout out.

Extraction parses files a stranger may have crafted, so every implementation
must hold to `ExtractionLimits` (pages, characters, archive members and their
sizes, a timeout), refuse external references, and make no network call.
Failures are `ExtractionFailedError` with a fixed reason, never the parser's
own message, which can quote the file.

Implementations (architecture §3.9, ADR-0009): `local.py` reads text, Markdown,
PDF (pypdf) and DOCX (standard library); `isolated.py` runs it in a separate
process with a stripped environment and kills it at the timeout. mmore's
extraction, which also reads images and audio, can be added later as another
implementation of the same Protocol.
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
