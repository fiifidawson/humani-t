"""The `mask` stage, run by the real worker: papers masked before anything screens them.

A fake serves fixed raw texts in place of ingest. The masked result goes to the real
`PaperMaskStore`, sealed under the project's key, and is read back through
`MaskedTextSource` as the screen stage reads it. The document source is a fake that proves
the privacy_review gate path.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Sequence
from uuid import UUID

import pytest

from app.application.masking import MaskStage, PaperMaskStore
from app.domain.discovery import IngestedText, TextKind
from app.domain.encryption import EncryptionContext, KeyDestroyedError
from app.domain.masking import PAPER_ENTITY_TYPES, DocumentAwaitingReview, EntityType
from app.domain.screening import MaskedTextSource
from app.privacy.patterns import PatternDetector
from app.privacy.sanitizer import DetectorSanitizer
from app.storage.base import UnitOfWork
from tests.project_support import send
from tests.run_support import RunTeam, add_rule, make_run_team, run_until_idle

EMAIL = "amina.yusuf@example.org"
MRN = "448-1207"
STAFF = "Kofi Mensah"

ABSTRACT_WITH_CONTACT = (
    f"Cash transfers in Marib reduced wasting. Correspondence: {EMAIL}; case MRN {MRN}."
)
ABSTRACT_WITH_STAFF = f"Field data were collected by {STAFF} in Aden between 2019 and 2021."
PLAIN_ABSTRACT = "School feeding had no effect on attendance (95% CI 0.8-1.1)."


class FakeIngestedTexts:
    """An `IngestedTextSource` serving fixed raw texts, as ingest would have kept them."""

    def __init__(self, texts: Sequence[str]) -> None:
        self.by_paper = {uuid.uuid4(): text for text in texts}
        self.asked: list[tuple[UUID, UUID]] = []

    async def texts_for_run(
        self, uow: UnitOfWork, project_id: UUID, run_id: UUID
    ) -> tuple[IngestedText, ...]:
        self.asked.append((project_id, run_id))
        return tuple(
            IngestedText(paper_id, TextKind.ABSTRACT, text)
            for paper_id, text in self.by_paper.items()
        )

    def paper_id(self, raw: str) -> UUID:
        return next(p for p, text in self.by_paper.items() if text == raw)


class FakeDocuments:
    """A `DocumentsAwaitingReview` with a fixed list: P2's upload flow is not built yet."""

    def __init__(self, waiting: Sequence[DocumentAwaitingReview] = ()) -> None:
        self.waiting = tuple(waiting)

    async def awaiting_review(self, project_id: UUID) -> tuple[DocumentAwaitingReview, ...]:
        return self.waiting


class Lane:
    """The stage under test, the store it writes, and how to read the store back."""

    def __init__(
        self, rt: RunTeam, texts: FakeIngestedTexts, documents: FakeDocuments | None = None
    ) -> None:
        providers = rt.harness.providers
        self.rt = rt
        self.texts = texts
        self.masks = PaperMaskStore(providers.encryption, providers.masking, rt.clock)
        masker = DetectorSanitizer([PatternDetector()], entity_types=PAPER_ENTITY_TYPES)
        self.stage = MaskStage(
            storage=providers.storage,
            texts=texts,
            masks=self.masks,
            rules=providers.masking_rules,
            sanitizer=masker,
            detector_sources=masker.sources,
            entity_types=tuple(PAPER_ENTITY_TYPES),
            documents=documents,
        )

    def run(self) -> str:
        run_id = self.rt.start_run(["mask"])
        self.drain()
        return run_id

    def drain(self) -> None:
        run_until_idle(self.rt.worker({"mask": self.stage}))

    def masked(self, raw: str) -> str | None:
        source: MaskedTextSource[UnitOfWork] = self.masks
        project_id = UUID(self.rt.project_id)

        async def read() -> str | None:
            async with self.rt.harness.providers.storage.unit_of_work() as uow:
                return await source.masked_text(uow, project_id, self.texts.paper_id(raw))

        return asyncio.run(read())

    def stored(self, raw: str):
        async def read():
            async with self.rt.harness.providers.storage.unit_of_work() as uow:
                return await uow.paper_masks.get(self.texts.paper_id(raw))

        return asyncio.run(read())

    def open_spans(self, raw: str) -> list[dict]:
        stored = self.stored(raw)
        assert stored is not None
        context = EncryptionContext(
            UUID(self.rt.project_id), "paper_masks.spans", str(stored.paper_id)
        )

        async def read() -> bytes:
            async with self.rt.harness.providers.storage.unit_of_work() as uow:
                return await self.rt.harness.providers.encryption.decrypt(
                    uow, context, stored.spans_sealed
                )

        spans: list[dict] = json.loads(asyncio.run(read()))
        return spans


