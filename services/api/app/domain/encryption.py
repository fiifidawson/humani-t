"""What encrypted data is bound to, and the lifecycle of a data key.

The cryptography itself lives in `app/crypto`. This module holds the rules that
must not depend on a library: what a ciphertext is bound to, and when a key is
usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID


class KeyDestroyedError(Exception):
    """The scope's data key was shredded. The data is permanently unreadable."""


class UnknownScopeError(Exception):
    """No data key exists for this scope."""


class DecryptionError(Exception):
    """The ciphertext was tampered with, or is being read in the wrong context."""


@dataclass(frozen=True, slots=True)
class EncryptionContext:
    """Where a ciphertext belongs.

    Bound into every ciphertext as associated data, so a value copied into
    another row, column or project fails to decrypt instead of being silently
    accepted.
    """

    scope_id: UUID
    purpose: str
    record_id: str

    def __post_init__(self) -> None:
        if not self.purpose.strip():
            raise ValueError("EncryptionContext.purpose must not be blank")
        if not self.record_id.strip():
            raise ValueError("EncryptionContext.record_id must not be blank")

    def associated_data(self) -> bytes:
        # Canonical JSON: a delimiter-joined string would let ("a:b", "c") and
        # ("a", "b:c") produce the same bytes.
        return json.dumps(
            [str(self.scope_id), self.purpose, self.record_id],
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, slots=True)
class WrappedDataKey:
    """A scope's data key, encrypted under a key-encryption key (KEK).

    `wrapped` is `None` once the key is destroyed. The row is kept so the
    destruction itself remains on record.
    """

    scope_id: UUID
    kek_version: int
    wrapped: bytes | None
    created_at: datetime
    destroyed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("WrappedDataKey.created_at must be timezone-aware")
        if (self.wrapped is None) != (self.destroyed_at is not None):
            raise ValueError("WrappedDataKey is destroyed exactly when wrapped is None")

    @property
    def is_destroyed(self) -> bool:
        return self.wrapped is None

    def require_wrapped(self) -> bytes:
        if self.wrapped is None:
            raise KeyDestroyedError(f"data key for scope {self.scope_id} was destroyed")
        return self.wrapped

    def destroyed(self, at: datetime) -> WrappedDataKey:
        return replace(self, wrapped=None, destroyed_at=at)

    def rewrapped(self, wrapped: bytes, kek_version: int) -> WrappedDataKey:
        return replace(self, wrapped=wrapped, kek_version=kek_version)
