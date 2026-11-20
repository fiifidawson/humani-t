"""Requirements for a leaning: the option a member leaned towards before reading a brief."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.leanings import Leaning, require_offered
from app.domain.projects import Project

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
OFFERED = ("Cash transfers", "Vouchers", "In-kind food")


def leaning(**changes: object) -> Leaning:
    values: dict[str, object] = {
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "option": "Cash transfers",
        "answered_at": NOW,
    }
    values.update(changes)
    return Leaning(**values)  # type: ignore[arg-type]


class TestLeaning:
    def test_a_leaning_names_an_option(self):
        assert leaning().option == "Cash transfers"

    def test_not_sure_is_a_leaning_without_an_option(self):
        assert leaning(option=None).option is None

    def test_a_blank_option_is_refused(self):
        with pytest.raises(ValueError, match=r"Leaning\.option"):
            leaning(option="  ")

    def test_a_time_without_a_zone_is_refused(self):
        with pytest.raises(ValueError, match="answered_at"):
            leaning(answered_at=datetime(2026, 11, 19, 9, 0))  # noqa: DTZ001


class TestRequireOffered:
    def test_an_option_the_brief_offers_is_accepted(self):
        require_offered("Vouchers", OFFERED)

    def test_not_sure_is_always_accepted(self):
        require_offered(None, OFFERED)

    def test_an_option_the_brief_does_not_offer_is_refused(self):
        with pytest.raises(ValueError, match=r"Leaning\.option must be one of the brief"):
            require_offered("Food stamps", OFFERED)


class TestTheProjectSetting:
    def test_a_project_does_not_ask_for_your_view_first_unless_turned_on(self):
        project = Project(
            id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=uuid.uuid4()
        )

        assert project.is_view_first_on is False
        assert project.with_view_first(is_on=True).is_view_first_on is True