def finished_summary(rt: RunTeam, run_id: str) -> dict:
    (finished,) = [e for e in rt.events(run_id) if e.name == "stage.finished"]
    summary: dict = finished.data["summary"]
    return summary


def outcome(rt: RunTeam, run_id: str) -> dict:
    (job,) = [e for e in rt.events(run_id) if e.name == "job.finished"]
    return job.data


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


class TestMaskingPapers:
    def test_each_paper_is_masked_with_the_narrow_list_and_stored(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT, PLAIN_ABSTRACT]))

        run_id = lane.run()

        assert outcome(rt, run_id)["outcome"] == "done"
        assert lane.masked(ABSTRACT_WITH_CONTACT) == (
            "Cash transfers in Marib reduced wasting. Correspondence: [EMAIL_1]; "
            "case MRN [MEDICAL_RECORD_1]."
        )
        assert lane.masked(PLAIN_ABSTRACT) == PLAIN_ABSTRACT

    def test_a_paper_never_masked_reads_as_none(self, rt):
        lane = Lane(rt, FakeIngestedTexts([PLAIN_ABSTRACT]))

        assert lane.masked(PLAIN_ABSTRACT) is None

    def test_the_stored_spans_point_into_the_unchanged_raw_text(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT]))

        lane.run()

        spans = lane.open_spans(ABSTRACT_WITH_CONTACT)
        assert {ABSTRACT_WITH_CONTACT[s["start"] : s["end"]] for s in spans} == {EMAIL, MRN}
        assert {s["source"] for s in spans} == {"patterns@1"}

    def test_texts_are_read_for_this_run_in_this_project(self, rt):
        lane = Lane(rt, FakeIngestedTexts([PLAIN_ABSTRACT]))

        run_id = lane.run()

        assert lane.texts.asked == [(UUID(rt.project_id), UUID(run_id))]

    def test_project_rules_are_applied_to_papers(self, rt):
        add_rule(rt, STAFF)
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_STAFF]))

        lane.run()

        assert lane.masked(ABSTRACT_WITH_STAFF) == (
            "Field data were collected by [PERSON_1] in Aden between 2019 and 2021."
        )

    def test_one_progress_event_per_paper_with_no_text_from_it(self, rt):
        texts = [ABSTRACT_WITH_CONTACT, PLAIN_ABSTRACT, ABSTRACT_WITH_STAFF]
        lane = Lane(rt, FakeIngestedTexts(texts))

        run_id = lane.run()

        progress = [e.data for e in rt.events(run_id) if e.name == "stage.progress"]
        assert [(p["done"], p["total"]) for p in progress] == [(1, 3), (2, 3), (3, 3)]
        assert all(p["item"] == f"Paper {p['done']} of 3" for p in progress)


class TestAtRest:
    def test_masked_text_is_sealed_and_bound_to_its_paper(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT]))
        lane.run()
        stored = lane.stored(ABSTRACT_WITH_CONTACT)

        assert stored is not None
        assert b"Marib" not in stored.masked_sealed
        assert EMAIL.encode() not in stored.spans_sealed

    def test_the_store_never_returns_another_projects_masking(self, rt):
        lane = Lane(rt, FakeIngestedTexts([PLAIN_ABSTRACT]))
        lane.run()
        paper_id = lane.texts.paper_id(PLAIN_ABSTRACT)

        async def read() -> str | None:
            async with rt.harness.providers.storage.unit_of_work() as uow:
                return await lane.masks.masked_text(uow, uuid.uuid4(), paper_id)

        assert asyncio.run(read()) is None

    def test_deleting_the_project_makes_masked_text_unreadable(self, rt):
        lane = Lane(rt, FakeIngestedTexts([PLAIN_ABSTRACT]))
        lane.run()

        send(rt.owner, "DELETE", f"/api/v1/projects/{rt.project_id}")

        with pytest.raises(KeyDestroyedError):
            lane.masked(PLAIN_ABSTRACT)


