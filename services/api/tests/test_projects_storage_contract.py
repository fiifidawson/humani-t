"""One behaviour contract for project and membership storage, run against every backend.

The Postgres run is skipped unless TEST_DATABASE_URL points at a migrated
database as the runtime role, exactly as in `test_storage_contract.py`.

Two guarantees matter most here (architecture §5.7):

- no blind overwrites: a stale copy of a project cannot replace a newer one;
- membership changes on one project are serialised, so the last-owner rule
  holds even when owners act at the same moment. The in-memory backend runs
  coroutines one after another, so only the Postgres run truly races them.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.application.projects import ProjectService
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider
from app.domain.accounts import NotFoundError, User
from app.domain.errors import ConcurrentUpdateError
from app.domain.projects import AlreadyMemberError, LastOwnerError, Membership, Project, Role
from app.domain.sessions import Session
from app.storage.base import StorageProvider
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage
from tests.postgres_support import delete_all_rows

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_ENCRYPTION = ScopeEncryption(
    LocalKeyProvider({1: bytes(32)}), AesGcmCipher(), clock=lambda: NOW
)


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


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:12]}@example.org",
        display_name="Test",
        password_hash="fake$x",
        created_at=NOW,
    )


def _project(created_by: UUID) -> Project:
    return Project(
        id=uuid.uuid4(), name_sealed=b"sealed", created_at=NOW, created_by=created_by
    )


def _membership(project_id: UUID, user_id: UUID, role: Role = Role.VIEWER) -> Membership:
    return Membership(
        project_id=project_id, user_id=user_id, role=role, added_at=NOW, added_by=None
    )


async def _users(storage: StorageProvider, count: int) -> list[User]:
    users = [_user() for _ in range(count)]
    async with storage.unit_of_work() as uow:
        for user in users:
            await uow.users.add(user)
        await uow.commit()
    return users


async def _stored_project(storage: StorageProvider, *members: tuple[User, Role]) -> Project:
    """A committed project whose first member created it."""
    project = _project(members[0][0].id)
    async with storage.unit_of_work() as uow:
        await uow.projects.add(project)
        for user, role in members:
            await uow.memberships.add(_membership(project.id, user.id, role))
        await uow.commit()
    return project


class TestProjects:
    async def test_a_committed_project_is_visible_to_the_next_unit_of_work(self, storage):
        (creator,) = await _users(storage, 1)
        project = _project(creator.id)
        async with storage.unit_of_work() as uow:
            await uow.projects.add(project)
            await uow.commit()

        async with storage.unit_of_work() as uow:
            assert await uow.projects.get(project.id) == project

    async def test_uncommitted_work_is_discarded(self, storage):
        (creator,) = await _users(storage, 1)
        project = _project(creator.id)
        async with storage.unit_of_work() as uow:
            await uow.projects.add(project)

        async with storage.unit_of_work() as uow:
            assert await uow.projects.get(project.id) is None

    async def test_a_missing_project_is_none(self, storage):
        async with storage.unit_of_work() as uow:
            assert await uow.projects.get(uuid.uuid4()) is None
            assert await uow.projects.lock(uuid.uuid4()) is None

    async def test_locking_returns_the_same_project_as_reading(self, storage):
        (creator,) = await _users(storage, 1)
        project = await _stored_project(storage, (creator, Role.OWNER))

        async with storage.unit_of_work() as uow:
            assert await uow.projects.lock(project.id) == project

    async def test_saving_moves_the_version_on(self, storage):
        (creator,) = await _users(storage, 1)
        project = await _stored_project(storage, (creator, Role.OWNER))

        async with storage.unit_of_work() as uow:
            await uow.projects.save(project.archived(NOW))
            await uow.commit()
        async with storage.unit_of_work() as uow:
            stored = await uow.projects.get(project.id)

        assert stored is not None
        assert stored.archived_at == NOW
        assert stored.version == project.version + 1

    async def test_a_stale_copy_cannot_overwrite_a_newer_one(self, storage):
        """One owner renames while another, holding an older copy, archives."""
        (creator,) = await _users(storage, 1)
        stale = await _stored_project(storage, (creator, Role.OWNER))
        async with storage.unit_of_work() as uow:
            await uow.projects.save(stale.renamed(b"renamed"))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            with pytest.raises(ConcurrentUpdateError):
                await uow.projects.save(stale.archived(NOW))

        async with storage.unit_of_work() as uow:
            current = await uow.projects.get(stale.id)
        assert current is not None
        assert current.name_sealed == b"renamed"
        assert not current.is_archived

    async def test_saving_an_unknown_project_fails(self, storage):
        (creator,) = await _users(storage, 1)
        async with storage.unit_of_work() as uow:
            with pytest.raises(ValueError, match="does not exist"):
                await uow.projects.save(_project(creator.id))

    async def test_a_member_is_listed_only_their_own_live_projects(self, storage):
        amara, kofi = await _users(storage, 2)
        mine = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))
        await _stored_project(storage, (kofi, Role.OWNER))
        gone = await _stored_project(storage, (amara, Role.OWNER))
        async with storage.unit_of_work() as uow:
            await uow.projects.save(gone.deleted(NOW))
            await uow.commit()

        async with storage.unit_of_work() as uow:
            listed = await uow.projects.list_for_member(amara.id)

        assert [p.id for p in listed] == [mine.id]


class TestMemberships:
    async def test_a_membership_is_found_by_project_and_user(self, storage):
        amara, kofi = await _users(storage, 2)
        project = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.REVIEWER))

        async with storage.unit_of_work() as uow:
            found = await uow.memberships.get(project.id, kofi.id)

        assert found == _membership(project.id, kofi.id, Role.REVIEWER)

    async def test_someone_who_is_not_a_member_is_none(self, storage):
        amara, kofi = await _users(storage, 2)
        project = await _stored_project(storage, (amara, Role.OWNER))

        async with storage.unit_of_work() as uow:
            assert await uow.memberships.get(project.id, kofi.id) is None

    async def test_adding_the_same_user_twice_is_refused(self, storage):
        amara, kofi = await _users(storage, 2)
        project = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))

        async with storage.unit_of_work() as uow:
            with pytest.raises(AlreadyMemberError):
                await uow.memberships.add(_membership(project.id, kofi.id, Role.OWNER))

    async def test_listing_a_project_returns_its_members_and_no_one_else(self, storage):
        amara, kofi, maya = await _users(storage, 3)
        project = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))
        await _stored_project(storage, (maya, Role.OWNER))

        async with storage.unit_of_work() as uow:
            members = await uow.memberships.list_for_project(project.id)

        assert {(m.user_id, m.role) for m in members} == {
            (amara.id, Role.OWNER),
            (kofi.id, Role.VIEWER),
        }

    async def test_changing_a_role_reports_whether_the_member_was_still_there(self, storage):
        amara, kofi, maya = await _users(storage, 3)
        project = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))

        async with storage.unit_of_work() as uow:
            changed = await uow.memberships.change_role(project.id, kofi.id, Role.REVIEWER)
            absent = await uow.memberships.change_role(project.id, maya.id, Role.REVIEWER)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            kofis = await uow.memberships.get(project.id, kofi.id)

        assert (changed, absent) == (True, False)
        assert kofis is not None
        assert kofis.role is Role.REVIEWER

    async def test_removing_reports_whether_there_was_anyone_to_remove(self, storage):
        amara, kofi = await _users(storage, 2)
        project = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))

        async with storage.unit_of_work() as uow:
            first = await uow.memberships.remove(project.id, kofi.id)
            second = await uow.memberships.remove(project.id, kofi.id)
            await uow.commit()

        assert (first, second) == (True, False)

    async def test_removing_a_projects_members_leaves_other_projects_alone(self, storage):
        amara, kofi = await _users(storage, 2)
        doomed = await _stored_project(storage, (amara, Role.OWNER), (kofi, Role.VIEWER))
        kept = await _stored_project(storage, (amara, Role.OWNER))

        async with storage.unit_of_work() as uow:
            removed = await uow.memberships.remove_all_for_project(doomed.id)
            await uow.commit()
        async with storage.unit_of_work() as uow:
            doomed_members = await uow.memberships.list_for_project(doomed.id)
            kept_members = await uow.memberships.list_for_project(kept.id)

        assert removed == 2
        assert doomed_members == []
        assert [m.user_id for m in kept_members] == [amara.id]


def _current(user: User) -> Current:
    session = Session(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=uuid.uuid4().bytes * 2,
        csrf_token_hash=uuid.uuid4().bytes * 2,
        created_at=NOW,
        last_seen_at=NOW,
        authenticated_at=NOW,
    )
    return Current(user, session)


def _service(storage: StorageProvider) -> ProjectService:
    return ProjectService(
        storage=storage, encryption=_ENCRYPTION, audit=AuditLog(_ENCRYPTION), clock=lambda: NOW
    )


async def _project_owned_by(
    storage: StorageProvider, service: ProjectService, count: int
) -> tuple[UUID, list[Current]]:
    """A project created through the service, with `count` owners in all."""
    owners = [_current(user) for user in await _users(storage, count)]
    created = await service.create(owners[0], "Cholera response, Sana'a", request_id=None)
    async with storage.unit_of_work() as uow:
        for owner in owners[1:]:
            await uow.memberships.add(_membership(created.id, owner.user.id, Role.OWNER))
        await uow.commit()
    return created.id, owners


async def _owners_left(storage: StorageProvider, project_id: UUID) -> int:
    async with storage.unit_of_work() as uow:
        members = await uow.memberships.list_for_project(project_id)
    return sum(m.role is Role.OWNER for m in members)


def _refusals_are_expected(outcomes: Sequence[object]) -> bool:
    # NotFoundError: the caller was removed by an owner who acted first, so by
    # the time its own turn came it was no longer a member.
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    return all(
        isinstance(e, LastOwnerError | ConcurrentUpdateError | NotFoundError) for e in errors
    )


class TestLastOwnerUnderConcurrency:
    """Five owners acting at once must never leave a project without an owner."""

    async def test_owners_stepping_down_together_always_leave_one_owner(self, storage):
        service = _service(storage)
        project_id, owners = await _project_owned_by(storage, service, 5)

        outcomes = await asyncio.gather(
            *(
                service.change_role(o, project_id, o.user.id, Role.VIEWER, request_id=None)
                for o in owners
            ),
            return_exceptions=True,
        )

        left = await _owners_left(storage, project_id)
        succeeded = sum(not isinstance(o, BaseException) for o in outcomes)
        assert left >= 1
        assert succeeded == len(owners) - left
        assert _refusals_are_expected(outcomes)

    async def test_owners_leaving_together_always_leave_one_owner(self, storage):
        service = _service(storage)
        project_id, owners = await _project_owned_by(storage, service, 5)

        outcomes = await asyncio.gather(
            *(service.remove_member(o, project_id, o.user.id, request_id=None) for o in owners),
            return_exceptions=True,
        )

        left = await _owners_left(storage, project_id)
        succeeded = sum(not isinstance(o, BaseException) for o in outcomes)
        assert left >= 1
        assert succeeded == len(owners) - left
        assert _refusals_are_expected(outcomes)

    async def test_two_owners_removing_each_other_together_leave_one_owner(self, storage):
        service = _service(storage)
        project_id, (first, second) = await _project_owned_by(storage, service, 2)

        outcomes = await asyncio.gather(
            service.remove_member(first, project_id, second.user.id, request_id=None),
            service.remove_member(second, project_id, first.user.id, request_id=None),
            return_exceptions=True,
        )

        assert await _owners_left(storage, project_id) == 1
        assert sum(not isinstance(o, BaseException) for o in outcomes) == 1
        assert _refusals_are_expected(outcomes)


@pytest.mark.postgres
@pytest.mark.skipif(not _DATABASE_URL, reason="TEST_DATABASE_URL not set")
class TestPostgresProjectLock:
    async def test_a_second_locker_waits_until_the_first_commits(self):
        """`lock` is SELECT … FOR UPDATE: the serialisation point for membership changes."""
        assert _DATABASE_URL is not None
        postgres = PostgresStorage.from_url(_DATABASE_URL)
        order: list[str] = []
        first_has_lock = asyncio.Event()
        try:
            (creator,) = await _users(postgres, 1)
            project = await _stored_project(postgres, (creator, Role.OWNER))

            async def first() -> None:
                async with postgres.unit_of_work() as uow:
                    await uow.projects.lock(project.id)
                    order.append("first locked")
                    first_has_lock.set()
                    await asyncio.sleep(0.2)
                    order.append("first committing")
                    await uow.commit()

            async def second() -> None:
                await first_has_lock.wait()
                async with postgres.unit_of_work() as uow:
                    await uow.projects.lock(project.id)
                    order.append("second locked")
                    await uow.commit()

            await asyncio.wait_for(asyncio.gather(first(), second()), timeout=5)
        finally:
            await postgres.close()
            await delete_all_rows(_DATABASE_URL)

        assert order == ["first locked", "first committing", "second locked"]
