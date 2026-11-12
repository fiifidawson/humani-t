"""Extraction in a separate, short-lived process: the production choice (ADR-0009).

The child runs with `-I` and an almost empty environment, so no secret is within its
reach, and is killed at the timeout. Its stderr is never logged: a parser's traceback
can quote the file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog

from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractedDocument,
    ExtractionFailedError,
    ExtractionLimits,
)
from app.extraction.local import EXTRACTOR_ID

# The directory holding the `app` package: the child imports it from there,
# because `-I` leaves the current directory off its path.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from app.extraction._child import main; main()"
)
# Windows cannot start Python without these; nothing else is passed on.
_KEPT_VARIABLES = ("SYSTEMROOT", "WINDIR")

_log = structlog.get_logger(__name__)


def _child_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in _KEPT_VARIABLES if name in os.environ}


def _parse(answer: dict[str, Any]) -> ExtractedDocument:
    if not answer.get("ok"):
        raise ExtractionFailedError(DocumentFailure(answer["failure"]))
    return ExtractedDocument(
        text=str(answer["text"]),
        page_starts=tuple(int(p) for p in answer["page_starts"]),
        is_truncated=bool(answer["is_truncated"]),
        extractor=str(answer["extractor"]),
        is_paged=bool(answer["is_paged"]),
    )


class IsolatedDocumentExtractor:
    def __init__(self, *, python: str = sys.executable) -> None:
        self._python = python

    @property
    def extractor_id(self) -> str:
        return EXTRACTOR_ID

    async def extract(
        self, content: bytes, file_type: DocumentType, limits: ExtractionLimits
    ) -> ExtractedDocument:
        header = json.dumps({"file_type": file_type.value, "limits": asdict(limits)})
        process = await asyncio.create_subprocess_exec(
            self._python,
            "-I",
            "-c",
            _BOOTSTRAP,
            str(_SOURCE_ROOT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_environment(),
        )
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                stdout, _ = await process.communicate(header.encode() + b"\n" + content)
        except TimeoutError as exc:
            raise ExtractionFailedError(DocumentFailure.TIMED_OUT) from exc
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if process.returncode != 0:
            _log.warning("extraction_child_failed", returncode=process.returncode)
            failure = (
                DocumentFailure.TOO_COMPLEX
                if process.returncode is not None and process.returncode < 0
                else DocumentFailure.UNREADABLE
            )
            raise ExtractionFailedError(failure)
        try:
            return _parse(json.loads(stdout))
        except (ValueError, KeyError, TypeError) as exc:
            raise ExtractionFailedError(DocumentFailure.UNREADABLE) from exc
