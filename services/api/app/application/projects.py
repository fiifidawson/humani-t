"""Projects and their members: create, read, rename, archive, share and delete.

Each use case checks membership inside its own transaction, even after the HTTP layer
has: only the check made alongside the write decides. Membership changes take the
project's row lock first, so one owner always remains. The name is sealed; the audit log
gets ids only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.accounts import Current
from app.application.audit import AuditLog
from app.application.encryption import ScopeEncryption
from app.domain.accounts import (
    ForbiddenError,
    NotFoundError,
    ReauthenticationRequiredError,
    normalize_email,
)
from app.domain.encryption import EncryptionContext
from app.domain.projects import (
    Action,
    Membership,
    NoSuchAccountError,
    Project,
    Role,
    can,
    normalize_project_name,
    require_an_owner_remains,
)
from app.domain.sessions import SessionPolicy
from app.storage.base import StorageProvider, UnitOfWork

# Table "projects", column "name"; the row is the project itself.
_NAME_PURPOSE = "projects.name"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProjectView:
    """A project as one member sees it: its name in the clear and their own role."""

    id: UUID
    name: str
    role: Role
    created_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberView:
    """A member as the caller may see them: `email` is `None` unless they manage members."""

    user_id: UUID
    display_name: str
    role: Role
    added_at: datetime
    email: str | None = None


class ProjectService:
    def __init__(
        self,
        *,
        storage: StorageProvider,
        encryption: ScopeEncryption,
        audit: AuditLog,
        session_policy: SessionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._storage = storage
        self._encryption = encryption
        self._audit = audit
        self._session_policy = session_policy or SessionPolicy()
        self._clock = clock

    async def authorize(
        self,
        current: Current,
        project_id: UUID,
        action: Action,
        *,
        needs_recent_sign_in: bool = False,
    ) -> Role:
        """Check access without acting: 404, then 403, then the recent sign-in.

        For the HTTP layer, so a refusal comes before the request body is read.
        Every use case below checks again inside its own transaction.
        """
        async with self._storage.unit_of_work() as uow:
            _, membership = await self._access(
                uow, current, project_id, action, needs_recent_sign_in=needs_recent_sign_in
            )
        return membership.role

    async def create(
        self, current: Current, name: str, request_id: str | None = None
    ) -> ProjectView:
        """Create a project, its data key and the caller's owner membership, together."""
        name = normalize_project_name(name)
        project_id = uuid.uuid4()
        now = self._clock()
        async with self._storage.unit_of_work() as uow:
            await self._encryption.create_scope(uow, project_id)
            project = Project(
                id=project_id,
                name_sealed=await self._seal_name(uow, project_id, name),
                created_at=now,
                created_by=current.user.id,
            )
            await uow.projects.add(project)
            owner = Membership(project_id, current.user.id, Role.OWNER, now, added_by=None)
            await uow.memberships.add(owner)
            await self._record(uow, current, request_id, "project.created", project_id)
            await uow.commit()
        return ProjectView(project_id, name, Role.OWNER, now, None)

    async def list_mine(
        self, current: Current, *, include_archived: bool = False
    ) -> list[ProjectView]:
        """The caller's projects, newest first. Archived ones only when asked for."""
        views: list[ProjectView] = []
        async with self._storage.unit_of_work() as uow:
            for project in await uow.projects.list_for_member(current.user.id):
                membership = await uow.memberships.get(project.id, current.user.id)
                if membership is None or (project.is_archived and not include_archived):
                    continue
                views.append(await self._view(uow, project, membership.role))
        return views

    async def get(self, current: Current, project_id: UUID) -> ProjectView:
        async with self._storage.unit_of_work() as uow:
            project, membership = await self._access(
                uow, current, project_id, Action.READ_PROJECT
            )
            return await self._view(uow, project, membership.role)

    async def rename(
        self, current: Current, project_id: UUID, name: str, request_id: str | None = None
    ) -> ProjectView:
        async with self._storage.unit_of_work() as uow:
            project, membership = await self._access(
                uow, current, project_id, Action.UPDATE_PROJECT
            )
            name = normalize_project_name(name)
            renamed = project.renamed(await self._seal_name(uow, project_id, name))
            await uow.projects.save(renamed)
            await self._record(uow, current, request_id, "project.renamed", project_id)
            await uow.commit()
        return ProjectView(
            project_id, name, membership.role, project.created_at, project.archived_at
        )

    async def archive(
        self, current: Current, project_id: UUID, request_id: str | None = None
    ) -> ProjectView:
        """Hide the project from default lists. Archiving again changes nothing."""
        return await self._set_archived(current, project_id, request_id, archive=True)

    async def unarchive(
        self, current: Current, project_id: UUID, request_id: str | None = None
    ) -> ProjectView:
        return await self._set_archived(current, project_id, request_id, archive=False)

    async def delete(
        self, current: Current, project_id: UUID, request_id: str | None = None
    ) -> None:
        """Crypto-shred the project. Irreversible once committed.

        The row stays as a tombstone; memberships go, because who worked on what is
        sensitive too.
        """
        async with self._storage.unit_of_work() as uow:
            project, _ = await self._access(
                uow,
                current,
                project_id,
                Action.DELETE_PROJECT,
                needs_recent_sign_in=True,
                lock=True,
            )
            await self._encryption.destroy_scope(uow, project_id)
            await uow.memberships.remove_all_for_project(project_id)
            await uow.projects.save(project.deleted(self._clock()))
            await self._record(uow, current, request_id, "project.deleted", project_id)
            await uow.commit()

    async def list_members(self, current: Current, project_id: UUID) -> list[MemberView]:
        async with self._storage.unit_of_work() as uow:
            _, own = await self._access(uow, current, project_id, Action.READ_PROJECT)
            # Owners need the email to tell two people with the same display name
            # apart before changing their access; nobody else needs it.
            shows_email = can(own.role, Action.MANAGE_MEMBERS)
            return [
                await self._member_view(uow, m, shows_email=shows_email)
                for m in await uow.memberships.list_for_project(project_id)
            ]

    async def add_member(
        self,
        current: Current,
        project_id: UUID,
        *,
        email: str,
        role: Role,
        request_id: str | None = None,
    ) -> MemberView:
        """Give an existing, enabled account a role. Unknown and disabled look the same."""
        async with self._storage.unit_of_work() as uow:
            await self._access(
                uow,
                current,
                project_id,
                Action.MANAGE_MEMBERS,
                needs_recent_sign_in=True,
                lock=True,
            )
            user = await uow.users.get_by_email(normalize_email(email))
            if user is None or user.is_disabled:
                raise NoSuchAccountError("No active account uses this email address.")
            membership = Membership(
                project_id, user.id, role, self._clock(), added_by=current.user.id
            )
            await uow.memberships.add(membership)
            await self._record_member(uow, current, request_id, "membership.added", membership)
            await uow.commit()
        return MemberView(user.id, user.display_name, role, membership.added_at, user.email)

    async def change_role(
        self,
        current: Current,
        project_id: UUID,
        user_id: UUID,
        role: Role,
        request_id: str | None = None,
    ) -> MemberView:
        async with self._storage.unit_of_work() as uow:
            await self._access(
                uow,
                current,
                project_id,
                Action.MANAGE_MEMBERS,
                needs_recent_sign_in=True,
                lock=True,
            )
            target = await self._member(uow, project_id, user_id)
            require_an_owner_remains(
                await uow.memberships.list_for_project(project_id), user_id, role
            )
            changed = target.with_role(role)
            if role is not target.role:
                await uow.memberships.change_role(project_id, user_id, role)
                await self._record_member(
                    uow, current, request_id, "membership.role_changed", changed
                )
                await uow.commit()
            return await self._member_view(uow, changed, shows_email=True)

    async def remove_member(
        self, current: Current, project_id: UUID, user_id: UUID, request_id: str | None = None
    ) -> None:
        """Remove someone, or leave. Leaving needs no recent sign-in: it only takes away."""
        is_leaving = user_id == current.user.id
        async with self._storage.unit_of_work() as uow:
            await self._access(
                uow,
                current,
                project_id,
                Action.LEAVE_PROJECT if is_leaving else Action.MANAGE_MEMBERS,
                needs_recent_sign_in=not is_leaving,
                lock=True,
            )
            target = await self._member(uow, project_id, user_id)
            require_an_owner_remains(
                await uow.memberships.list_for_project(project_id), user_id, None
            )
            await uow.memberships.remove(project_id, user_id)
            await self._record_member(uow, current, request_id, "membership.removed", target)
            await uow.commit()

    async def _set_archived(
        self, current: Current, project_id: UUID, request_id: str | None, *, archive: bool
    ) -> ProjectView:
        async with self._storage.unit_of_work() as uow:
            project, membership = await self._access(
                uow, current, project_id, Action.UPDATE_PROJECT
            )
            changed = project.archived(self._clock()) if archive else project.unarchived()
            if changed != project:
                await uow.projects.save(changed)
                action = "project.archived" if archive else "project.unarchived"
                await self._record(uow, current, request_id, action, project_id)
                await uow.commit()
            return await self._view(uow, changed, membership.role)

    async def _access(
        self,
        uow: UnitOfWork,
        current: Current,
        project_id: UUID,
        action: Action,
        *,
        needs_recent_sign_in: bool = False,
        lock: bool = False,
    ) -> tuple[Project, Membership]:
        # One message for "no such project" and "not yours": a project's
        # existence is not an outsider's business.
        project = await (uow.projects.lock if lock else uow.projects.get)(project_id)
        membership = await uow.memberships.get(project_id, current.user.id)
        if project is None or project.is_deleted or membership is None:
            raise NotFoundError("No such project.")
        if not can(membership.role, action):
            raise ForbiddenError("Your role in this project does not allow this.")
        if needs_recent_sign_in and not current.session.is_recently_authenticated(
            self._clock(), self._session_policy
        ):
            raise ReauthenticationRequiredError("Confirm your password to continue.")
        return project, membership

    @staticmethod
    async def _member(uow: UnitOfWork, project_id: UUID, user_id: UUID) -> Membership:
        membership = await uow.memberships.get(project_id, user_id)
        if membership is None:
            raise NotFoundError("No such member.")
        return membership

    async def _view(self, uow: UnitOfWork, project: Project, role: Role) -> ProjectView:
        name = await self._encryption.decrypt(
            uow, self._name_context(project.id), project.name_sealed
        )
        return ProjectView(
            project.id, name.decode(), role, project.created_at, project.archived_at
        )

    @staticmethod
    async def _member_view(
        uow: UnitOfWork, membership: Membership, *, shows_email: bool
    ) -> MemberView:
        user = await uow.users.get(membership.user_id)
        display_name = user.display_name if user is not None else "Unknown account"
        email = user.email if user is not None and shows_email else None
        return MemberView(
            membership.user_id, display_name, membership.role, membership.added_at, email
        )

    async def _seal_name(self, uow: UnitOfWork, project_id: UUID, name: str) -> bytes:
        return await self._encryption.encrypt(
            uow, self._name_context(project_id), name.encode()
        )

    @staticmethod
    def _name_context(project_id: UUID) -> EncryptionContext:
        return EncryptionContext(project_id, _NAME_PURPOSE, str(project_id))

    async def _record(
        self,
        uow: UnitOfWork,
        current: Current,
        request_id: str | None,
        action: str,
        project_id: UUID,
    ) -> None:
        await self._audit.record(
            uow,
            current.actor(request_id),
            action,
            "project",
            str(project_id),
            scope_id=project_id,
        )

    async def _record_member(
        self,
        uow: UnitOfWork,
        current: Current,
        request_id: str | None,
        action: str,
        membership: Membership,
    ) -> None:
        await self._audit.record(
            uow,
            current.actor(request_id),
            action,
            "user",
            str(membership.user_id),
            scope_id=membership.project_id,
        )
