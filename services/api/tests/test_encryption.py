"""Envelope encryption: binding, tamper detection, shredding and key rotation."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.domain.encryption import (
    DecryptionError,
    EncryptionContext,
    KeyDestroyedError,
    UnknownScopeError,
    WrappedDataKey,
)
from app.storage.memory import MemoryStorage

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
KEK_1 = bytes(range(32))
KEK_2 = bytes(range(32, 64))


def _context(scope_id: uuid.UUID, record_id: str = "row-1") -> EncryptionContext:
    return EncryptionContext(scope_id=scope_id, purpose="documents.body", record_id=record_id)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def encryption() -> ScopeEncryption:
    return ScopeEncryption(LocalKeyProvider({1: KEK_1}), AesGcmCipher(), clock=lambda: NOW)


@pytest.fixture
async def scope_id(storage: MemoryStorage, encryption: ScopeEncryption) -> uuid.UUID:
    scope_id = uuid.uuid4()
    async with storage.unit_of_work() as uow:
        await encryption.create_scope(uow, scope_id)
        await uow.commit()
    return scope_id


class TestEncryptionContext:
    def test_rejects_blank_purpose(self):
        with pytest.raises(ValueError, match="purpose"):
            EncryptionContext(scope_id=uuid.uuid4(), purpose=" ", record_id="1")

    def test_associated_data_cannot_be_forged_by_moving_a_delimiter(self):
        scope = uuid.uuid4()
        first = EncryptionContext(scope_id=scope, purpose="a:b", record_id="c")
        second = EncryptionContext(scope_id=scope, purpose="a", record_id="b:c")

        assert first.associated_data() != second.associated_data()


class TestWrappedDataKey:
    def test_rejects_naive_timestamps(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            WrappedDataKey(
                scope_id=uuid.uuid4(),
                kek_version=1,
                wrapped=b"x",
                created_at=datetime(2026, 1, 1),  # noqa: DTZ001 — the invalid input under test
            )

    def test_destroyed_key_refuses_to_hand_out_bytes(self):
        key = WrappedDataKey(uuid.uuid4(), 1, b"x", NOW).destroyed(NOW)

        assert key.is_destroyed
        with pytest.raises(KeyDestroyedError):
            key.require_wrapped()


class TestAesGcmCipher:
    def test_tampered_ciphertext_is_rejected(self):
        cipher = AesGcmCipher()
        key = cipher.generate_key()
        sealed = bytearray(cipher.seal(key, b"secret", b"ad"))
        sealed[-1] ^= 0x01

        with pytest.raises(DecryptionError):
            cipher.open(key, bytes(sealed), b"ad")

    def test_wrong_associated_data_is_rejected(self):
        cipher = AesGcmCipher()
        key = cipher.generate_key()

        with pytest.raises(DecryptionError):
            cipher.open(key, cipher.seal(key, b"secret", b"ad"), b"other")

    def test_unknown_format_version_is_rejected(self):
        cipher = AesGcmCipher()
        key = cipher.generate_key()
        sealed = cipher.seal(key, b"secret", b"ad")

        with pytest.raises(DecryptionError, match="format"):
            cipher.open(key, b"\x02" + sealed[1:], b"ad")

    def test_same_plaintext_never_produces_the_same_ciphertext(self):
        cipher = AesGcmCipher()
        key = cipher.generate_key()

        assert cipher.seal(key, b"same", b"") != cipher.seal(key, b"same", b"")

    @given(plaintext=st.binary(max_size=4096), associated=st.binary(max_size=256))
    def test_round_trips_any_bytes(self, plaintext: bytes, associated: bytes):
        cipher = AesGcmCipher()
        key = cipher.generate_key()

        assert (
            cipher.open(key, cipher.seal(key, plaintext, associated), associated) == plaintext
        )


class TestLocalKeyProvider:
    def test_parses_versioned_keys_and_wraps_with_the_newest(self):
        config = f"1:{generate_kek()},3:{generate_kek()}"

        assert LocalKeyProvider.from_config(config).current_version == 3

    def test_rejects_malformed_config(self):
        with pytest.raises(ValueError, match="version:base64"):
            LocalKeyProvider.from_config("one:not-base64!")

    def test_rejects_short_keys(self):
        short = base64.b64encode(b"too short").decode()
        with pytest.raises(ValueError, match="32 bytes"):
            LocalKeyProvider.from_config(f"1:{short}")

    def test_a_wrapped_key_cannot_be_moved_to_another_scope(self):
        keys = LocalKeyProvider({1: KEK_1})
        wrapped = keys.wrap(b"k" * 32, uuid.uuid4())

        with pytest.raises(DecryptionError):
            keys.unwrap(wrapped, 1, uuid.uuid4())


class TestScopeEncryption:
    async def test_round_trips_within_a_scope(self, storage, encryption, scope_id):
        async with storage.unit_of_work() as uow:
            sealed = await encryption.encrypt(uow, _context(scope_id), b"patient notes")
            assert await encryption.decrypt(uow, _context(scope_id), sealed) == b"patient notes"

    async def test_ciphertext_copied_to_another_row_does_not_decrypt(
        self, storage, encryption, scope_id
    ):
        async with storage.unit_of_work() as uow:
            sealed = await encryption.encrypt(uow, _context(scope_id, "row-1"), b"x")
            with pytest.raises(DecryptionError):
                await encryption.decrypt(uow, _context(scope_id, "row-2"), sealed)

    async def test_ciphertext_copied_to_another_project_does_not_decrypt(
        self, storage, encryption, scope_id
    ):
        other = uuid.uuid4()
        async with storage.unit_of_work() as uow:
            await encryption.create_scope(uow, other)
            sealed = await encryption.encrypt(uow, _context(scope_id), b"x")
            with pytest.raises(DecryptionError):
                await encryption.decrypt(uow, _context(other), sealed)

    async def test_shredded_scope_is_permanently_unreadable(
        self, storage, encryption, scope_id
    ):
        async with storage.unit_of_work() as uow:
            sealed = await encryption.encrypt(uow, _context(scope_id), b"x")
            await encryption.destroy_scope(uow, scope_id)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(KeyDestroyedError):
                await encryption.decrypt(uow, _context(scope_id), sealed)

    async def test_shredding_is_recorded_not_deleted(self, storage, encryption, scope_id):
        async with storage.unit_of_work() as uow:
            await encryption.destroy_scope(uow, scope_id)
            await uow.commit()
            record = await uow.data_keys.get(scope_id)

        assert record is not None
        assert record.destroyed_at == NOW

    async def test_unknown_scope_is_an_error_not_a_new_key(self, storage, encryption):
        async with storage.unit_of_work() as uow:
            with pytest.raises(UnknownScopeError):
                await encryption.encrypt(uow, _context(uuid.uuid4()), b"x")

    async def test_old_data_still_decrypts_after_kek_rotation(self, storage, scope_id):
        before = ScopeEncryption(LocalKeyProvider({1: KEK_1}), AesGcmCipher())
        async with storage.unit_of_work() as uow:
            sealed = await before.encrypt(uow, _context(scope_id), b"x")

        after = ScopeEncryption(LocalKeyProvider({1: KEK_1, 2: KEK_2}), AesGcmCipher())
        async with storage.unit_of_work() as uow:
            assert await after.rewrap_all(uow) == 1
            await uow.commit()

        retired_kek_1 = ScopeEncryption(LocalKeyProvider({2: KEK_2}), AesGcmCipher())
        async with storage.unit_of_work() as uow:
            assert await retired_kek_1.decrypt(uow, _context(scope_id), sealed) == b"x"

    async def test_rewrap_skips_destroyed_and_current_keys(self, storage, encryption, scope_id):
        async with storage.unit_of_work() as uow:
            await encryption.destroy_scope(uow, scope_id)
            await encryption.create_scope(uow, uuid.uuid4())
            await uow.commit()
            assert await encryption.rewrap_all(uow) == 0
