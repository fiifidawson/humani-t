"""The encryption seams: who holds the master key, and how data is sealed.

Two Protocols, because they change for different reasons. `KeyProvider` moves
when the operator switches from a key file to Vault or a cloud KMS. `Cipher`
moves only if the algorithm does.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class KeyProvider(Protocol):
    """Wraps and unwraps data keys with a key-encryption key the app never stores."""

    @property
    def current_version(self) -> int: ...

    def wrap(self, data_key: bytes, scope_id: UUID) -> bytes:
        """Encrypt a data key under the current KEK, bound to its scope."""
        ...

    def unwrap(self, wrapped: bytes, kek_version: int, scope_id: UUID) -> bytes:
        """Raise `DecryptionError` if the key was wrapped for a different scope."""
        ...


@runtime_checkable
class Cipher(Protocol):
    """Authenticated encryption with a data key."""

    def generate_key(self) -> bytes: ...

    def seal(self, key: bytes, plaintext: bytes, associated_data: bytes) -> bytes: ...

    def open(self, key: bytes, sealed: bytes, associated_data: bytes) -> bytes:
        """Raise `DecryptionError` on any tampering or context mismatch."""
        ...
