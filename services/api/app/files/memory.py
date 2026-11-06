"""A `FileStore` in a dictionary: for tests, and for development with in-memory storage.

It enforces the same key shape and the same no-overwrite rule as the volume,
so a use case that passes against it cannot rely on anything the real store
refuses. The files die with the process, exactly like the in-memory database.
"""

from __future__ import annotations

from app.files.base import FileExistsInStoreError, require_key


class MemoryFileStore:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    @property
    def store_id(self) -> str:
        return "memory"

    async def put(self, key: str, data: bytes) -> None:
        if require_key(key) in self.files:
            raise FileExistsInStoreError(key)
        self.files[key] = bytes(data)

    async def get(self, key: str) -> bytes | None:
        return self.files.get(require_key(key))

    async def delete(self, key: str) -> bool:
        return self.files.pop(require_key(key), None) is not None
