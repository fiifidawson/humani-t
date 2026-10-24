"""Instance administration: invitations and disabling accounts."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.dependencies import AccountsDep, StorageDep
from app.api.schemas.accounts import InvitationIn, InvitationOut
from app.api.session import CurrentDep, request_id
from app.api.transaction import transaction

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/invitations", status_code=status.HTTP_201_CREATED, summary="Invite someone by email"
)
async def invite(
    payload: InvitationIn,
    request: Request,
    current: CurrentDep,
    storage: StorageDep,
    accounts: AccountsDep,
) -> InvitationOut:
    """The link goes only to the invitee's inbox; it is never in this response."""
    async with transaction(storage) as uow:
        invitation = await accounts.invite(uow, current, payload.email, request_id(request))
    return InvitationOut.from_domain(invitation)


@router.post(
    "/users/{user_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable an account and end its sessions",
)
async def disable_user(
    user_id: UUID, current: CurrentDep, storage: StorageDep, accounts: AccountsDep
) -> None:
    async with transaction(storage) as uow:
        await accounts.disable_user(uow, current, user_id)
