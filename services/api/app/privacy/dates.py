"""Calendar dates by regular expression, for the broad list that uploaded documents get.

Only dates that name a day or a month are masked: a bare year is context, not a person.
Papers, questions and the prompt guard never use it, so a publication date never
refuses a model call.
"""

from __future__ import annotations

import re

from app.domain.masking import EntityType, MaskSpan, detector_source

NAME = "dates"
# Bump whenever a pattern changes what it finds: stored maskings record it.
VERSION = "1"

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    # An abbreviation's full stop belongs to it only before a number ("Mar. 12");
    # otherwise it ends the sentence.
    r"(?:\.(?=\s+\d))?"
)
_DAY = r"(?:0?[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?"
_YEAR = r"(?:1[89][0-9]{2}|20[0-9]{2})"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2024-03-12, 2024/03/12
    re.compile(
        r"(?<![\d/.-])" + _YEAR + r"[-/.](?:0?[1-9]|1[0-2])[-/.]"
        r"(?:0?[1-9]|[12][0-9]|3[01])(?![\d/-]|\.\d)"
    ),
    # 12/03/2024, 12.03.24, 3-12-2024
    re.compile(
        r"(?<![\d/.-])(?:0?[1-9]|[12][0-9]|3[01])[-/.](?:0?[1-9]|[12][0-9]|3[01])[-/.]"
        r"(?:" + _YEAR + r"|[0-9]{2})(?![\d/-]|\.\d)"
    ),
    # 12 March 2024, 12th of March, 12 Mar
    re.compile(
        r"(?<![\w])" + _DAY + r"\s+(?:of\s+)?" + _MONTHS + r"(?:,?\s+" + _YEAR + r")?(?![\w])"
    ),
    # March 12, 2024 · March 12th · March 2024
    re.compile(
        r"(?<![\w])"
        + _MONTHS
        + r"\s+(?:"
        + _DAY
        + r"(?:,?\s+"
        + _YEAR
        + r")?|"
        + _YEAR
        + r")(?![\w])"
    ),
)


class DatePatternDetector:
    """Dates that name a day or a month. No model, no I/O."""

    @property
    def source(self) -> str:
        return detector_source(NAME, VERSION)

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return frozenset({EntityType.DATE})

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        found: list[tuple[int, int]] = []
        for pattern in _PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                if not any(start < e and s < end for s, e in found):
                    found.append((start, end))
        source = self.source
        return tuple(MaskSpan(s, e, EntityType.DATE, source) for s, e in sorted(found))
