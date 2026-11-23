"""What each line of a document's text is, as its file said: heading, list item, table cell.

Kept beside the text, one per line, and never inside it, so masking and every offset work
on the plain text alone. Only Word files say this; other files have none, and the web
guesses a layout from the text instead (ADR 0023).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

MAX_HEADING_LEVEL = 3

_PLACE = ("table", "row", "column")


class LineKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE_CELL = "table_cell"


@dataclass(frozen=True, slots=True)
class DocumentLine:
    """One line's kind: a heading with its `level`, or a table cell with its place.

    Levels run from 1 to 3. Table, row and column count from 0 in the document's order.
    """

    kind: LineKind
    level: int | None = None
    table: int | None = None
    row: int | None = None
    column: int | None = None

    def __post_init__(self) -> None:
        is_heading = self.kind is LineKind.HEADING
        if is_heading != (self.level is not None):
            raise ValueError("DocumentLine.level is set exactly for a heading")
        if self.level is not None and not 1 <= self.level <= MAX_HEADING_LEVEL:
            raise ValueError(f"DocumentLine.level must be from 1 to {MAX_HEADING_LEVEL}")
        place = (self.table, self.row, self.column)
        if self.kind is LineKind.TABLE_CELL:
            if any(p is None or p < 0 for p in place):
                raise ValueError("DocumentLine.table, row and column must all be 0 or more")
        elif any(p is not None for p in place):
            raise ValueError("DocumentLine.table, row and column are only for a table cell")

    def to_json(self) -> dict[str, object]:
        stored: dict[str, object] = {"kind": self.kind.value}
        if self.level is not None:
            stored["level"] = self.level
        for name in _PLACE:
            value = getattr(self, name)
            if value is not None:
                stored[name] = value
        return stored

    @classmethod
    def from_json(cls, data: object) -> DocumentLine:
        """Read a stored or child-made line; raise `ValueError` for anything else."""
        if not isinstance(data, dict):
            raise ValueError("DocumentLine must be an object")
        fields = cast(dict[str, object], data)
        if set(fields) - {"kind", "level", *_PLACE}:
            raise ValueError("DocumentLine has unknown fields")
        numbers = {name: fields.get(name) for name in ("level", *_PLACE)}
        if any(v is not None and type(v) is not int for v in numbers.values()):
            raise ValueError("DocumentLine numbers must be integers")
        try:
            kind = LineKind(fields.get("kind"))
        except ValueError as exc:
            raise ValueError("DocumentLine.kind is not one this application knows") from exc
        return cls(kind, **cast(dict[str, int | None], numbers))


def lines_to_json(lines: Sequence[DocumentLine]) -> list[dict[str, object]]:
    return [line.to_json() for line in lines]


def lines_from_json(value: object) -> tuple[DocumentLine, ...]:
    if not isinstance(value, list):
        raise ValueError("DocumentLine values must come as a list")
    return tuple(DocumentLine.from_json(item) for item in cast(list[object], value))


def lines_fit(lines: Sequence[DocumentLine], text: str) -> bool:
    """Whether `lines` describe `text`: one per line. A mask over a line break joins two."""
    return len(lines) == text.count("\n") + 1
