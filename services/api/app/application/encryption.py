"""Envelope encryption: one data key per scope, wrapped by the operator's KEK.

A scope is whatever is deleted as a unit — a project. Destroying its data key
makes every ciphertext in that scope permanently unreadable, wherever copies of
the ciphertext still exist. That is how "delete a project completely" stays true
for rows we cannot easily find, and for the audit log, which must not be edited.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.crypto.base import Cipher, KeyProvider
from app.domain.encryption import EncryptionContext, UnknownScopeError, WrappedDataKey
from app.storage.base import UnitOfWork


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ScopeEncryption:
    """Encrypts and decrypts inside a caller's unit of work, never its own.

    Taking the unit of work as an argument keeps key creation in the same
    transaction as the data it protects: a project is never committed without
    its key, nor a key without its project.
    """

    def __init__(
        self,
        keys: KeyProvider,
        cipher: Cipher,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._keys = keys
        self._cipher = cipher
        self._clock = clock

    async def create_scope(self, uow: UnitOfWork, scope_id: UUID) -> None:
        data_key = self._cipher.generate_key()
        await uow.data_keys.add(
            WrappedDataKey(
                scope_id=scope_id,
                kek_version=self._keys.current_version,
                wrapped=self._keys.wrap(data_key, scope_id),
                created_at=self._clock(),
            )
        )

    async def encrypt(
        self, uow: UnitOfWork, context: EncryptionContext, plaintext: bytes
    ) -> bytes:
        data_key = await self._data_key(uow, context.scope_id)
        return self._cipher.seal(data_key, plaintext, context.associated_data())

    async def decrypt(
        self, uow: UnitOfWork, context: EncryptionContext, sealed: bytes
    ) -> bytes:
        data_key = await self._data_key(uow, context.scope_id)
        return self._cipher.open(data_key, sealed, context.associated_data())

    async def destroy_scope(self, uow: UnitOfWork, scope_id: UUID) -> None:
        """Crypto-shred. Irreversible once committed."""
        record = await self._record(uow, scope_id)
        if not record.is_destroyed:
            await uow.data_keys.save(record.destroyed(self._clock()))

    async def rewrap_all(self, uow: UnitOfWork) -> int:
        """Move every live data key to the current KEK. Data itself is untouched."""
        current = self._keys.current_version
        moved = 0
        for record in await uow.data_keys.list_live():
            if record.kek_version == current:
                continue
            data_key = self._keys.unwrap(
                record.require_wrapped(), record.kek_version, record.scope_id
            )
            await uow.data_keys.save(
                record.rewrapped(self._keys.wrap(data_key, record.scope_id), current)
            )
            moved += 1
        return moved

    async def _record(self, uow: UnitOfWork, scope_id: UUID) -> WrappedDataKey:
        record = await uow.data_keys.get(scope_id)
        if record is None:
            raise UnknownScopeError(f"no data key for scope {scope_id}")
        return record

    async def _data_key(self, uow: UnitOfWork, scope_id: UUID) -> bytes:
        record = await self._record(uow, scope_id)
        return self._keys.unwrap(record.require_wrapped(), record.kek_version, scope_id)
