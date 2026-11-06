"""The privacy_review gate, fed by real uploaded documents.

Sources: architecture §3.9.1 and §4.4 (an uploaded document is not used until a
person approves its masking; the run waits for it without a worker) and
UI_UX_SPEC §6.6 ("Approving the last document resumes the run").

The worker is the real `RunWorker` and the documents come from the real
upload route and `DocumentProcessor`, with the production extractor: a child
process. Only the papers' raw text is a stand-in, as in `test_mask_stage.py`.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.application.documents import DocumentReviewQueue
from app.application.masking import MaskStage, PaperMaskStore
from app.domain.discovery import IngestedText
from app.domain.masking import PAPER_ENTITY_TYPES
from app.privacy.patterns import PatternDetector
from app.privacy.sanitizer import DetectorSanitizer
from app.storage.base import UnitOfWork
from tests.project_support import send
from tests.run_support import RunTeam, make_run_team, run_until_idle
from tests.support import csrf_headers

REPORT = b"Interview on 12 March 2024 with amina.yusuf@example.org about the clinic."


class _NoPapers:
    async def texts_for_run(
        self, uow: UnitOfWork, project_id: UUID, run_id: UUID
    ) -> tuple[IngestedText, ...]:
        return ()


@pytest.fixture
def rt() -> RunTeam:
    return make_run_team()


def _mask_stage(rt: RunTeam) -> MaskStage:
    providers = rt.harness.providers
    masker = DetectorSanitizer([PatternDetector()], entity_types=PAPER_ENTITY_TYPES)
    return MaskStage(
        storage=providers.storage,
        texts=_NoPapers(),
        masks=PaperMaskStore(providers.encryption, rt.clock),
        rules=providers.masking_rules,
        sanitizer=masker,
        detector_sources=masker.sources,
        entity_types=tuple(PAPER_ENTITY_TYPES),
        documents=DocumentReviewQueue(providers.storage),
    )


def _upload_and_process(rt: RunTeam, content: bytes = REPORT) -> str:
    path = f"/api/v1/projects/{rt.project_id}/documents"
    response = rt.owner.post(
        path,
        files={"file": ("report.txt", content, "text/plain")},
        headers=csrf_headers(rt.owner),
    )
    assert response.status_code == 202, response.text
    asyncio.run(rt.harness.providers.document_processor.run_once())
    document_id: str = response.json()["id"]
    return document_id


def _decide(rt: RunTeam, document_id: str, decision: str) -> int:
    path = f"/api/v1/projects/{rt.project_id}/documents/{document_id}/{decision}"
    return send(rt.owner, "POST", path, {"version": 1}).status_code


def _outcome(rt: RunTeam, run_id: str) -> str:
    (finished,) = [e for e in rt.events(run_id) if e.name == "job.finished"]
    outcome: str = finished.data["outcome"]
    return outcome


class TestPrivacyReviewGate:
    def test_a_document_waiting_for_review_pauses_the_run_with_counts_only(self, rt: RunTeam):
        document_id = _upload_and_process(rt)
        worker = rt.worker({"mask": _mask_stage(rt)})

        run_id = rt.start_run(["mask"])
        run_until_idle(worker)

        (gate,) = [e for e in rt.events(run_id) if e.name == "gate.required"]
        assert gate.data["kind"] == "privacy_review"
        assert gate.data["payload"] == {
            "documents": [{"document_id": document_id, "masked_items": {"DATE": 1, "EMAIL": 1}}]
        }
        assert "amina" not in str(gate.data)
        assert rt.stored_run(run_id).state.value == "waiting"

    def test_approving_the_last_document_resumes_the_run(self, rt: RunTeam):
        first = _upload_and_process(rt)
        second = _upload_and_process(rt, b"Second note, 3 June 2025.")
        worker = rt.worker({"mask": _mask_stage(rt)})
        run_id = rt.start_run(["mask"])
        run_until_idle(worker)

        assert _decide(rt, first, "approve") == 200
        assert rt.stored_run(run_id).state.value == "waiting"
        assert _decide(rt, second, "reject") == 200
        run_until_idle(worker)

        resolved = [e for e in rt.events(run_id) if e.name == "gate.resolved"]
        assert [e.data["decision"] for e in resolved] == ["approve"]
        assert _outcome(rt, run_id) == "done"

    def test_without_documents_waiting_there_is_no_gate(self, rt: RunTeam):
        document_id = _upload_and_process(rt)
        _decide(rt, document_id, "approve")
        worker = rt.worker({"mask": _mask_stage(rt)})

        run_id = rt.start_run(["mask"])
        run_until_idle(worker)

        assert [e for e in rt.events(run_id) if e.name == "gate.required"] == []
        assert _outcome(rt, run_id) == "done"

    def test_the_wired_mask_stage_reads_the_real_review_queue(self, rt: RunTeam):
        stage = rt.harness.providers.stages["mask"]

        assert isinstance(stage, MaskStage)
        assert isinstance(stage._documents, DocumentReviewQueue)
