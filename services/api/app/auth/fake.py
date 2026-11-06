"""A fast, deterministic stand-in for Argon2 in tests.

Real Argon2 spends 64 MiB and tens of milliseconds per call by design; a suite
that signs in hundreds of times cannot afford it. This one still really
distinguishes passwords, and counts verifications so tests can prove that both
sides of a branch did the same work.
"""

from __future__ import annotations

import hashlib
import hmac


class FakePasswordHasher:
    def __init__(self) -> None:
        self.verify_calls = 0

    def hash(self, password: str) -> str:
        return "fake$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, stored_hash: str, password: str) -> bool:
        self.verify_calls += 1
        return hmac.compare_digest(stored_hash, self.hash(password))

    def needs_rehash(self, stored_hash: str) -> bool:
        return not stored_hash.startswith("fake$")
