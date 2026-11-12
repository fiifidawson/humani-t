"""A software WebAuthn authenticator, for proving passkey verification is real.

It builds `clientDataJSON` and `authenticatorData` byte for byte and signs with a real
ES256 key, and its knobs forge one thing at a time, so a server that skips a check is
caught. It imports nothing from the app, and its CBOR encoder covers only the types
WebAuthn uses.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

_FLAG_USER_PRESENT = 0x01
_FLAG_USER_VERIFIED = 0x04
_FLAG_ATTESTED_CREDENTIAL = 0x40
_COSE_ES256 = -7


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor_encode(value: object) -> bytes:
    """Encode the CBOR subset WebAuthn needs (RFC 8949, definite lengths only)."""
    if isinstance(value, bool) or value is None:
        raise TypeError("booleans and null are not needed by WebAuthn here")
    if isinstance(value, int):
        return _cbor_head(0, value) if value >= 0 else _cbor_head(1, -1 - value)
    if isinstance(value, bytes):
        return _cbor_head(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode()
        return _cbor_head(3, len(encoded)) + encoded
    if isinstance(value, Mapping):
        items = b"".join(cbor_encode(k) + cbor_encode(v) for k, v in value.items())
        return _cbor_head(5, len(value)) + items
    raise TypeError(f"cannot CBOR-encode {type(value).__name__}")


def _cbor_head(major: int, argument: int) -> bytes:
    if argument < 24:
        return bytes([major << 5 | argument])
    for additional, fmt in ((24, ">B"), (25, ">H"), (26, ">I"), (27, ">Q")):
        if argument < 1 << (8 * struct.calcsize(fmt)):
            return bytes([major << 5 | additional]) + struct.pack(fmt, argument)
    raise ValueError("CBOR argument too large")


@dataclass
class SoftwareAuthenticator:
    """One passkey on one device: a P-256 key pair, a credential id and a counter.

    `counts_signatures=False` behaves like the many platform authenticators
    that always report a sign count of zero, which the server must accept.
    """

    counts_signatures: bool = True
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    private_key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    sign_count: int = 0
    user_handle: bytes | None = None

    @property
    def credential_id_b64(self) -> str:
        return b64url(self.credential_id)

    def public_key_cose(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor_encode(
            {
                1: 2,  # kty: EC2
                3: _COSE_ES256,  # alg
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def create(
        self,
        options: Mapping[str, Any],
        *,
        origin: str,
        rp_id: str | None = None,
        user_verified: bool = True,
        ceremony_type: str = "webauthn.create",
        challenge: str | None = None,
    ) -> dict[str, Any]:
        """Answer `navigator.credentials.create()`: a RegistrationResponseJSON.

        Every keyword forges one field; by default the answer is honest.
        """
        self.user_handle = b64url_decode(options["user"]["id"])
        client_data = self._client_data(
            ceremony_type, challenge or options["challenge"], origin
        )
        attested = (
            bytes(16)  # AAGUID: all zero for "none" attestation
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.public_key_cose()
        )
        authenticator_data = (
            self._authenticator_data(
                rp_id or options["rp"]["id"],
                user_verified=user_verified,
                extra_flags=_FLAG_ATTESTED_CREDENTIAL,
                sign_count=self.sign_count,
            )
            + attested
        )
        attestation_object = cbor_encode(
            {"fmt": "none", "attStmt": {}, "authData": authenticator_data}
        )
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation_object),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(
        self,
        options: Mapping[str, Any],
        *,
        origin: str,
        rp_id: str | None = None,
        user_verified: bool = True,
        ceremony_type: str = "webauthn.get",
        challenge: str | None = None,
        sign_count: int | None = None,
        tamper_signature: bool = False,
    ) -> dict[str, Any]:
        """Answer `navigator.credentials.get()`: an AuthenticationResponseJSON."""
        if sign_count is not None:
            self.sign_count = sign_count
        elif self.counts_signatures:
            self.sign_count += 1
        client_data = self._client_data(
            ceremony_type, challenge or options["challenge"], origin
        )
        authenticator_data = self._authenticator_data(
            rp_id or options["rpId"],
            user_verified=user_verified,
            extra_flags=0,
            sign_count=self.sign_count,
        )
        signature = self.private_key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        if tamper_signature:
            client_data = client_data.replace(b'"crossOrigin":false', b'"crossOrigin":false ')
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle) if self.user_handle else None,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def clone(self) -> SoftwareAuthenticator:
        """A copy of this key with its own counter: what a cloned security key looks like."""
        return SoftwareAuthenticator(
            counts_signatures=self.counts_signatures,
            credential_id=self.credential_id,
            private_key=self.private_key,
            sign_count=self.sign_count,
            user_handle=self.user_handle,
        )

    @staticmethod
    def _client_data(ceremony_type: str, challenge: str, origin: str) -> bytes:
        # Compact JSON, keys in the order browsers emit them.
        return json.dumps(
            {
                "type": ceremony_type,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _authenticator_data(
        rp_id: str, *, user_verified: bool, extra_flags: int, sign_count: int
    ) -> bytes:
        flags = _FLAG_USER_PRESENT | extra_flags
        if user_verified:
            flags |= _FLAG_USER_VERIFIED
        return (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", sign_count)
        )
