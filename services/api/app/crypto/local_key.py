"""Key-encryption keys held by the operator, delivered as a Docker secret.

Suitable for a self-hosted deployment. An organisation with Vault or a cloud
KMS implements `KeyProvider` against it instead, and nothing else changes.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from uuid import UUID

from app.crypto.aesgcm import AesGcmCipher

_KEK_BYTES = 32


class LocalKeyProvider:
    """A keyring of versioned KEKs. The highest version wraps; any version unwraps."""

    def __init__(self, keys: Mapping[int, bytes]) -> None:
        if not keys:
            raise ValueError("LocalKeyProvider needs at least one key")
        for version, key in keys.items():
            if len(key) != _KEK_BYTES:
                raise ValueError(f"KEK version {version} must be {_KEK_BYTES} bytes")
        self._keys = dict(keys)
        self._cipher = AesGcmCipher()

    @classmethod
    def from_config(cls, value: str) -> LocalKeyProvider:
        """Parse `1:<base64>,2:<base64>` as written by `cli generate-kek`."""
        keys: dict[int, bytes] = {}
        for entry in value.split(","):
            version, _, encoded = entry.strip().partition(":")
            try:
                keys[int(version)] = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("key_encryption_keys must be `version:base64,…`") from exc
        return cls(keys)

    @property
    def current_version(self) -> int:
        return max(self._keys)

    def wrap(self, data_key: bytes, scope_id: UUID) -> bytes:
        kek = self._keys[self.current_version]
        return self._cipher.seal(kek, data_key, scope_id.bytes)

    def unwrap(self, wrapped: bytes, kek_version: int, scope_id: UUID) -> bytes:
        try:
            kek = self._keys[kek_version]
        except KeyError as exc:
            raise ValueError(f"KEK version {kek_version} is not loaded") from exc
        return self._cipher.open(kek, wrapped, scope_id.bytes)


def generate_kek() -> str:
    """Return a new base64 KEK for the operator to store as a secret."""
    return base64.b64encode(AesGcmCipher().generate_key()).decode()
