"""Read an upload: one `multipart/form-data` body holding exactly one file.

Parsed here, in memory, rather than by Starlette's form parser, which spools large
parts to a temporary file and would leave an unencrypted copy on disk. The body is
already size-capped. Anything but one `file` part is refused, and the browser's
`Content-Type` is ignored: the type is read from the bytes.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.message import Message

FILE_FIELD = "file"
# The multipart framing around a file: boundaries and part headers.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
_MAX_PART_HEADER_BYTES = 8 * 1024
_MAX_PARTS = 4
# RFC 2046 §5.1.1: 1 to 70 characters, and not ending in a space.
_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,\-./:=? ]{0,69}[0-9A-Za-z'()+_,\-./:=?]")
_CRLF = b"\r\n"


class UploadFormatError(Exception):
    """The body is not one well-formed file upload. `status` is 415 or 422."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class UploadTooLargeError(Exception):
    """The body grew past its limit while it was being read."""


@dataclass(frozen=True, slots=True)
class UploadedFile:
    filename: str
    content: bytes


async def read_capped(stream: AsyncIterator[bytes], limit: int) -> bytes:
    """The whole body, refusing it as soon as it passes `limit` bytes."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > limit:
            raise UploadTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _header_parameters(name: str, value: str) -> Message:
    message = Message()
    message[name] = value
    return message


def _boundary(content_type: str) -> bytes:
    header = _header_parameters("content-type", content_type)
    if header.get_content_type() != "multipart/form-data":
        raise UploadFormatError(415, "Send the file as multipart/form-data.")
    boundary = header.get_param("boundary")
    if not isinstance(boundary, str) or not _BOUNDARY.fullmatch(boundary):
        raise UploadFormatError(422, "The upload's multipart boundary is missing or invalid.")
    return boundary.encode("ascii")


def _part_headers(raw: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.split(_CRLF):
        name, colon, value = line.partition(b":")
        if not colon or not name.strip():
            raise UploadFormatError(422, "The upload has a malformed part header.")
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("latin-1")
        headers[name.decode("latin-1").strip().lower()] = text.strip()
    return headers


def _parts(body: bytes, boundary: bytes) -> list[tuple[dict[str, str], bytes]]:
    delimiter = b"--" + boundary
    if body.startswith(delimiter):
        position = len(delimiter)
    else:
        found = body.find(_CRLF + delimiter)
        if found < 0:
            raise UploadFormatError(422, "The upload has no parts.")
        position = found + len(_CRLF) + len(delimiter)
    parts: list[tuple[dict[str, str], bytes]] = []
    while True:
        if body.startswith(b"--", position):
            return parts
        line_end = body.find(_CRLF, position)
        if line_end < 0 or body[position:line_end].strip(b" \t"):
            raise UploadFormatError(422, "The upload's multipart framing is malformed.")
        headers_start = line_end + len(_CRLF)
        headers_limit = headers_start + _MAX_PART_HEADER_BYTES
        headers_end = body.find(_CRLF + _CRLF, headers_start, headers_limit)
        if headers_end < 0:
            raise UploadFormatError(422, "The upload's part headers are missing or too long.")
        content_start = headers_end + 2 * len(_CRLF)
        next_delimiter = body.find(_CRLF + delimiter, content_start)
        if next_delimiter < 0:
            raise UploadFormatError(422, "The upload ends before its last part does.")
        headers = _part_headers(body[headers_start:headers_end])
        parts.append((headers, body[content_start:next_delimiter]))
        if len(parts) > _MAX_PARTS:
            raise UploadFormatError(422, "The upload has too many parts.")
        position = next_delimiter + len(_CRLF) + len(delimiter)


def parse_single_file(content_type: str, body: bytes) -> UploadedFile:
    """The one file in a `multipart/form-data` body, in a part named `file`."""
    files: list[UploadedFile] = []
    for headers, content in _parts(body, _boundary(content_type)):
        disposition = _header_parameters(
            "content-disposition", headers.get("content-disposition", "")
        )
        if disposition.get_content_disposition() != "form-data":
            raise UploadFormatError(422, "Each part of the upload must be form-data.")
        if disposition.get_param("name", header="content-disposition") != FILE_FIELD:
            raise UploadFormatError(422, f"Send the file in a field named '{FILE_FIELD}' only.")
        filename = disposition.get_filename()
        if filename is None:
            raise UploadFormatError(422, "The file part has no filename.")
        files.append(UploadedFile(filename, content))
    if len(files) != 1:
        raise UploadFormatError(422, "Send exactly one file per upload.")
    return files[0]
