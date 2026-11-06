"""A `FileStore` on a local directory: a Docker volume in the compose deployment (ADR-0008).

Each file is `<root>/<project id>/<document id>.sealed`, holding ciphertext
only. Writes go to a temporary file in the same directory, are flushed to
disk, and are moved into place in one rename, so a crash leaves either the
whole file or none of it. Files are created readable by the API's user alone.

The disk work runs in a thread, so a slow volume never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path

from app.files.base import FileExistsInStoreError, require_key

_SUFFIX = ".sealed"


class LocalVolumeFileStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def store_id(self) -> str:
        return "volume"

    def _path(self, key: str) -> Path:
        project, document = require_key(key).split("/")
        path = (self._root / project / f"{document}{_SUFFIX}").resolve()
        # The key's shape already rules this out; the check stays in case the
        # shape is ever loosened.
        if path.parent.parent != self._root:
            raise ValueError("a file key must stay inside the store")
        return path

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._put, self._path(key), bytes(data))

    async def get(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self._get, self._path(key))

    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(self._delete, self._path(key))

    @staticmethod
    def _put(path: Path, data: bytes) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsInStoreError(path.name)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).chmod(0o600)
            if path.exists():
                raise FileExistsInStoreError(path.name)
            Path(temporary).replace(path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary).unlink()
            raise

    @staticmethod
    def _get(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    @staticmethod
    def _delete(path: Path) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
