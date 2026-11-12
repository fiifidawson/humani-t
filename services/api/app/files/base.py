"""The file storage seam: where uploaded files' bytes are kept.

Only ciphertext reaches a `FileStore`: files are sealed under the project's key
before `put` (ADR-0008), so a store never needs to be trusted with content. There is
no listing and no pre-signed URL. Keys are `<project id>/<document id>`, and any other
shape is refused, so a key can never become a path outside the store.
"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID

_KEY = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class FileExistsInStoreError(Exception):
    """A file is already stored under this key. Stored files are never overwritten."""


def file_key(project_id: UUID, document_id: UUID) -> str:
    return f"{project_id}/{document_id}"


def require_key(key: str) -> str:
    """Return `key` if it is `<uuid>/<uuid>`; raise `ValueError` for anything else."""
    if not _KEY.fullmatch(key):
        raise ValueError("a file key must be '<project id>/<document id>'")
    return key


class FileStore(Protocol):
    @property
    def store_id(self) -> str:
        """Which store this is, for the run manifest and the operator's checks."""
        ...

    async def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`. Raise `FileExistsInStoreError` if the key is taken."""
        ...

    async def get(self, key: str) -> bytes | None:
        """The bytes stored under `key`, or `None` if there are none."""
        ...

    async def delete(self, key: str) -> bool:
        """Remove the file. False if there was none, which is not an error."""
        ...
