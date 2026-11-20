"""Requirements for Needs you, the activity lines and seen marks, as rules with no I/O.

UI_UX_SPEC §7.1, §7.4 and §7.5: an item's key changes when the thing it names changes, so
a dismissal hides only what was dismissed; a timeline is grouped by review, newest first;
a run expands to its stages; a seen mark names the other projects it was seen in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.history import (
    ActivityAction,
    ActivityEvent,
    ActivityKind,
    Dismissal,
    NeedsYouItem,
    NeedsYouKind,
    StageState,
    group_by_review,
    run_numbers,
    stage_lines,
    still_needed,
)
from app.domain.marks import BriefRead, PaperMark, PaperMarkKind, marks_on_papers
from app.domain.runs import GateKind, Run, RunState

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
PROJECT = uuid.uuid4()
REVIEW = uuid.uuid4()


def _run(minutes: int = 0, **changes: object) -> Run:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "review_id": REVIEW,
        "project_id": PROJECT,
        "stages": ("queries", "discover", "screen"),
        "state": RunState.QUEUED,
        "created_at": NOW + timedelta(minutes=minutes),
        "created_by": uuid.uuid4(),
        "queued_at": NOW + timedelta(minutes=minutes),
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def _failed_run(at: datetime = NOW) -> NeedsYouItem:
    return NeedsYouItem(
        kind=NeedsYouKind.FAILED_RUN,
        project_id=PROJECT,
        review_id=REVIEW,
        at=at,
        run_id=uuid.uuid4(),
        run_number=1,
        stopped_at="screen",
    )


def _event(action: ActivityAction, minutes: int, review_id: uuid.UUID | None) -> ActivityEvent:
    return ActivityEvent(
        action=action,
        at=NOW + timedelta(minutes=minutes),
        project_id=PROJECT,
        review_id=review_id,
    )


class TestNeedsYouItems:
    def test_each_kind_of_item_is_keyed_by_the_thing_it_names(self):
        gate_id, brief_id = uuid.uuid4(), uuid.uuid4()
        failed = _failed_run()
        gate = NeedsYouItem(
            kind=NeedsYouKind.GATE,
            project_id=PROJECT,
            review_id=REVIEW,
            at=NOW,
            gate_id=gate_id,
            gate_kind=GateKind.VOCABULARY,
            run_id=uuid.uuid4(),
            run_number=1,
        )
        draft = NeedsYouItem(
            kind=NeedsYouKind.DRAFT, project_id=PROJECT, review_id=REVIEW, at=NOW, version=2
        )
        brief = NeedsYouItem(
            kind=NeedsYouKind.UNREAD_BRIEF,
            project_id=PROJECT,
            review_id=REVIEW,
            at=NOW,
            brief_id=brief_id,
            version=1,
        )

        assert gate.key == f"gate:{gate_id}"
        assert failed.key == f"failed_run:{failed.run_id}"
        assert draft.key == f"draft:{REVIEW}:2"
        assert brief.key == f"unread_brief:{brief_id}"

    def test_a_new_draft_version_is_a_new_item(self):
        first = NeedsYouItem(
            kind=NeedsYouKind.DRAFT, project_id=PROJECT, review_id=REVIEW, at=NOW, version=1
        )
        second = NeedsYouItem(
            kind=NeedsYouKind.DRAFT, project_id=PROJECT, review_id=REVIEW, at=NOW, version=2
        )

        assert first.key != second.key

    @pytest.mark.parametrize(
        ("kind", "missing"),
        [
            (NeedsYouKind.GATE, "gate_id"),
            (NeedsYouKind.FAILED_RUN, "run_id"),
            (NeedsYouKind.DRAFT, "version"),
            (NeedsYouKind.UNREAD_BRIEF, "brief_id"),
        ],
    )
    def test_an_item_without_the_thing_it_names_is_refused(self, kind, missing):
        with pytest.raises(ValueError, match=missing):
            NeedsYouItem(kind=kind, project_id=PROJECT, review_id=REVIEW, at=NOW)

    def test_dismissed_items_are_left_out_and_the_rest_come_newest_first(self):
        older = _failed_run(NOW)
        newer = _failed_run(NOW + timedelta(hours=1))
        dismissed = _failed_run(NOW + timedelta(hours=2))

        assert still_needed([older, dismissed, newer], {dismissed.key}) == (newer, older)

    def test_a_dismissal_names_an_item_key(self):
        with pytest.raises(ValueError, match=r"Dismissal\.item_key"):
            Dismissal(user_id=uuid.uuid4(), item_key=" ", dismissed_at=NOW)


class TestRuns:
    def test_runs_are_numbered_from_one_in_the_order_they_were_started(self):
        first, second, third = _run(0), _run(5), _run(9)

        assert run_numbers([third, first, second]) == {first.id: 1, second.id: 2, third.id: 3}

    def test_a_failed_run_expands_to_its_stages_up_to_where_it_stopped(self):
        run = _run(state=RunState.FAILED, stages_finished=1, started_at=NOW, finished_at=NOW)

        lines = stage_lines(run, {"queries": 4.5})

        assert [(s.stage, s.state, s.seconds) for s in lines] == [
            ("queries", StageState.DONE, 4.5),
            ("discover", StageState.STOPPED, None),
            ("screen", StageState.NOT_RUN, None),
        ]

    def test_a_waiting_run_shows_the_stage_that_waits(self):
        run = _run(state=RunState.WAITING, stages_finished=2, started_at=NOW)

        assert [s.state for s in stage_lines(run, {"queries": 1, "discover": 2})] == [
            StageState.DONE,
            StageState.DONE,
            StageState.WAITING,
        ]


class TestTimeline:
    def test_events_are_grouped_by_review_newest_first_inside_each(self):
        old_review, new_review = uuid.uuid4(), uuid.uuid4()
        events = [
            _event(ActivityAction.REVIEW_STARTED, 0, old_review),
            _event(ActivityAction.REVIEW_STARTED, 10, new_review),
            _event(ActivityAction.RUN, 5, old_review),
        ]

        groups = group_by_review(events)

        assert [g.review_id for g in groups] == [new_review, old_review]
        assert [e.action for e in groups[1].events] == [
            ActivityAction.RUN,
            ActivityAction.REVIEW_STARTED,
        ]

    def test_reviews_are_ordered_by_their_latest_activity_not_when_they_started(self):
        early, late = uuid.uuid4(), uuid.uuid4()
        events = [
            _event(ActivityAction.REVIEW_STARTED, 0, early),
            _event(ActivityAction.REVIEW_STARTED, 10, late),
            _event(ActivityAction.BRIEF_READY, 30, early),
        ]

        assert [g.review_id for g in group_by_review(events)] == [early, late]

    def test_project_events_outside_any_review_form_their_own_group(self):
        review = uuid.uuid4()
        events = [
            _event(ActivityAction.REVIEW_STARTED, 0, review),
            _event(ActivityAction.DOCUMENT_ADDED, 5, None),
        ]

        assert [g.review_id for g in group_by_review(events)] == [None, review]

    @pytest.mark.parametrize(
        ("action", "kind"),
        [
            (ActivityAction.REVIEW_STARTED, ActivityKind.REVIEW),
            (ActivityAction.PLAN_DRAFTED, ActivityKind.REVIEW),
            (ActivityAction.PLAN_ACCEPTED, ActivityKind.REVIEW),
            (ActivityAction.LABELS_CHANGED, ActivityKind.REVIEW),
            (ActivityAction.RUN, ActivityKind.RUN),
            (ActivityAction.BRIEF_READY, ActivityKind.BRIEF),
            (ActivityAction.QUESTION_ASKED, ActivityKind.QUESTION),
            (ActivityAction.DOCUMENT_ADDED, ActivityKind.DOCUMENT),
        ],
    )
    def test_every_action_falls_under_one_show_filter(self, action, kind):
        assert _event(action, 0, REVIEW).kind is kind


class TestMarks:
    def test_a_paper_seen_here_and_elsewhere_names_only_the_other_projects(self):
        paper, elsewhere = uuid.uuid4(), uuid.uuid4()

        (marks,) = marks_on_papers(PROJECT, {paper: {PROJECT, elsewhere}}, saved=set())

        assert marks.is_seen
        assert marks.seen_in_other_projects == (elsewhere,)
        assert not marks.is_saved

    def test_a_saved_paper_never_opened_is_saved_but_not_seen(self):
        paper = uuid.uuid4()

        (marks,) = marks_on_papers(PROJECT, {}, saved={paper})

        assert marks.is_saved
        assert not marks.is_seen

    def test_a_paper_mark_needs_its_sealed_identity(self):
        with pytest.raises(ValueError, match=r"PaperMark\.identity_sealed"):
            PaperMark(
                user_id=uuid.uuid4(),
                paper_id=uuid.uuid4(),
                kind=PaperMarkKind.SEEN,
                project_id=PROJECT,
                identity_sealed=b"",
                marked_at=NOW,
            )

    def test_a_brief_read_needs_an_aware_time(self):
        with pytest.raises(ValueError, match="timezone"):
            BriefRead(
                user_id=uuid.uuid4(),
                brief_id=uuid.uuid4(),
                project_id=PROJECT,
                read_at=datetime(2026, 11, 19, 10, 0),  # noqa: DTZ001
            )
