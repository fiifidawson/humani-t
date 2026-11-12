"""The check every use case inside a project makes, inside its own transaction.

A non-member gets the same 404 as for a missing object, so "not yours" and "not there"
look alike; a member whose role may not act gets 403.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.accounts import ForbiddenError, NotFoundError
from app.domain.projects import Action, Membership, can
from app.domain.reviews import Review
from app.storage.base import UnitOfWork

REVIEW_NOT_FOUND = "No such review."


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


async def require_review_member(
    uow: UnitOfWork, user_id: UUID, review_id: UUID, action: Action
) -> Review:
    """The review, if the caller's membership of its project allows `action`."""
    review = await uow.reviews.get(review_id)
    if review is None:
        raise NotFoundError(REVIEW_NOT_FOUND)
    await require_member(uow, user_id, review.project_id, action, not_found=REVIEW_NOT_FOUND)
    return review
