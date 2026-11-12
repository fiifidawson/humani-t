"""The always-on detector for structured identifiers: standard-library regular expressions.

The prompt guard runs it too, so a false positive refuses a model call. Bare numbers
are therefore matched only with a label ("MRN", "Tel:") or an unambiguous shape; an
unlabelled seven-digit number is not masked, which is one reason masking is not anonymity.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.domain.masking import (
    STRUCTURED_ENTITY_TYPES,
    EntityType,
    MaskSpan,
    detector_source,
)

NAME = "patterns"
# Bump whenever a pattern changes what it finds: stored maskings record this
# version, and a re-run masks again every text masked with an older one.
VERSION = "1"

_IDENTIFIER_VALUE = r"(?P<value>[A-Z0-9](?:[A-Z0-9]|[-/ ](?=[A-Z0-9])){3,24})"
# Minutes and seconds are written with ' and ", or with the primes U+2032 and
# U+2033, built with chr() so the source holds no look-alike characters.
_MINUTE = "['" + chr(0x2032) + "]"
_SECOND = '["' + chr(0x2033) + "]"
# A label ends at a separator, so "MRNs" or "passports" are not labels.
_LABEL_END = r"(?=[\s:#.]|$)\s*[:#.]?\s*"


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _has_digits(count: int) -> Callable[[str], bool]:
    return lambda value: len(_digits(value)) >= count


def _phone_digits(value: str) -> bool:
    # "(0)" is the trunk prefix written inside international numbers; it is not dialled.
    return 7 <= len(_digits(value.replace("(0)", ""))) <= 15


def _international_digits(value: str) -> bool:
    # E.164 allows at most 15 digits after the "+" or "00" prefix.
    number = value.replace("(0)", "").removeprefix("+").removeprefix("00")
    return 8 <= len(_digits(number)) <= 15


def _luhn(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _iban(value: str) -> bool:
    compact = value.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def _ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _decimal_pair(value: str) -> bool:
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", value)
    latitude, longitude = (abs(float(n)) for n in numbers[:2])
    return latitude <= 90 and longitude <= 180


def _degrees(value: str) -> bool:
    degrees = [int(d) for d in re.findall(r"(\d+)°", value)]
    return len(degrees) == 2 and degrees[0] <= 90 and degrees[1] <= 180


def _always(_: str) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class _Pattern:
    entity_type: EntityType
    regex: re.Pattern[str]
    is_valid: Callable[[str], bool] = _always

    def values(self, text: str) -> Iterator[tuple[int, int]]:
        """Offsets of the `value` group if the pattern has a label, else of the match."""
        group = "value" if "value" in self.regex.groupindex else 0
        for match in self.regex.finditer(text):
            if self.is_valid(match.group(group)):
                yield match.start(group), match.end(group)


# Ordered most specific first. A match that overlaps one already accepted is
# dropped, so a labelled MRN is not also reported as a phone number.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        EntityType.EMAIL,
        re.compile(
            r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
        ),
    ),
    # Papers write corresponding addresses as "name [at] domain [dot] org".
    _Pattern(
        EntityType.EMAIL,
        re.compile(
            r"(?<![\w.+-])[A-Za-z0-9._%+-]+\s*(?i:\[at\]|\(at\)|\{at\})\s*[A-Za-z0-9-]+"
            r"(?:\s*(?i:\[dot\]|\(dot\)|\{dot\}|\.)\s*[A-Za-z0-9-]+)+"
        ),
    ),
    # Brief rule 5: "MRN 448-1207" was missed before. The label stays readable.
    _Pattern(
        EntityType.MEDICAL_RECORD,
        re.compile(
            r"(?i:\b(?:MRN|MR\s?No\.?|MR\#|medical\s+record(?:\s+(?:number|no\.?|\#))?"
            r"|patient\s+(?:ID|number|no\.?)|hospital\s+(?:number|no\.?)"
            r"|file\s+(?:number|no\.?)))" + _LABEL_END + _IDENTIFIER_VALUE
        ),
        _has_digits(3),
    ),
    _Pattern(
        EntityType.ID_NUMBER,
        re.compile(
            r"(?i:\b(?:national\s+id(?:entity)?(?:\s+card)?|id\s+card"
            r"|identity\s+(?:card|number|no\.?)|NIN|NID|CNIC|SSN"
            r"|social\s+security(?:\s+number)?|aadhaar"
            r"|(?:UNHCR|proGres)(?:\s+(?:case|registration|individual))?)"
            r"(?:\s*(?:number|no\.?|\#|ID))?)" + _LABEL_END + _IDENTIFIER_VALUE
        ),
        _has_digits(3),
    ),
    _Pattern(EntityType.ID_NUMBER, re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")),
    _Pattern(EntityType.ID_NUMBER, re.compile(r"(?<![\d-])\d{5}-\d{7}-\d(?![\d-])")),
    _Pattern(
        EntityType.PASSPORT,
        re.compile(
            r"(?i:\bpassport(?:\s*(?:number|no\.?|\#))?)"
            + _LABEL_END
            + r"(?P<value>[A-Z0-9]{6,9})(?![A-Za-z0-9])"
        ),
        _has_digits(1),
    ),
    _Pattern(
        EntityType.IBAN,
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"),
        _iban,
    ),
    # Contiguous, or in the groups cards are printed in (4-4-4-4, 4-6-5), so an
    # ISBN or a list of numbers is not read as a card.
    _Pattern(
        EntityType.CARD_NUMBER,
        re.compile(
            r"(?<![\w-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})"
            r"(?![\w-])"
        ),
        _luhn,
    ),
    _Pattern(
        EntityType.COORDINATES,
        re.compile(r"(?<![\d.])[-+]?\d{1,2}\.\d{4,}°?\s*,\s*[-+]?\d{1,3}\.\d{4,}°?(?!\d|\.\d)"),
        _decimal_pair,
    ),
    _Pattern(
        EntityType.COORDINATES,
        re.compile(r"(?<![\d.])\d{1,2}\.\d+°?\s*[NS]\b,?\s*\d{1,3}\.\d+°?\s*[EW]\b"),
        _decimal_pair,
    ),
    _Pattern(
        EntityType.COORDINATES,
        re.compile(
            r"(?<!\d)\d{1,2}°\s*\d{1,2}"
            + _MINUTE
            + r"\s*(?:\d{1,2}(?:\.\d+)?"
            + _SECOND
            + r"\s*)?[NS][,\s]*\d{1,3}°\s*\d{1,2}"
            + _MINUTE
            + r"\s*(?:\d{1,2}(?:\.\d+)?"
            + _SECOND
            + r"\s*)?[EW]\b"
        ),
        _degrees,
    ),
    _Pattern(
        EntityType.IP_ADDRESS,
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
        _ip_address,
    ),
    _Pattern(
        EntityType.IP_ADDRESS,
        re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])"),
        _ip_address,
    ),
    _Pattern(
        EntityType.PHONE,
        re.compile(
            r"(?i:\b(?:tel|telephone|phone|mobile|mob|cell|fax|whatsapp)\b\.?"
            r"(?:\s*(?:no\.?|number))?)\s*[:#.]?\s*(?P<value>\(?\+?\d[\d ().-]{5,18}\d)"
        ),
        _phone_digits,
    ),
    _Pattern(
        EntityType.PHONE,
        re.compile(
            r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}(?:[ .-]?\(0\))?(?:[ .-]?\(?\d{1,4}\)?){2,6}"
            r"(?![\d-])"
        ),
        _international_digits,
    ),
    _Pattern(
        EntityType.PHONE,
        re.compile(r"(?<!\w)\(\d{2,5}\)\s?\d{3,4}[ .-]?\d{3,4}(?!\d)"),
        _phone_digits,
    ),
    _Pattern(EntityType.PHONE, re.compile(r"(?<![\d-])\d{3}[-.]\d{3}[-.]\d{4}(?![\d-])")),
)


class PatternDetector:
    """Structured identifiers by regular expression. No model, no I/O, always available."""

    @property
    def source(self) -> str:
        return detector_source(NAME, VERSION)

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return STRUCTURED_ENTITY_TYPES

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        accepted: list[tuple[int, int, EntityType]] = []
        for pattern in _PATTERNS:
            for start, end in pattern.values(text):
                if not any(start < e and s < end for s, e, _ in accepted):
                    accepted.append((start, end, pattern.entity_type))
        source = self.source
        return tuple(MaskSpan(s, e, kind, source) for s, e, kind in sorted(accepted))
