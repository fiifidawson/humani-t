"""AES-256-GCM, via the `cryptography` package.

Sealed format: `version (1 byte) || nonce (12 bytes) || ciphertext+tag`.
The version byte is there so the algorithm can change without guessing what an
old value was written with.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.encryption import DecryptionError

_FORMAT_V1 = b"\x01"
_NONCE_BYTES = 12
_KEY_BITS = 256


class AesGcmCipher:
    def generate_key(self) -> bytes:
        return AESGCM.generate_key(bit_length=_KEY_BITS)

    def seal(self, key: bytes, plaintext: bytes, associated_data: bytes) -> bytes:
        # Random 96-bit nonces are safe up to roughly 2^32 messages per key.
        # One key per project keeps every scope far below that.
        nonce = os.urandom(_NONCE_BYTES)
        return _FORMAT_V1 + nonce + AESGCM(key).encrypt(nonce, plaintext, associated_data)

    def open(self, key: bytes, sealed: bytes, associated_data: bytes) -> bytes:
        if sealed[:1] != _FORMAT_V1 or len(sealed) < 1 + _NONCE_BYTES:
            raise DecryptionError("unrecognised ciphertext format")
        nonce = sealed[1 : 1 + _NONCE_BYTES]
        try:
            return AESGCM(key).decrypt(nonce, sealed[1 + _NONCE_BYTES :], associated_data)
        except InvalidTag as exc:
            raise DecryptionError("ciphertext failed authentication") from exc
