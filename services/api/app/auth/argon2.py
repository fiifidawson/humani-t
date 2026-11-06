"""Argon2id via argon2-cffi, the reference binding.

Defaults are RFC 9106's second recommended profile (64 MiB, 3 passes, 4 lanes),
which OWASP endorses. Raising them later is safe: old hashes still verify and
are upgraded at the user's next sign-in via `needs_rehash`.
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY


class Argon2PasswordHasher:
    def __init__(
        self, memory_cost_kib: int | None = None, time_cost: int | None = None
    ) -> None:
        self._hasher = _Argon2(
            memory_cost=memory_cost_kib or RFC_9106_LOW_MEMORY.memory_cost,
            time_cost=time_cost or RFC_9106_LOW_MEMORY.time_cost,
            parallelism=RFC_9106_LOW_MEMORY.parallelism,
            hash_len=RFC_9106_LOW_MEMORY.hash_len,
            salt_len=RFC_9106_LOW_MEMORY.salt_len,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True
