"""Response models for the retention period and a project's deletion date (ADR 0020).

Dates are worked out by the API from the deployment's period, the project's last activity
and its latest Keep; the browser only words them.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.application.retention import RetentionView
from app.domain.retention import WARNING_DAYS, RetentionPolicy


class RetentionPolicyOut(BaseModel):
    period_days: int | None = Field(
        description="Days after a project's last activity until it is deleted; null: never."
    )
    warning_days: int = Field(description="Owners are told this many days before.")

    @classmethod
    def from_domain(cls, policy: RetentionPolicy) -> RetentionPolicyOut:
        return cls(period_days=policy.period_days, warning_days=WARNING_DAYS)


class ProjectRetentionOut(BaseModel):
    period_days: int | None = Field(description="The deployment's period; null: never.")
    deletes_at: datetime | None = Field(description="When the project will be deleted.")
    days_left: int | None = Field(description="Whole days until then, rounded up; 0 when due.")
    kept_at: datetime | None = Field(description="An owner's latest Keep, if any.")

    @classmethod
    def from_view(cls, view: RetentionView) -> ProjectRetentionOut:
        return cls(
            period_days=view.period_days,
            deletes_at=view.deletes_at,
            days_left=view.days_left,
            kept_at=view.kept_at,
        )
