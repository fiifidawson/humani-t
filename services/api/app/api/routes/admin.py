"""Instance administration: invitations and disabling accounts."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.dependencies import AccountsDep
from app.api.schemas.accounts import InvitationIn, InvitationOut
from app.api.session import CurrentDep, request_id

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/invitations", status_code=status.HTTP_201_CREATED, summary="Invite someone by email"
)
async def invite(
    payload: InvitationIn,
    request: Request,
    current: CurrentDep,
    accounts: AccountsDep,
) -> InvitationOut:
    """The link goes only to the invitee's inbox; it is never in this response."""
    invitation = await accounts.invite(current, payload.email, request_id(request))
    return InvitationOut.from_domain(invitation)


@router.post(
    "/users/{user_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable an account and end its sessions",
)
async def disable_user(user_id: UUID, current: CurrentDep, accounts: AccountsDep) -> None:
    await accounts.disable_user(current, user_id)
