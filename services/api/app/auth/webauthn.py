"""Passkey verification with py_webauthn.

The only module that imports `webauthn`. It turns the library's types into the
domain's `VerifiedRegistration`/`VerifiedAssertion`, and every library failure
— a bad signature, a wrong origin, malformed CBOR — into one
`PasskeyVerificationError`, so callers cannot accidentally treat one kind of
failure as success.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.domain.mfa import (
    WEBAUTHN_CHALLENGE_LIFETIME,
    PasskeyVerificationError,
    VerifiedAssertion,
    VerifiedRegistration,
)

_TIMEOUT_MS = int(WEBAUTHN_CHALLENGE_LIFETIME.total_seconds() * 1000)
_KNOWN_TRANSPORTS = frozenset(
    {"ble", "cable", "hybrid", "internal", "nfc", "smart-card", "usb"}
)
# Malformed input surfaces from the library as these, not only as its own errors.
_FAILURES = (WebAuthnException, ValueError, KeyError, TypeError)


class WebAuthnPasskeyVerifier:
    def __init__(self, *, public_origin: str, rp_name: str = "Humani-T") -> None:
        self._origin = public_origin.rstrip("/")
        hostname = urlsplit(self._origin).hostname
        if not hostname:
            raise ValueError("public_origin must include a host name")
        # The RP id is the origin's host: passkeys are then usable on exactly
        # this deployment, never on a sibling subdomain.
        self._rp_id = hostname
        self._rp_name = rp_name

    def registration_options(
        self,
        *,
        user_handle: bytes,
        user_name: str,
        display_name: str,
        challenge: bytes,
        exclude: Sequence[bytes],
    ) -> dict[str, Any]:
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=user_handle,
            user_name=user_name,
            user_display_name=display_name,
            challenge=challenge,
            timeout=_TIMEOUT_MS,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in exclude],
        )
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    def verify_registration(
        self, credential: Mapping[str, Any], *, challenge: bytes
    ) -> VerifiedRegistration:
        try:
            verified = verify_registration_response(
                credential=dict(credential),
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                require_user_verification=True,
            )
        except _FAILURES as exc:
            raise PasskeyVerificationError(str(exc)) from exc
        return VerifiedRegistration(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=_transports(credential),
        )

    def authentication_options(
        self, *, challenge: bytes, allow: Sequence[bytes]
    ) -> dict[str, Any]:
        options = generate_authentication_options(
            rp_id=self._rp_id,
            challenge=challenge,
            timeout=_TIMEOUT_MS,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allow],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    def credential_id(self, credential: Mapping[str, Any]) -> bytes:
        raw_id = credential.get("rawId")
        if not isinstance(raw_id, str) or not raw_id:
            raise PasskeyVerificationError("credential has no rawId")
        try:
            return base64url_to_bytes(raw_id)
        except _FAILURES as exc:
            raise PasskeyVerificationError("credential rawId is not base64url") from exc

    def verify_authentication(
        self,
        credential: Mapping[str, Any],
        *,
        challenge: bytes,
        public_key: bytes,
        sign_count: int,
    ) -> VerifiedAssertion:
        try:
            verified = verify_authentication_response(
                credential=dict(credential),
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=True,
            )
        except _FAILURES as exc:
            raise PasskeyVerificationError(str(exc)) from exc
        return VerifiedAssertion(
            credential_id=verified.credential_id, sign_count=verified.new_sign_count
        )


def _transports(credential: Mapping[str, Any]) -> tuple[str, ...]:
    response = credential.get("response")
    listed = response.get("transports") if isinstance(response, Mapping) else None
    if not isinstance(listed, list):
        return ()
    return tuple(t for t in listed if isinstance(t, str) and t in _KNOWN_TRANSPORTS)