class TestTheSummary:
    def test_it_counts_papers_and_masked_items_by_type_never_values(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT, PLAIN_ABSTRACT]))

        summary = finished_summary(rt, lane.run())

        assert summary["papers"] == 2
        assert summary["masked"] == 2
        assert summary["skipped"] == 0
        assert summary["masked_items"] == {"EMAIL": 1, "MEDICAL_RECORD": 1}

    def test_its_manifest_names_the_detectors_their_versions_and_the_entity_list(self, rt):
        summary = finished_summary(rt, Lane(rt, FakeIngestedTexts([])).run())

        assert summary["manifest"] == {
            "is_masked": True,
            "detectors": ["patterns@1"],
            "entity_types": sorted(t.value for t in PAPER_ENTITY_TYPES),
            "masking_rules": 0,
        }

    def test_no_event_carries_a_masked_value_or_a_rule_term(self, rt):
        add_rule(rt, STAFF)
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT, ABSTRACT_WITH_STAFF]))

        run_id = lane.run()

        log = json.dumps([e.data for e in rt.events(run_id)])
        for value in (EMAIL, MRN, STAFF, "Marib", "Aden"):
            assert value not in log


class TestReRuns:
    def test_papers_already_masked_the_same_way_are_skipped(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT, PLAIN_ABSTRACT]))
        lane.run()
        first = lane.stored(ABSTRACT_WITH_CONTACT)

        second = lane.run()

        assert finished_summary(rt, second)["skipped"] == 2
        assert finished_summary(rt, second)["masked"] == 0
        assert lane.stored(ABSTRACT_WITH_CONTACT) == first

    def test_a_new_project_rule_masks_every_paper_again(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_STAFF]))
        lane.run()
        assert STAFF in (lane.masked(ABSTRACT_WITH_STAFF) or "")

        add_rule(rt, STAFF)
        run_id = lane.run()

        assert STAFF not in (lane.masked(ABSTRACT_WITH_STAFF) or "")
        assert finished_summary(rt, run_id)["masked"] == 1


WAITING = DocumentAwaitingReview(
    uuid.UUID("0b8f8a1e-7c55-4c1e-9d2b-3f3c0c5c9e11"),
    {EntityType.PERSON: 6, EntityType.LOCATION: 5, EntityType.PHONE: 2},
)


class TestDocumentsAwaitingReview:
    def test_a_project_with_documents_waiting_pauses_at_the_privacy_review_gate(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT]), FakeDocuments([WAITING]))

        run_id = lane.run()

        (gate,) = [e for e in rt.events(run_id) if e.name == "gate.required"]
        assert gate.data["kind"] == "privacy_review"
        assert gate.data["payload"] == {
            "documents": [
                {
                    "document_id": str(WAITING.document_id),
                    "masked_items": {"LOCATION": 5, "PERSON": 6, "PHONE": 2},
                }
            ]
        }
        assert rt.stored_run(run_id).state.value == "waiting"
        assert lane.masked(ABSTRACT_WITH_CONTACT) is None

    def test_approving_the_review_resumes_and_masks_the_papers(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT]), FakeDocuments([WAITING]))
        run_id = lane.run()

        assert rt.resolve(rt.gate_id(run_id), "approve").status_code == 200
        lane.drain()

        assert outcome(rt, run_id)["outcome"] == "done"
        assert lane.masked(ABSTRACT_WITH_CONTACT) is not None

    def test_rejecting_the_review_fails_the_run_with_a_plain_message(self, rt):
        lane = Lane(rt, FakeIngestedTexts([ABSTRACT_WITH_CONTACT]), FakeDocuments([WAITING]))
        run_id = lane.run()

        rt.resolve(rt.gate_id(run_id), "reject")
        lane.drain()

        job = outcome(rt, run_id)
        assert job["outcome"] == "failed"
        assert "privacy review" in job["error"]
        assert lane.masked(ABSTRACT_WITH_CONTACT) is None

    def test_only_an_owner_may_approve_the_privacy_review(self, rt):
        lane = Lane(rt, FakeIngestedTexts([]), FakeDocuments([WAITING]))
        run_id = lane.run()

        assert rt.resolve(rt.gate_id(run_id), "approve", rt.reviewer).status_code == 403

    def test_without_documents_there_is_no_gate(self, rt):
        lane = Lane(rt, FakeIngestedTexts([PLAIN_ABSTRACT]), FakeDocuments())

        run_id = lane.run()

        assert [e for e in rt.events(run_id) if e.name == "gate.required"] == []


class TestProductionWiring:
    def test_the_wired_mask_stage_is_the_real_one(self, rt):
        assert isinstance(rt.harness.providers.stages["mask"], MaskStage)
