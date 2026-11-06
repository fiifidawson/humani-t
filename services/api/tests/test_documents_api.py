"""A team's own documents end to end: processed, masked, reviewed, approved, used, deleted.

Sources: the brief's rules 2, 3, 4, 12, 13 and 17; architecture §3.9.1 (masking
reviewed before use; raw text only to the owner, audited; "not personal" never
for structured identifiers), §4.4 (the privacy_review gate), §5.4 (sealed and
shredded with the project), §5.5 (ids-only audit) and §5.7 (no connection held
across slow work); UI_UX_SPEC §3.3, §6.6, §6.13 and §8.4.

Requests go through the real middleware, routes, storage, encryption and audit
log; the worker is the real `DocumentProcessor`. The seams are the fake
scanner (it finds EICAR for real), the in-thread local extractor and an
in-memory file store that records how many transactions were open.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from app.domain.documents import DocumentFailure, DocumentStatus
from app.domain.encryption import EncryptionContext, KeyDestroyedError
from app.domain.screening import PassageSourceKind
from app.extraction.fake import FakeDocumentExtractor
from app.malware.fake import EICAR, FakeMalwareScanner
from app.storage.base import UnitOfWork
from tests.document_support import (
    DocumentTeam,
    docx,
    make_document_team,
    pdf,
    stored_document,
)
from tests.project_support import project_events, send

EMAIL = "amina.yusuf@example.org"
REPORT = (
    f"Field report. Interviewed on 12 March 2024 at the clinic. Contact {EMAIL} "
    "or Kofi Mensah, who leads the team. MRN 448-1207 was referred."
)
STAFF = "Kofi Mensah"


@pytest.fixture
def team() -> DocumentTeam:
    return make_document_team()


def _review(team: DocumentTeam, document_id: str) -> dict[str, Any]:
    response = team.owner.get(team.path(f"/{document_id}/review"))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _span(review: dict[str, Any], text: str) -> dict[str, Any]:
    raw = review["raw_text"]
    start = raw.index(text)
    return next(s for s in review["spans"] if s["start"] == start)


def _approve(team: DocumentTeam, document_id: str, version: int = 1):
    return send(team.owner, "POST", team.path(f"/{document_id}/approve"), {"version": version})


def _passages(team: DocumentTeam) -> list[Any]:
    providers = team.harness.providers
    from app.application.documents import DocumentEvidence

    evidence = DocumentEvidence(providers.encryption)

    async def read() -> list[Any]:
        async with providers.storage.unit_of_work() as uow:
            return list(await evidence.approved_passages(uow, UUID(team.project_id)))

    return asyncio.run(read())


class TestProcessing:
    def test_an_upload_returns_at_once_and_waits_for_the_worker(self, team: DocumentTeam):
        response = team.upload(REPORT.encode())

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "uploaded"
        assert body["tag"] == "D1"
        assert response.headers["Location"].endswith(f"/documents/{body['id']}")

    def test_the_worker_scans_reads_and_masks_with_the_broad_list(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())

        assert team.process() == 1

        listed = team.owner.get(team.path(f"/{document_id}")).json()
        assert listed["status"] == "awaiting_review"
        assert listed["scanned_with"] == "fake"
        assert listed["mask_version"] == 1
        assert listed["masked_items"] == {"DATE": 1, "EMAIL": 1, "MEDICAL_RECORD": 1}

    def test_the_masked_text_any_member_reads_holds_no_personal_data(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        body = team.viewer.get(team.path(f"/{document_id}/masked")).json()

        assert EMAIL not in body["masked_text"]
        assert "448-1207" not in body["masked_text"]
        assert "12 March 2024" not in body["masked_text"]
        assert "[EMAIL_1]" in body["masked_text"]
        assert "raw_text" not in body
        item = next(i for i in body["items"] if i["entity_type"] == "DATE")
        assert body["masked_text"][item["start"] : item["end"]] == item["placeholder"]
        assert item["sources"] == ["dates@1"]

    def test_project_rules_reach_documents_too(self, team: DocumentTeam):
        send(team.owner, "POST", team.team.path("/masking-rules"), {"term": STAFF})
        document_id = team.uploaded(REPORT.encode())
        team.process()

        body = team.viewer.get(team.path(f"/{document_id}/masked")).json()

        assert STAFF not in body["masked_text"]
        assert any(s.startswith("rule:") for i in body["items"] for s in i["sources"])

    def test_pdf_and_docx_are_read_too(self, team: DocumentTeam):
        first = team.uploaded(pdf("Seen on 12 March 2024.", "Page two."), "report.pdf")
        second = team.uploaded(docx("Seen on 12 March 2024."), "report.docx")
        team.process()

        for document_id in (first, second):
            body = team.viewer.get(team.path(f"/{document_id}/masked")).json()
            assert "[DATE_1]" in body["masked_text"]
        assert team.owner.get(team.path(f"/{first}")).json()["page_count"] == 2

    def test_an_infected_file_is_deleted_unread_and_fails_plainly(self, team: DocumentTeam):
        document_id = team.uploaded(b"Report. " + EICAR)

        team.process()

        body = team.owner.get(team.path(f"/{document_id}")).json()
        assert body["status"] == "failed"
        assert body["failure"] == "malware_found"
        assert "deleted unread" in body["failure_message"]
        assert team.files.files == {}

    def test_a_file_that_cannot_be_read_fails_with_a_fixed_reason(self):
        team = make_document_team(
            extractor=FakeDocumentExtractor(failure=DocumentFailure.ENCRYPTED)
        )
        document_id = team.uploaded(b"anything")

        team.process()

        body = team.owner.get(team.path(f"/{document_id}")).json()
        assert body["status"] == "failed"
        assert body["failure"] == "encrypted"

    def test_an_unavailable_scanner_leaves_the_document_queued_unread(self):
        scanner = FakeMalwareScanner(is_available=False)
        team = make_document_team(scanner=scanner)
        document_id = team.uploaded(REPORT.encode())

        team.process()

        assert team.owner.get(team.path(f"/{document_id}")).json()["status"] == "uploaded"
        scanner.is_available = True
        team.process()
        assert (
            team.owner.get(team.path(f"/{document_id}")).json()["status"] == "awaiting_review"
        )

    def test_a_document_whose_worker_died_is_queued_again_then_failed(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        processor = team.harness.providers.document_processor

        async def claim_and_die() -> None:
            async with team.harness.providers.storage.unit_of_work() as uow:
                await uow.documents.claim_next(
                    "dead-worker",
                    team.harness.clock(),
                    processor._lease,
                )
                await uow.commit()

        for _ in range(3):
            asyncio.run(claim_and_die())
            team.harness.clock.advance(minutes=10)
            asyncio.run(processor.recover_expired())

        stored = stored_document(team, document_id)
        assert stored.status is DocumentStatus.FAILED
        assert stored.failure is DocumentFailure.INTERRUPTED

    def test_no_database_connection_is_held_across_file_io_scanning_or_extraction(self):
        # The lambda reads `team` when the extractor runs, after it is assigned.
        extractor_seen = FakeDocumentExtractor(
            open_units=lambda: team.harness.storage.open_units
        )
        team = make_document_team(extractor=extractor_seen)

        team.uploaded(REPORT.encode())
        team.process()

        assert team.files.open_units_seen
        assert set(team.files.open_units_seen) == {0}
        assert set(team.scanner.open_units_seen) == {0}  # type: ignore[attr-defined]
        assert extractor_seen.open_units_seen == [0]


class TestReview:
    def test_only_an_owner_sees_the_raw_text(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        assert team.reviewer.get(team.path(f"/{document_id}/review")).status_code == 403
        assert team.viewer.get(team.path(f"/{document_id}/review")).status_code == 403
        assert _review(team, document_id)["raw_text"] == REPORT

    def test_every_opening_of_raw_text_is_audited_with_ids_only(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode(), "interview_amina.txt")
        team.process()
        _review(team, document_id)
        _review(team, document_id)

        events = project_events(team.harness, team.project_id)

        viewed = [e for e in events if e.entry.action == "document.raw_text_viewed"]
        assert len(viewed) == 2
        assert {e.entry.target_id for e in viewed} == {document_id}
        assert "amina" not in repr(events).lower()

    def test_the_review_says_what_made_each_mask_and_which_may_be_removed(
        self, team: DocumentTeam
    ):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        review = _review(team, document_id)

        email = _span(review, EMAIL)
        date = _span(review, "12 March 2024")
        assert email["source"] == "patterns@1"
        assert email["can_remove"] is False
        assert date["can_remove"] is True
        assert "recorded" in review["notice"]

    def test_masking_missed_text_makes_a_new_version_that_needs_review(
        self, team: DocumentTeam
    ):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        start = REPORT.index(STAFF)

        response = send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/masks"),
            {"version": 1, "start": start, "end": start + len(STAFF), "entity_type": "PERSON"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mask_version"] == 2
        assert STAFF not in body["masked_text"]
        added = _span(body, STAFF)
        assert added["source"] == f"user:{team.team.user_id('amara@example.org')}"
        masked = team.viewer.get(team.path(f"/{document_id}/masked")).json()
        assert STAFF not in masked["masked_text"]

    def test_a_correction_against_an_old_version_is_a_conflict(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        start = REPORT.index(STAFF)
        body = {"version": 1, "start": start, "end": start + 4, "entity_type": "PERSON"}
        send(team.owner, "POST", team.path(f"/{document_id}/masks"), body)

        again = send(team.owner, "POST", team.path(f"/{document_id}/masks"), body)

        assert again.status_code == 409

    def test_a_date_can_be_marked_not_personal_with_a_reason(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        date = _span(_review(team, document_id), "12 March 2024")

        response = send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/unmask"),
            {
                "version": 1,
                "start": date["start"],
                "end": date["end"],
                "reason": "The date of a public event",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "12 March 2024" in body["masked_text"]
        (removed,) = body["removed"]
        assert removed["reason"] == "The date of a public event"
        assert removed["source"] == "dates@1"

    def test_a_structured_identifier_can_never_be_unmasked(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        email = _span(_review(team, document_id), EMAIL)

        response = send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/unmask"),
            {"version": 1, "start": email["start"], "end": email["end"], "reason": "public"},
        )

        assert response.status_code == 422
        assert EMAIL not in team.viewer.get(team.path(f"/{document_id}/masked")).text

    def test_the_removal_reason_is_stored_only_sealed(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        date = _span(_review(team, document_id), "12 March 2024")
        send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/unmask"),
            {
                "version": 1,
                "start": date["start"],
                "end": date["end"],
                "reason": "Zanzibar note",
            },
        )

        state = team.harness.storage.state.document_state
        assert b"Zanzibar" not in repr(state.masks).encode()
        assert b"Fieldwork" not in repr(state.texts).encode()

    def test_reviewers_cannot_correct_masks(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        response = send(
            team.reviewer,
            "POST",
            team.path(f"/{document_id}/masks"),
            {"version": 1, "start": 0, "end": 5, "entity_type": "PERSON"},
        )

        assert response.status_code == 403


class TestApproval:
    def test_approving_records_who_approved_which_version_and_when(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        response = _approve(team, document_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        review = body["last_review"]
        assert review["decision"] == "approve"
        assert review["mask_version"] == 1
        assert review["decided_by"] == team.team.user_id("amara@example.org")
        assert review["decided_at"] == team.harness.clock().isoformat().replace("+00:00", "Z")

    def test_approving_a_version_no_longer_current_is_a_conflict(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        start = REPORT.index(STAFF)
        send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/masks"),
            {"version": 1, "start": start, "end": start + 4, "entity_type": "PERSON"},
        )

        assert _approve(team, document_id, version=1).status_code == 409
        assert _approve(team, document_id, version=2).status_code == 200

    def test_a_document_still_being_read_cannot_be_approved(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())

        assert _approve(team, document_id).status_code == 409

    def test_only_an_owner_approves(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        response = send(
            team.reviewer, "POST", team.path(f"/{document_id}/approve"), {"version": 1}
        )

        assert response.status_code == 403

    def test_a_correction_after_approval_withdraws_it_from_the_evidence(
        self, team: DocumentTeam
    ):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        _approve(team, document_id)
        assert _passages(team)
        start = REPORT.index(STAFF)

        send(
            team.owner,
            "POST",
            team.path(f"/{document_id}/masks"),
            {"version": 1, "start": start, "end": start + 4, "entity_type": "PERSON"},
        )

        assert team.owner.get(team.path(f"/{document_id}")).json()["status"] == (
            "awaiting_review"
        )
        assert _passages(team) == []

    def test_rejecting_keeps_it_out_of_the_evidence(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        response = send(team.owner, "POST", team.path(f"/{document_id}/reject"), {"version": 1})

        assert response.json()["status"] == "rejected"
        assert _passages(team) == []

    def test_decisions_are_audited_with_ids_only(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode(), "kofi_mensah_interview.txt")
        team.process()
        _approve(team, document_id)

        events = project_events(team.harness, team.project_id)
        actions = [e.entry.action for e in events if e.entry.target_type == "document"]

        assert actions == [
            "document.uploaded",
            "document.ready_for_review",
            "document.approved",
        ]
        assert "kofi" not in repr(events).lower()
        assert EMAIL not in repr(events)


class TestEvidence:
    def test_nothing_unapproved_ever_becomes_evidence(self, team: DocumentTeam):
        team.uploaded(REPORT.encode())
        team.process()

        assert _passages(team) == []

    def test_an_approved_document_gives_masked_passages_tagged_with_its_d_tag(
        self, team: DocumentTeam
    ):
        team.uploaded(b"Unrelated first document.")
        document_id = team.uploaded(REPORT.encode())
        team.process()
        _approve(team, document_id)

        passages = _passages(team)

        assert {p.tag for p in passages} == {"D2"}
        assert all(p.source_kind is PassageSourceKind.DOCUMENT for p in passages)
        assert all(p.paper_id == UUID(document_id) for p in passages)
        text = " ".join(p.text for p in passages)
        assert EMAIL not in text
        assert "[EMAIL_1]" in text
        assert passages[0].locator.startswith("characters 0-")

    def test_a_pdfs_passages_name_their_page(self, team: DocumentTeam):
        document_id = team.uploaded(pdf("Cash on page one.", "Vouchers on page two."), "a.pdf")
        team.process()
        _approve(team, document_id)

        locators = [p.locator for p in _passages(team)]

        assert locators == ["page 1", "page 2"] or locators == ["pages 1-2"]

    def test_the_pipelines_evidence_source_carries_approved_documents(self, team: DocumentTeam):
        from app.application.papers import ScreenedEvidence

        answer = team.harness.providers.stages["answer"]
        evidence = answer._evidence  # type: ignore[attr-defined]

        assert isinstance(evidence, ScreenedEvidence)
        assert evidence._documents is not None


class TestDeletion:
    def test_deleting_a_document_shreds_its_file_and_text_and_is_audited(
        self, team: DocumentTeam
    ):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        response = send(team.owner, "DELETE", team.path(f"/{document_id}"))

        assert response.status_code == 204
        assert team.files.files == {}
        state = team.harness.storage.state.document_state
        assert UUID(document_id) not in state.texts
        assert UUID(document_id) not in state.masks
        assert team.owner.get(team.path(f"/{document_id}")).status_code == 404
        actions = [e.entry.action for e in project_events(team.harness, team.project_id)]
        assert actions[-1] == "document.deleted"

    def test_a_deleted_documents_tag_is_never_reused(self, team: DocumentTeam):
        first = team.uploaded(b"one")
        send(team.owner, "DELETE", team.path(f"/{first}"))

        assert team.upload(b"two").json()["tag"] == "D2"

    def test_only_an_owner_deletes(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())

        assert send(team.reviewer, "DELETE", team.path(f"/{document_id}")).status_code == 403

    def test_deleting_the_project_makes_the_file_and_text_unreadable(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        ((key, sealed),) = team.files.files.items()
        stored_text = team.harness.storage.state.document_state.texts[UUID(document_id)]

        send(team.owner, "DELETE", team.team.path())

        providers = team.harness.providers
        context = EncryptionContext(UUID(team.project_id), "documents.file", document_id)

        async def open_file() -> bytes:
            async with providers.storage.unit_of_work() as uow:
                return await providers.encryption.decrypt(uow, context, sealed)

        with pytest.raises(KeyDestroyedError):
            asyncio.run(open_file())
        raw_context = EncryptionContext(
            UUID(team.project_id), "document_texts.raw", document_id
        )

        async def open_text() -> bytes:
            async with providers.storage.unit_of_work() as uow:
                return await providers.encryption.decrypt(
                    uow, raw_context, stored_text.raw_sealed
                )

        with pytest.raises(KeyDestroyedError):
            asyncio.run(open_text())
        assert key.startswith(team.project_id)


class TestSealing:
    def test_the_stored_file_is_ciphertext_bound_to_its_document(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        (sealed,) = team.files.files.values()

        assert EMAIL.encode() not in sealed
        providers = team.harness.providers
        other = EncryptionContext(UUID(team.project_id), "documents.file", str(UUID(int=7)))

        async def open_elsewhere() -> bytes:
            async with providers.storage.unit_of_work() as uow:
                return await providers.encryption.decrypt(uow, other, sealed)

        with pytest.raises(Exception):  # noqa: B017, PT011 — any authentication failure
            asyncio.run(open_elsewhere())
        assert document_id

    def test_names_and_texts_are_stored_only_sealed(self, team: DocumentTeam):
        team.uploaded(REPORT.encode(), "interview_amina_yusuf.txt")
        team.process()

        state = team.harness.storage.state.document_state
        dumped = repr(state.documents) + repr(state.texts) + repr(state.masks)
        assert "amina" not in dumped.lower()
        assert "Field report" not in dumped


class TestListing:
    def test_members_list_documents_with_their_status_and_the_limits(self, team: DocumentTeam):
        team.uploaded(REPORT.encode(), "report.txt")

        body = team.viewer.get(team.path()).json()

        (listed,) = body["documents"]
        assert listed["name"] == "report.txt"
        assert listed["status"] == "uploaded"
        assert body["usage"]["documents"] == 1
        assert body["usage"]["size_bytes"] == len(REPORT.encode())
        assert body["accepted_types"] == ["pdf", "docx", "txt", "md"]
        assert body["uploads_enabled"] is True

    def test_a_document_of_another_project_is_not_found_here(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        other = send(team.owner, "POST", "/api/v1/projects", {"name": "Other"}).json()["id"]

        response = team.owner.get(f"/api/v1/projects/{other}/documents/{document_id}")

        assert response.status_code == 404

    def test_masked_text_before_masking_is_not_found(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())

        assert team.viewer.get(team.path(f"/{document_id}/masked")).status_code == 404


class TestStoredReviewsArePermanent:
    def test_the_approval_outlives_the_deleted_document(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()
        _approve(team, document_id)
        send(team.owner, "DELETE", team.path(f"/{document_id}"))

        async def reviews() -> list[Any]:
            async with team.harness.providers.storage.unit_of_work() as uow:
                return await uow.documents.list_reviews(UUID(document_id))

        (review,) = asyncio.run(reviews())
        assert review.mask_version == 1


def _detail(team: DocumentTeam, document_id: str) -> dict[str, Any]:
    providers = team.harness.providers

    async def read(uow: UnitOfWork) -> bytes:
        mask = await uow.documents.get_mask(UUID(document_id))
        assert mask is not None
        context = EncryptionContext(
            UUID(team.project_id), "document_masks.detail", f"{document_id}:v{mask.version}"
        )
        return await providers.encryption.decrypt(uow, context, mask.detail_sealed)

    async def run() -> bytes:
        async with providers.storage.unit_of_work() as uow:
            return await read(uow)

    detail: dict[str, Any] = json.loads(asyncio.run(run()))
    return detail


class TestMaskVersionsAreBound:
    def test_each_masked_version_is_sealed_to_its_version(self, team: DocumentTeam):
        document_id = team.uploaded(REPORT.encode())
        team.process()

        detail = _detail(team, document_id)

        assert detail["masked_page_starts"] == [0]
        assert {s["source"] for s in detail["spans"]} >= {"patterns@1", "dates@1"}
