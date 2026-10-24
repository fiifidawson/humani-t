"""The password-hashing seam.

Synchronous on purpose: hashing is CPU-bound, so callers run it in a worker
thread (`asyncio.to_thread`) rather than pretending it is I/O.
"""

from __future__ import annotations

from typing import Protocol


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, stored_hash: str, password: str) -> bool:
        """False for a wrong password *and* for a malformed hash; never raises."""
        ...

    def needs_rehash(self, stored_hash: str) -> bool:
        """True when the hash was made with weaker parameters than the current ones."""
        ...
