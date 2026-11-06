"""A project's masking rules: terms always masked in every text of the project.

Owners only, for reading too: a rule's term is usually a person's name, which
is raw text (architecture §3.9.1). Every route passes through `require(action)`
first (`api/access.py`), so a non-member is refused with 404.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.access import require
from app.api.dependencies import MaskingRulesDep
from app.api.schemas.masking import MaskingRuleIn, MaskingRuleOut
from app.api.session import request_id
from app.application.accounts import Current
from app.domain.masking import DuplicateMaskingRuleError
from app.domain.projects import Action

router = APIRouter(prefix="/projects/{project_id}/masking-rules", tags=["masking"])

_RawTextReader = Annotated[Current, require(Action.VIEW_RAW_TEXT)]
_Owner = Annotated[Current, require(Action.MANAGE_DOCUMENTS)]


@router.get("", summary="The project's masking rules, oldest first")
async def list_masking_rules(
    project_id: UUID, request: Request, current: _RawTextReader, rules: MaskingRulesDep
) -> list[MaskingRuleOut]:
    """Shows raw terms, so each listing writes an audit event."""
    listed = await rules.list(current, project_id, request_id(request))
    return [MaskingRuleOut.from_domain(record, term) for record, term in listed]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Always mask a term in this project",
    responses={409: {"description": "The project already masks this term"}},
)
async def add_masking_rule(
    project_id: UUID,
    payload: MaskingRuleIn,
    request: Request,
    response: Response,
    current: _Owner,
    rules: MaskingRulesDep,
) -> MaskingRuleOut:
    """The term is sealed under the project's key; the audit event holds the rule's id only."""
    try:
        record, term = await rules.add(
            current, project_id, payload.term, payload.entity_type, request_id(request)
        )
    except DuplicateMaskingRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response.headers["Location"] = f"{request.url.path}/{record.id}"
    return MaskingRuleOut.from_domain(record, term)


@router.delete(
    "/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Stop masking a term"
)
async def remove_masking_rule(
    project_id: UUID, rule_id: UUID, request: Request, current: _Owner, rules: MaskingRulesDep
) -> None:
    """Texts already masked keep their placeholders until the next mask stage."""
    await rules.remove(current, project_id, rule_id, request_id(request))
