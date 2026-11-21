"""Requirements for the retention period, as rules with no I/O (UI_UX_SPEC §7.7, ADR 0020).

No period means nothing is ever deleted for age. With one, a project's deletion date is
the period after its last activity or an owner's Keep, whichever is later, and owners are
warned in the last 14 days.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.history import NeedsYouItem, NeedsYouKind
from app.domain.projects import Project
from app.domain.retention import (
    WARNING_DAYS,
    RetentionPolicy,
    days_left,
    is_due,
    is_near,
)

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
YEAR = RetentionPolicy(period_days=365)


class TestRetentionPolicy:
    def test_no_period_means_nothing_is_ever_deleted_for_age(self):
        policy = RetentionPolicy()

        assert not policy.is_on
        assert policy.deletes_at(last_activity_at=NOW - timedelta(days=10_000)) is None

    def test_the_clock_counts_from_the_last_activity(self):
        assert YEAR.deletes_at(last_activity_at=NOW) == NOW + timedelta(days=365)

    def test_a_keep_after_the_last_activity_restarts_the_clock(self):
        kept = NOW + timedelta(days=100)

        assert YEAR.deletes_at(last_activity_at=NOW, kept_at=kept) == kept + timedelta(days=365)

    def test_activity_after_a_keep_restarts_the_clock_too(self):
        kept = NOW - timedelta(days=100)

        assert YEAR.deletes_at(last_activity_at=NOW, kept_at=kept) == NOW + timedelta(days=365)

    @pytest.mark.parametrize("days", [0, 13, 29, 3651])
    def test_a_period_outside_30_days_to_ten_years_is_refused(self, days):
        with pytest.raises(ValueError, match=r"RetentionPolicy\.period_days"):
            RetentionPolicy(period_days=days)

    def test_the_warning_window_is_14_days(self):
        assert WARNING_DAYS == 14


class TestDeletionDate:
    @pytest.mark.parametrize(
        ("left", "days"),
        [
            (timedelta(days=14), 14),
            (timedelta(days=13, hours=1), 14),
            (timedelta(hours=2), 1),
            (timedelta(0), 0),
            (-timedelta(days=3), 0),
        ],
    )
    def test_days_left_round_up_and_never_go_below_zero(self, left, days):
        assert days_left(NOW + left, NOW) == days

    def test_a_project_is_near_its_date_within_the_last_14_days(self):
        assert is_near(NOW + timedelta(days=14), NOW)
        assert not is_near(NOW + timedelta(days=14, seconds=1), NOW)

    def test_a_project_is_due_once_its_date_has_come(self):
        assert not is_due(NOW + timedelta(seconds=1), NOW)
        assert is_due(NOW, NOW)
        assert is_due(NOW - timedelta(days=1), NOW)


class TestKeptProject:
    def test_keeping_records_when_and_changes_nothing_else(self):
        project = Project(
            id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=uuid.uuid4()
        )
        later = NOW + timedelta(days=3)

        kept = project.kept(later)

        assert project.kept_at is None
        assert kept.kept_at == later
        assert kept.version == project.version
        assert kept.name_sealed == project.name_sealed


class TestRetentionNotice:
    def test_a_notice_names_its_project_and_date_and_has_no_review(self):
        project_id = uuid.uuid4()
        deletes_at = NOW + timedelta(days=14)

        item = NeedsYouItem(
            kind=NeedsYouKind.RETENTION,
            project_id=project_id,
            review_id=None,
            at=deletes_at - timedelta(days=WARNING_DAYS),
            deletes_at=deletes_at,
            days_left=14,
        )

        assert item.key == f"retention:{project_id}:2026-12-05"

    def test_a_new_date_is_a_new_key_so_a_dismissed_notice_comes_back(self):
        project_id = uuid.uuid4()

        def notice(deletes_at: datetime) -> NeedsYouItem:
            return NeedsYouItem(
                kind=NeedsYouKind.RETENTION,
                project_id=project_id,
                review_id=None,
                at=NOW,
                deletes_at=deletes_at,
                days_left=1,
            )

        assert notice(NOW).key != notice(NOW + timedelta(days=365)).key

    def test_a_notice_needs_its_date(self):
        with pytest.raises(ValueError, match="deletes_at"):
            NeedsYouItem(
                kind=NeedsYouKind.RETENTION, project_id=uuid.uuid4(), review_id=None, at=NOW
            )

    def test_only_a_notice_may_have_no_review(self):
        with pytest.raises(ValueError, match="review_id"):
            NeedsYouItem(
                kind=NeedsYouKind.FAILED_RUN,
                project_id=uuid.uuid4(),
                review_id=None,
                at=NOW,
                run_id=uuid.uuid4(),
            )
