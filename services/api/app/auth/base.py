"""The authenticator seams: password hashing and passkey (WebAuthn) verification.

Both are synchronous on purpose: the work is CPU-bound and local, so callers
run password hashing in a worker thread rather than pretending it is I/O.
Passkey verification is a few signature checks and runs inline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.domain.mfa import VerifiedAssertion, VerifiedRegistration


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, stored_hash: str, password: str) -> bool:
        """False for a wrong password *and* for a malformed hash; never raises."""
        ...

    def needs_rehash(self, stored_hash: str) -> bool:
        """True when the hash was made with weaker parameters than the current ones."""
        ...


class PasskeyVerifier(Protocol):
    """Builds WebAuthn ceremony options and verifies the browser's answers.

    Bound to one relying party — this deployment's `public_origin` — at
    construction. Options are the W3C JSON serialisation, ready to hand to
    `PublicKeyCredential.parseCreationOptionsFromJSON` and its sibling.
    """

    def registration_options(
        self,
        *,
        user_handle: bytes,
        user_name: str,
        display_name: str,
        challenge: bytes,
        exclude: Sequence[bytes],
    ) -> dict[str, Any]: ...

    def verify_registration(
        self, credential: Mapping[str, Any], *, challenge: bytes
    ) -> VerifiedRegistration:
        """Raise `PasskeyVerificationError` unless every check of WebAuthn §7.1 passes."""
        ...

    def authentication_options(
        self, *, challenge: bytes, allow: Sequence[bytes]
    ) -> dict[str, Any]: ...

    def credential_id(self, credential: Mapping[str, Any]) -> bytes:
        """The credential id an answer claims; `PasskeyVerificationError` if malformed."""
        ...

    def verify_authentication(
        self,
        credential: Mapping[str, Any],
        *,
        challenge: bytes,
        public_key: bytes,
        sign_count: int,
    ) -> VerifiedAssertion:
        """Raise `PasskeyVerificationError` unless every check of WebAuthn §7.2 passes."""
        ...
