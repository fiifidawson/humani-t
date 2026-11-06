"""One behaviour contract for masking-rule storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a database
migrated to 0010, as the runtime role, exactly as in `test_storage_contract.py`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.accounts import User
from app.domain.masking import EntityType, MaskingRuleRecord, PaperMask
from app.domain.projects import Project
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
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


def _rule(
    user: User,
    project: Project,
    *,
    at: datetime = NOW,
    entity_type: EntityType = EntityType.PERSON,
) -> MaskingRuleRecord:
    return MaskingRuleRecord(
        id=uuid.uuid4(),
        project_id=project.id,
        term_sealed=b"sealed-term",
        entity_type=entity_type,
        created_by=user.id,
        created_at=at,
    )


class TestMaskingRules:
    async def test_a_committed_rule_is_visible_to_the_next_unit_of_work(self, storage):
        user, project = await _project(storage)
        rule = _rule(user, project, entity_type=EntityType.LOCATION)
        async with storage.unit_of_work() as uow:
            await uow.masking_rules.add(rule)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.masking_rules.get(rule.id) == rule

    async def test_uncommitted_work_is_discarded(self, storage):
        user, project = await _project(storage)
        rule = _rule(user, project)
        async with storage.unit_of_work() as uow:
            await uow.masking_rules.add(rule)

        async with storage.unit_of_work() as uow:
            assert await uow.masking_rules.get(rule.id) is None

    async def test_a_project_lists_only_its_own_rules_oldest_first(self, storage):
        user, project = await _project(storage)
        _, other = await _project(storage)
        later = _rule(user, project, at=NOW + timedelta(minutes=1))
        earlier = _rule(user, project)
        foreign = _rule(user, other)
        async with storage.unit_of_work() as uow:
            for rule in (later, earlier, foreign):
                await uow.masking_rules.add(rule)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            listed = await uow.masking_rules.list_for_project(project.id)

        assert listed == [earlier, later]

    async def test_delete_reports_whether_it_removed_a_rule(self, storage):
        user, project = await _project(storage)
        rule = _rule(user, project)
        async with storage.unit_of_work() as uow:
            await uow.masking_rules.add(rule)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            first = await uow.masking_rules.delete(rule.id)
            second = await uow.masking_rules.delete(rule.id)
            await uow.commit()

        assert (first, second) == (True, False)
        async with storage.unit_of_work() as uow:
            assert await uow.masking_rules.get(rule.id) is None

    async def test_a_missing_rule_is_none(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.masking_rules.get(uuid.uuid4()) is None


def _mask(project: Project, paper_id: uuid.UUID, masked_with: tuple[str, ...]) -> PaperMask:
    return PaperMask(
        paper_id=paper_id,
        project_id=project.id,
        masked_sealed=b"sealed-masked",
        spans_sealed=b"sealed-spans",
        masked_with=masked_with,
        masked_at=NOW,
    )


class TestPaperMasks:
    async def test_a_first_masking_is_stored_once(self, storage):
        _, project = await _project(storage)
        paper_id = uuid.uuid4()
        first = _mask(project, paper_id, ("patterns@1",))
        async with storage.unit_of_work() as uow:
            won = await uow.paper_masks.put(first, replaces=None)
            lost = await uow.paper_masks.put(first, replaces=None)
            await uow.commit()

        assert (won, lost) == (True, False)
        async with storage.unit_of_work() as uow:
            assert await uow.paper_masks.get(paper_id) == first

    async def test_a_masking_is_replaced_only_while_it_is_the_one_expected(self, storage):
        """Two re-maskers cannot both win; a stale one never overwrites newer work."""
        _, project = await _project(storage)
        paper_id = uuid.uuid4()
        async with storage.unit_of_work() as uow:
            await uow.paper_masks.put(_mask(project, paper_id, ("patterns@1",)), replaces=None)
            await uow.commit()
        newer = _mask(project, paper_id, ("patterns@2",))

        async with storage.unit_of_work() as uow:
            won = await uow.paper_masks.put(newer, replaces=("patterns@1",))
            stale = await uow.paper_masks.put(
                _mask(project, paper_id, ("patterns@3",)), replaces=("patterns@1",)
            )
            await uow.commit()

        assert (won, stale) == (True, False)
        async with storage.unit_of_work() as uow:
            assert await uow.paper_masks.get(paper_id) == newer
