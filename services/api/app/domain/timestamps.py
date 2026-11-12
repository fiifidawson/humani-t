"""The one timestamp rule every stored domain record shares: it names its time zone.

Rules about what a time means (expiry, ordering) belong with the record that has them.
"""

from __future__ import annotations

from datetime import datetime


def require_aware(owner: str, **timestamps: datetime | None) -> None:
    """Raise `ValueError` naming `owner.field` for a naive timestamp; `None` is allowed."""
    for name, value in timestamps.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{owner}.{name} must be timezone-aware")
