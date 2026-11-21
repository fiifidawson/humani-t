"""The retention period: when a project is deleted for age, and when its owners are warned.

The operator sets the period; without one nothing is ever deleted for age. The clock counts
from the project's last activity, a member's last read or an owner's Keep, whichever is
latest (ADR 0020). Reading those from storage, and deleting, are the application's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.projects import ProjectError

WARNING_DAYS = 14
MIN_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 3650

_DAY = timedelta(days=1)

# A read moves the clock at most once a day, so opening papers is not a write each time.
# Periods are at least 30 days, so a day either way changes nothing that matters.
READ_COUNTS_EVERY = _DAY


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """The deployment's period in days; `None` means projects are never deleted for age."""

    period_days: int | None = None

    def __post_init__(self) -> None:
        if self.period_days is not None and not (
            MIN_PERIOD_DAYS <= self.period_days <= MAX_PERIOD_DAYS
        ):
            raise ValueError(
                f"RetentionPolicy.period_days must be from {MIN_PERIOD_DAYS} "
                f"to {MAX_PERIOD_DAYS}"
            )

    @property
    def is_on(self) -> bool:
        return self.period_days is not None

    def deletes_at(
        self, *, last_activity_at: datetime, kept_at: datetime | None = None
    ) -> datetime | None:
        """When the project is deleted, or `None` when the deployment deletes nothing."""
        if self.period_days is None:
            return None
        start = last_activity_at if kept_at is None else max(last_activity_at, kept_at)
        return start + timedelta(days=self.period_days)


def days_left(deletes_at: datetime, now: datetime) -> int:
    """Whole days until the date, rounded up, so the last hours still read as one day."""
    return max(0, math.ceil((deletes_at - now) / _DAY))


def is_near(deletes_at: datetime, now: datetime) -> bool:
    return deletes_at - now <= timedelta(days=WARNING_DAYS)


def is_due(deletes_at: datetime, now: datetime) -> bool:
    return now >= deletes_at


class RetentionOffError(ProjectError):
    """The deployment sets no retention period, so there is nothing to keep a project from."""
