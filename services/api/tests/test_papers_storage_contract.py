"""One behaviour contract for paper and screening storage, run against every backend.

A paper is stored once per review and found again by its identity digest, so a re-run
neither duplicates papers nor loses their ids. Writes a re-run repeats are conditional and
say whether they won. The Postgres run is skipped unless TEST_DATABASE_URL points at a
database migrated to revision 0009, as the runtime role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.discovery import (
    PaperSet,
    PaperText,
    RunPaper,
    RunSource,
    SourceOutcome,
    StoredPaper,
    TextKind,
)
from app.domain.projects import Project
from app.domain.reviews import Review, ReviewStatus
from app.domain.runs import Run, RunState
from app.domain.screening import Priority, ScreeningLabel, StoredDecision
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


# Children before parents. audit_events is append-only and is left alone.
@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.postgres,
                pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set"),
            ],
        ),
    ]
)
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[StorageProvider]:
    if request.param == "memory":
        yield MemoryStorage()
        return
    assert _DATABASE_URL is not None
    postgres = PostgresStorage.from_url(_DATABASE_URL)
    yield postgres
    await postgres.close()
    await delete_all_rows(_DATABASE_URL)


async def _run(storage: StorageProvider, review: Review | None = None) -> Run:
    """A committed run, and the user, project and review it needs."""
    async with storage.unit_of_work() as uow:
        if review is None:
            user = User(
                id=uuid.uuid4(),
                email=f"{uuid.uuid4().hex[:12]}@example.org",
                display_name="Test",
                password_hash="fake$x",
                created_at=NOW,
            )
            project = Project(
                id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=user.id
            )
            review = Review(
                id=uuid.uuid4(),
                project_id=project.id,
                question_sealed=b"sealed-question",
                status=ReviewStatus.NEW,
                created_at=NOW,
                created_by=user.id,
            )
            await uow.users.add(user)
            await uow.projects.add(project)
            await uow.reviews.add(review)
        run = Run(
            id=uuid.uuid4(),
            review_id=review.id,
            project_id=review.project_id,
            stages=("discover", "screen"),
            state=RunState.RUNNING,
            created_at=NOW,
            created_by=review.created_by,
            queued_at=NOW,
            lease_owner="worker-a",
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        await uow.runs.add(run)
        await uow.commit()
    return run


async def _review_of(storage: StorageProvider, run: Run) -> Review:
    async with storage.unit_of_work() as uow:
        review = await uow.reviews.get(run.review_id)
    assert review is not None
    return review


def _paper(run: Run, digest: bytes | None = None) -> StoredPaper:
    return StoredPaper(
        id=uuid.uuid4(),
        project_id=run.project_id,
        review_id=run.review_id,
        first_run_id=run.id,
        identity_digest=digest or uuid.uuid4().bytes,
        source_id="openalex",
        record_sealed=b"sealed-record",
        created_at=NOW,
    )


async def _stored_papers(storage: StorageProvider, run: Run, count: int) -> list[StoredPaper]:
    papers = [_paper(run) for _ in range(count)]
    async with storage.unit_of_work() as uow:
        for paper in papers:
            await uow.papers.add(paper)
        await uow.commit()
    return papers


def _decision(run: Run, paper: StoredPaper, **changes: object) -> StoredDecision:
    values: dict[str, object] = {
        "run_id": run.id,
        "paper_id": paper.id,
        "project_id": run.project_id,
        "label": ScreeningLabel.INCLUDE,
        "priority": Priority.HIGH,
        "score": 0.875,
        "criteria_matched": ("population", "intervention"),
        "exclusion_reasons": (),
        "model_id": "llama3.1:8b",
        "prompt_version": "screen-1",
        "criteria_version": 2,
        "evidence_kind": TextKind.ABSTRACT,
        "is_unreadable": False,
        "decided_at": NOW,
        "rationale_sealed": b"sealed-rationale",
    }
    values.update(changes)
    return StoredDecision(**values)  # type: ignore[arg-type]


class TestPapers:
    async def test_a_stored_paper_is_read_back_exactly(self, storage):
        run = await _run(storage)
        (paper,) = await _stored_papers(storage, run, 1)

        async with storage.unit_of_work() as uow:
            assert await uow.papers.get(paper.id) == paper

    async def test_a_review_finds_its_papers_by_identity_digest(self, storage):
        run = await _run(storage)
        first, second, _ = await _stored_papers(storage, run, 3)

        async with storage.unit_of_work() as uow:
            found = await uow.papers.find_by_identity(
                run.review_id, [first.identity_digest, second.identity_digest, b"unknown"]
            )

        assert {p.id for p in found} == {first.id, second.id}

    async def test_another_reviews_paper_is_never_found(self, storage):
        run = await _run(storage)
        other = await _run(storage)
        (paper,) = await _stored_papers(storage, other, 1)

        async with storage.unit_of_work() as uow:
            found = await uow.papers.find_by_identity(run.review_id, [paper.identity_digest])

        assert found == []

    async def test_uncommitted_papers_are_discarded(self, storage):
        run = await _run(storage)
        paper = _paper(run)
        async with storage.unit_of_work() as uow:
            await uow.papers.add(paper)

        async with storage.unit_of_work() as uow:
            assert await uow.papers.get(paper.id) is None


class TestPaperSets:
    async def test_a_run_records_its_paper_set_once(self, storage):
        run = await _run(storage)
        own = PaperSet(run.id, run.id, NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.papers.add_set(own)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            assert not await uow.papers.add_set(PaperSet(run.id, run.id, NOW + timedelta(1)))
            await uow.commit()
        async with storage.unit_of_work() as uow:
            assert await uow.papers.get_set(run.id) == own

    async def test_a_later_run_can_adopt_an_earlier_runs_set(self, storage):
        first = await _run(storage)
        second = await _run(storage, await _review_of(storage, first))
        async with storage.unit_of_work() as uow:
            await uow.papers.add_set(PaperSet(first.id, first.id, NOW))
            await uow.papers.add_set(PaperSet(second.id, first.id, NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            adopted = await uow.papers.get_set(second.id)

        assert adopted is not None
        assert adopted.basis_run_id == first.id
        assert not adopted.is_own

    async def test_a_run_without_a_set_has_none(self, storage):
        run = await _run(storage)

        async with storage.unit_of_work() as uow:
            assert await uow.papers.get_set(run.id) is None


class TestRunPapersAndSources:
    async def test_a_runs_papers_come_back_in_their_order(self, storage):
        run = await _run(storage)
        papers = await _stored_papers(storage, run, 3)
        entries = [
            RunPaper(run.id, papers[2].id, 1, ("openalex", "arxiv")),
            RunPaper(run.id, papers[0].id, 2, ("arxiv",)),
            RunPaper(run.id, papers[1].id, 3, ("openalex",)),
        ]
        async with storage.unit_of_work() as uow:
            await uow.papers.add_run_papers(list(reversed(entries)))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.papers.list_run_papers(run.id) == entries

    async def test_one_paper_seen_by_two_runs_keeps_one_id(self, storage):
        first = await _run(storage)
        second = await _run(storage, await _review_of(storage, first))
        (paper,) = await _stored_papers(storage, first, 1)
        async with storage.unit_of_work() as uow:
            await uow.papers.add_run_papers([RunPaper(first.id, paper.id, 1, ("openalex",))])
            await uow.papers.add_run_papers([RunPaper(second.id, paper.id, 4, ("arxiv",))])
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert [p.paper_id for p in await uow.papers.list_run_papers(second.id)] == [
                paper.id
            ]
            assert [p.position for p in await uow.papers.list_run_papers(first.id)] == [1]

    async def test_a_run_records_each_source_it_asked(self, storage):
        run = await _run(storage)
        sources = [
            RunSource(run.id, "openalex", SourceOutcome.SEARCHED, 12, 3, 2, b"sealed-q1", NOW),
            RunSource(run.id, "arxiv", SourceOutcome.FAILED, 0, 3, 2, b"sealed-q2", NOW),
        ]
        async with storage.unit_of_work() as uow:
            await uow.papers.add_run_sources(sources)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            stored = await uow.papers.list_run_sources(run.id)

        assert sorted(stored, key=lambda s: s.source_id) == sorted(
            sources, key=lambda s: s.source_id
        )


class TestTexts:
    async def test_a_papers_text_is_stored_once(self, storage):
        run = await _run(storage)
        (paper,) = await _stored_papers(storage, run, 1)
        kept = PaperText(paper.id, run.project_id, TextKind.ABSTRACT, b"sealed-text", NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.papers.add_text(kept)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            again = PaperText(paper.id, run.project_id, TextKind.TITLE, b"other", NOW)
            assert not await uow.papers.add_text(again)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.papers.list_texts([paper.id]) == [kept]

    async def test_texts_are_listed_only_for_the_papers_asked_about(self, storage):
        run = await _run(storage)
        first, second = await _stored_papers(storage, run, 2)
        async with storage.unit_of_work() as uow:
            for paper in (first, second):
                await uow.papers.add_text(
                    PaperText(paper.id, run.project_id, TextKind.ABSTRACT, b"sealed", NOW)
                )
            await uow.commit()

        async with storage.unit_of_work() as uow:
            texts = await uow.papers.list_texts([second.id])

        assert [t.paper_id for t in texts] == [second.id]


class TestDecisions:
    async def test_a_decision_is_read_back_with_all_its_provenance(self, storage):
        run = await _run(storage)
        (paper,) = await _stored_papers(storage, run, 1)
        decision = _decision(run, paper)

        async with storage.unit_of_work() as uow:
            assert await uow.screening.add(decision)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.screening.get(run.id, paper.id) == decision

    async def test_a_decision_keeps_its_sealed_criterion_answers(self, storage):
        run = await _run(storage)
        (paper,) = await _stored_papers(storage, run, 1)
        decision = _decision(run, paper, answers_sealed=b"sealed-answers")

        async with storage.unit_of_work() as uow:
            assert await uow.screening.add(decision)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.screening.get(run.id, paper.id) == decision
            assert await uow.screening.list_for_run(run.id) == [decision]

    async def test_a_run_decides_a_paper_once(self, storage):
        run = await _run(storage)
        (paper,) = await _stored_papers(storage, run, 1)
        first = _decision(run, paper)
        async with storage.unit_of_work() as uow:
            await uow.screening.add(first)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            second = _decision(
                run,
                paper,
                label=ScreeningLabel.EXCLUDE,
                exclusion_reasons=("outcome",),
            )
            assert not await uow.screening.add(second)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.screening.get(run.id, paper.id) == first

    async def test_another_run_decides_the_same_paper_afresh(self, storage):
        first = await _run(storage)
        second = await _run(storage, await _review_of(storage, first))
        (paper,) = await _stored_papers(storage, first, 1)
        async with storage.unit_of_work() as uow:
            assert await uow.screening.add(_decision(first, paper))
            assert await uow.screening.add(
                _decision(second, paper, label=ScreeningLabel.UNCERTAIN, is_unreadable=True)
            )
            await uow.commit()

        async with storage.unit_of_work() as uow:
            decided = await uow.screening.list_for_run(second.id)

        assert [(d.paper_id, d.label) for d in decided] == [
            (paper.id, ScreeningLabel.UNCERTAIN)
        ]

    async def test_a_runs_decisions_are_listed_without_another_runs(self, storage):
        run = await _run(storage)
        other = await _run(storage)
        papers = await _stored_papers(storage, run, 2)
        (elsewhere,) = await _stored_papers(storage, other, 1)
        async with storage.unit_of_work() as uow:
            for paper in papers:
                await uow.screening.add(_decision(run, paper))
            await uow.screening.add(_decision(other, elsewhere))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            decided = await uow.screening.list_for_run(run.id)

        assert {d.paper_id for d in decided} == {p.id for p in papers}
