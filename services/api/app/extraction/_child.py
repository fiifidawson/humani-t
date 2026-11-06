"""The entry point of the extraction child process (`isolated.py` starts it).

Reads one request from stdin — a JSON header line, then the file's bytes —
and writes one JSON answer to stdout. Before touching the file it refuses
anything a text extractor never needs: opening a network connection, starting
a process, or writing a file (a Python audit hook). On Linux it also caps its
own memory, CPU time and file size. The parent kills it at the timeout.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

from app.domain.documents import (
    DocumentFailure,
    DocumentType,
    ExtractionFailedError,
    ExtractionLimits,
)
from app.extraction.local import extract_document

_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_FORBIDDEN_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.startfile",
        "ctypes.dlopen",
    }
)
_WRITE_MODES = frozenset("wax+")


def _audit(event: str, arguments: tuple[Any, ...]) -> None:
    if event in _FORBIDDEN_EVENTS:
        raise PermissionError(f"extraction may not use {event}")
    if event == "open" and len(arguments) > 1:
        mode = arguments[1]
        if isinstance(mode, str) and _WRITE_MODES & set(mode):
            raise PermissionError("extraction may not write files")


def forbid_side_effects() -> None:
    """Refuse network, processes and file writes for the rest of this process. Irreversible."""
    sys.addaudithook(_audit)


def _limit_resources(timeout_seconds: float) -> None:
    if sys.platform == "win32":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_BYTES, _MEMORY_BYTES))
    cpu = math.ceil(timeout_seconds) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _answer(header: dict[str, Any], content: bytes) -> dict[str, Any]:
    try:
        limits = ExtractionLimits(**header["limits"])
        extracted = extract_document(content, DocumentType(header["file_type"]), limits)
    except ExtractionFailedError as exc:
        return {"ok": False, "failure": exc.failure.value}
    except (MemoryError, RecursionError):
        return {"ok": False, "failure": DocumentFailure.TOO_COMPLEX.value}
    return {
        "ok": True,
        "text": extracted.text,
        "page_starts": list(extracted.page_starts),
        "is_truncated": extracted.is_truncated,
        "is_paged": extracted.is_paged,
        "extractor": extracted.extractor,
    }


def main() -> None:
    header: dict[str, Any] = json.loads(sys.stdin.buffer.readline())
    content = sys.stdin.buffer.read()
    _limit_resources(float(header["limits"]["timeout_seconds"]))
    forbid_side_effects()
    sys.stdout.buffer.write(json.dumps(_answer(header, content)).encode())
    sys.stdout.buffer.flush()
