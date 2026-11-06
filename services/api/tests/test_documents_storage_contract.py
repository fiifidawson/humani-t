"""One behaviour contract for document storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a database
migrated to 0013, as the runtime role, exactly as in `test_storage_contract.py`.
The contract covers what concurrency depends on: claims skip held rows, saves
are version-checked, a masking replaces only the version it read, a tag number
is never reused, and a review outlives its document.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.documents import (
    Document,
    DocumentDecision,
    DocumentMask,
    DocumentReview,
    DocumentStatus,
    DocumentText,
    DocumentType,
)
from app.domain.errors import ConcurrentUpdateError
from app.domain.projects import Project
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


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


async def _project(storage: StorageProvider) -> tuple[User, Project]:
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
    async with storage.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.projects.add(project)
        await uow.commit()
    return user, project


def _document(user: User, project: Project, number: int, at: datetime = NOW) -> Document:
    return Document(
        id=uuid.uuid4(),
        project_id=project.id,
        number=number,
        name_sealed=b"sealed-name",
        file_type=DocumentType.PDF,
        size_bytes=1234,
        status=DocumentStatus.UPLOADED,
        uploaded_by=user.id,
        uploaded_at=at,
    )


async def _added(storage: StorageProvider, *documents: Document) -> None:
    async with storage.unit_of_work() as uow:
        for document in documents:
            await uow.documents.add(document)
        await uow.commit()


def _mask(document: Document, version: int) -> DocumentMask:
    return DocumentMask(
        document_id=document.id,
        project_id=document.project_id,
        version=version,
        masked_sealed=f"masked-{version}".encode(),
        detail_sealed=b"detail",
        masked_with=("patterns@1", "dates@1"),
        masked_at=NOW,
    )


class TestDocuments:
    async def test_a_committed_document_reads_back_exactly(self, storage):
        user, project = await _project(storage)
        document = _document(user, project, 1)
        await _added(storage, document)

        async with storage.unit_of_work() as uow:
            assert await uow.documents.get(document.id) == document

    async def test_uncommitted_work_is_discarded(self, storage):
        user, project = await _project(storage)
        async with storage.unit_of_work() as uow:
            await uow.documents.add(_document(user, project, 1))

        async with storage.unit_of_work() as uow:
            assert await uow.documents.list_for_project(project.id) == []

    async def test_a_project_lists_only_its_own_documents_by_number(self, storage):
        user, project = await _project(storage)
        _, other = await _project(storage)
        second, first = _document(user, project, 2), _document(user, project, 1)
        await _added(storage, first, second, _document(user, other, 1))

        async with storage.unit_of_work() as uow:
            listed = await uow.documents.list_for_project(project.id)

        assert [d.number for d in listed] == [1, 2]

    async def test_a_tag_number_is_never_reused_even_after_deletion(self, storage):
        user, project = await _project(storage)
        document = _document(user, project, 1)
        await _added(storage, document)
        async with storage.unit_of_work() as uow:
            await uow.documents.delete(document.id)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.next_number(project.id) == 2
            with pytest.raises(ConcurrentUpdateError):
                await uow.documents.add(_document(user, project, 1))

    async def test_a_stale_save_is_refused(self, storage):
        user, project = await _project(storage)
        document = _document(user, project, 1)
        await _added(storage, document)
        async with storage.unit_of_work() as uow:
            await uow.documents.save(document)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.documents.save(document)

    async def test_saved_counts_and_status_read_back(self, storage):
        user, project = await _project(storage)
        document = _document(user, project, 1)
        await _added(storage, document)
        claimed = document.claimed("w", NOW + LEASE)

        async with storage.unit_of_work() as uow:
            saved = await uow.documents.save(claimed)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.get(document.id) == saved
        assert saved.version == document.version + 1


class TestClaims:
    async def test_the_oldest_uploaded_document_is_claimed_once(self, storage):
        user, project = await _project(storage)
        later = _document(user, project, 1, NOW + timedelta(seconds=1))
        oldest = _document(user, project, 2)
        await _added(storage, later, oldest)

        async with storage.unit_of_work() as uow:
            first = await uow.documents.claim_next("worker-a", NOW, LEASE)
            second = await uow.documents.claim_next("worker-a", NOW, LEASE)
            await uow.commit()

        assert first is not None
        assert first.id == oldest.id
        assert first.status is DocumentStatus.SCANNING
        assert first.lease_until == NOW + LEASE
        assert second is not None
        assert second.id == later.id
        async with storage.unit_of_work() as uow:
            assert await uow.documents.claim_next("worker-b", NOW, LEASE) is None

    async def test_only_the_holder_renews_its_lease(self, storage):
        user, project = await _project(storage)
        await _added(storage, _document(user, project, 1))
        async with storage.unit_of_work() as uow:
            claimed = await uow.documents.claim_next("worker-a", NOW, LEASE)
            await uow.commit()
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            assert (
                await uow.documents.renew_lease(claimed.id, "worker-b", NOW + 2 * LEASE)
                is False
            )
            assert await uow.documents.renew_lease(claimed.id, "worker-a", NOW + 2 * LEASE)
            await uow.commit()

    async def test_expired_leases_are_found(self, storage):
        user, project = await _project(storage)
        await _added(storage, _document(user, project, 1))
        async with storage.unit_of_work() as uow:
            claimed = await uow.documents.claim_next("worker-a", NOW, LEASE)
            await uow.commit()
        assert claimed is not None

        async with storage.unit_of_work() as uow:
            assert await uow.documents.lock_expired(NOW, 10) == []
            expired = await uow.documents.lock_expired(NOW + 2 * LEASE, 10)

        assert [d.id for d in expired] == [claimed.id]


class TestTextsAndMasks:
    async def _ready(self, storage: StorageProvider) -> Document:
        user, project = await _project(storage)
        document = _document(user, project, 1)
        await _added(storage, document)
        return document

    async def test_the_text_is_kept_once(self, storage):
        document = await self._ready(storage)
        text = DocumentText(document.id, document.project_id, b"raw", (0, 40), NOW)

        async with storage.unit_of_work() as uow:
            assert await uow.documents.add_text(text) is True
            assert await uow.documents.add_text(text) is False
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.get_text(document.id) == text

    async def test_a_masking_replaces_only_the_version_it_read(self, storage):
        document = await self._ready(storage)
        async with storage.unit_of_work() as uow:
            assert await uow.documents.put_mask(_mask(document, 1), replaces=None)
            assert not await uow.documents.put_mask(_mask(document, 1), replaces=None)
            assert await uow.documents.put_mask(_mask(document, 2), replaces=1)
            assert not await uow.documents.put_mask(_mask(document, 3), replaces=1)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.get_mask(document.id) == _mask(document, 2)

    async def test_deleting_a_document_removes_its_text_and_masking(self, storage):
        document = await self._ready(storage)
        async with storage.unit_of_work() as uow:
            await uow.documents.add_text(
                DocumentText(document.id, document.project_id, b"raw", (0,), NOW)
            )
            await uow.documents.put_mask(_mask(document, 1), replaces=None)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.delete(document.id) is True
            assert await uow.documents.delete(document.id) is False
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.get_text(document.id) is None
            assert await uow.documents.get_mask(document.id) is None

    async def test_a_review_outlives_its_document(self, storage):
        document = await self._ready(storage)
        review = DocumentReview(
            uuid.uuid4(),
            document.id,
            document.project_id,
            DocumentDecision.APPROVE,
            1,
            document.uploaded_by,
            NOW,
        )
        async with storage.unit_of_work() as uow:
            await uow.documents.add_review(review)
            await uow.documents.delete(document.id)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.documents.list_reviews(document.id) == [review]
