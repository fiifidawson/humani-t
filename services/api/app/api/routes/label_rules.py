"""A review's label rules: read them, preview what they would move, apply them.

Every member may read the rules; owners and reviewers may preview and apply. A non-member
gets the same 404 as a missing review. Nothing here calls a model.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.api.access import require_review
from app.api.dependencies import LabelRulesDep
from app.api.schemas.label_rules import (
    LabelRulesIn,
    LabelRulesOut,
    RulesAppliedOut,
    RulesPreviewOut,
)
from app.api.session import request_id
from app.application.accounts import Current
from app.domain.label_rules import LabelRules
from app.domain.projects import Action

router = APIRouter(prefix="/reviews/{review_id}/label-rules", tags=["label rules"])

_Reader = Annotated[Current, require_review(Action.READ_PROJECT)]
_Editor = Annotated[Current, require_review(Action.EDIT_LABELS)]


def _rules(payload: LabelRulesIn) -> LabelRules:
    try:
        return payload.to_domain()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("", summary="The review's label rules and every earlier version")
async def read_rules(review_id: UUID, current: _Reader, rules: LabelRulesDep) -> LabelRulesOut:
    return LabelRulesOut.from_view(await rules.history(current, review_id))


@router.post("/preview", summary="How many papers these rules would move; changes nothing")
async def preview_rules(
    review_id: UUID, payload: LabelRulesIn, current: _Editor, rules: LabelRulesDep
) -> RulesPreviewOut:
    return RulesPreviewOut.from_view(await rules.preview(current, review_id, _rules(payload)))


@router.put(
    "",
    summary="Apply these rules as the review's next version",
    responses={409: {"description": "Someone else applied rules at the same moment"}},
)
async def apply_rules(
    review_id: UUID,
    payload: LabelRulesIn,
    request: Request,
    current: _Editor,
    rules: LabelRulesDep,
) -> RulesAppliedOut:
    """Papers are relabelled from the answers already stored; no model is called."""
    applied = await rules.apply(current, review_id, _rules(payload), request_id(request))
    return RulesAppliedOut.from_applied(applied)
