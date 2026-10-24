"""What makes a password acceptable, following NIST SP 800-63B rev 4.

Length and screening only: no composition rules ("one symbol, one digit") and no
forced rotation. Both make passwords more predictable, not less. The screening
list here is deliberately small and built in; a deployment can pass a larger
one to `PasswordPolicy(blocklist=…)`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

TOO_SHORT = "too_short"
TOO_LONG = "too_long"
TOO_COMMON = "too_common"
CONTAINS_PERSONAL_DETAILS = "contains_personal_details"

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "passw0rd",
        "p@ssword",
        "qwerty",
        "qwertyuiop",
        "iloveyou",
        "letmein",
        "welcome",
        "admin",
        "administrator",
        "monkey",
        "dragon",
        "football",
        "baseball",
        "sunshine",
        "princess",
        "starwars",
        "master",
        "shadow",
        "superman",
        "trustno1",
        "whatever",
        "freedom",
        "michael",
        "jennifer",
        "computer",
        "internet",
        "secret",
        "changeme",
        "default",
        "login",
        "abc123",
        "123456",
        "1234567890",
        "111111",
        "000000",
        "654321",
        "696969",
        "121212",
        "123123",
        "zaq12wsx",
        "1q2w3e4r",
        "qazwsx",
        "asdfgh",
        "zxcvbn",
        "hello",
        "charlie",
        "donald",
        "batman",
        "access",
        "humanitarian",
        "humani-t",
    }
)
# Keyboard rows and counting runs: any 15-character slice of these is a guess
# an attacker tries early.
_SEQUENCES = (
    "01234567890123456789",
    "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnm",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",
)
_PERSONAL_TOKEN = re.compile(r"[^\W_]{4,}", re.UNICODE)


def normalize_password(password: str) -> str:
    """NFKC-normalise, so the same password typed on any keyboard hashes the same."""
    return unicodedata.normalize("NFKC", password)


def _repeating_unit(text: str) -> str | None:
    """'abcabcabc' -> 'abc'; None when the text does not repeat."""
    for size in range(1, len(text) // 2 + 1):
        if len(text) % size == 0 and text[:size] * (len(text) // size) == text:
            return text[:size]
    return None


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int = 15
    max_length: int = 1024
    blocklist: frozenset[str] = field(default=_COMMON_PASSWORDS)

    def violations(self, password: str, context: Iterable[str] = ()) -> tuple[str, ...]:
        """Every reason the password is refused; empty when it is acceptable.

        `context` is the user's own details — email, display name — which a
        targeted attacker tries first.
        """
        normalized = normalize_password(password)
        found: list[str] = []
        if len(normalized) < self.min_length:
            found.append(TOO_SHORT)
        if len(normalized) > self.max_length:
            found.append(TOO_LONG)
        if self._is_common(normalized.casefold()):
            found.append(TOO_COMMON)
        if self._uses_personal_details(normalized.casefold(), context):
            found.append(CONTAINS_PERSONAL_DETAILS)
        return tuple(found)

    def _is_common(self, folded: str) -> bool:
        if folded in self.blocklist:
            return True
        unit = _repeating_unit(folded)
        if unit is not None and (len(unit) <= 4 or unit in self.blocklist):
            return True
        return any(folded in seq or folded in seq[::-1] for seq in _SEQUENCES)

    @staticmethod
    def _uses_personal_details(folded: str, context: Iterable[str]) -> bool:
        tokens = {
            token.casefold() for value in context for token in _PERSONAL_TOKEN.findall(value)
        }
        return any(token in folded for token in tokens)
