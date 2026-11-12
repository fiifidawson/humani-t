"""What can be downloaded, and the rules every download keeps, with no I/O.

Rendering belongs in `api/exports.py` and gathering in `application/exports.py`. Here: each
export's name and media type, a spreadsheet-safe cell, and a header-safe filename.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self
from uuid import UUID

# OWASP "CSV Injection": spreadsheet programs run a cell starting with one of
# these as a formula, which can exfiltrate other cells or start a program.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# RFC 6266 allows more, but a name we build ourselves never needs more than
# this, and anything else could break the header or a client's file system.
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ExportName(StrEnum):
    DATASET_CSV = "dataset.csv"
    DATASET_JSONL = "dataset.jsonl"
    INCLUDED_RIS = "included.ris"
    PRISMA_MD = "prisma.md"
    MANIFEST_JSON = "manifest.json"
    BRIEF_MD = "brief.md"
    BRIEF_JSON = "brief.json"
    BRIEF_HTML = "brief.html"

    @classmethod
    def parse(cls, name: str) -> Self | None:
        """The export called `name`, or `None` for anything else."""
        return next((e for e in cls if e.value == name), None)

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self]

    @property
    def is_brief(self) -> bool:
        return self.value.startswith("brief.")


_MEDIA_TYPES: dict[ExportName, str] = {
    ExportName.DATASET_CSV: "text/csv; charset=utf-8",
    ExportName.DATASET_JSONL: "application/x-ndjson",
    ExportName.INCLUDED_RIS: "application/x-research-info-systems",
    ExportName.PRISMA_MD: "text/markdown; charset=utf-8",
    ExportName.MANIFEST_JSON: "application/json",
    ExportName.BRIEF_MD: "text/markdown; charset=utf-8",
    ExportName.BRIEF_JSON: "application/json",
    ExportName.BRIEF_HTML: "text/html; charset=utf-8",
}


def neutralise_cell(value: str) -> str:
    """A cell a spreadsheet shows as text: one that would run as a formula gets a `'` first."""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def download_filename(name: ExportName, run_id: UUID) -> str:
    """`Humani-T-run-<first 8 of the run id>-<export>`: says what it is, holds no title."""
    return f"Humani-T-run-{run_id.hex[:8]}-{name.value}"


def content_disposition(filename: str) -> str:
    """An attachment header for `filename`; `ValueError` for a name it cannot carry safely."""
    if not _SAFE_FILENAME.fullmatch(filename) or ".." in filename:
        raise ValueError("the download filename must be ASCII letters, digits, '.', '_' or '-'")
    return f'attachment; filename="{filename}"'
