"""The file storage seam: where uploaded files' bytes are kept.

Only ciphertext ever reaches a `FileStore`: the application seals a file under
its project's data key, bound to the document's id, before `put`, and opens it
after `get` (architecture §3.3, ADR-0008). A store therefore never needs to be
trusted with content, and deleting the project makes every file in it
unreadable wherever copies remain.

There is no pre-signed URL and no listing: files are reached only through the
API, which checks membership and audits, and only by the key the database
names. Keys are `<project id>/<document id>`, both UUIDs; an implementation
refuses any other shape, so a key can never become a path outside the store.
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
