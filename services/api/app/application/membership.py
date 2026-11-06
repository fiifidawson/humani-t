"""The check every use case inside a project makes, inside its own transaction.

Reviews, runs and gates belong to exactly one project. Whatever id a request
names, the use case finds the project it belongs to and asks this module
whether the caller may act there: 404 first, with the caller's own message for
a missing object, so "not yours" and "not there" are indistinguishable
(architecture §5.5); then 403 for a role that may not.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.accounts import ForbiddenError, NotFoundError
from app.domain.projects import Action, Membership, can
from app.storage.base import UnitOfWork


async def require_member(
    uow: UnitOfWork, user_id: UUID, project_id: UUID, action: Action, *, not_found: str
) -> Membership:
    """The caller's membership if it allows `action`.

    `not_found` is the message for an object that does not exist; it is used
    unchanged for an object in a project the caller is not a member of.
    """
    project = await uow.projects.get(project_id)
    membership = await uow.memberships.get(project_id, user_id)
    if project is None or project.is_deleted or membership is None:
        raise NotFoundError(not_found)
    if not can(membership.role, action):
        raise ForbiddenError("Your role in this project does not allow this.")
    return membership
