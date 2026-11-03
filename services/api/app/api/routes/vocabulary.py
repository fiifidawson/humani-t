"""Turning a vocabulary into a Boolean query."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import CompiledQueryOut, VocabularyIn
from app.domain.query import compile_vocabulary

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


@router.post(
    "/compile",
    summary="Compile a vocabulary into a Boolean query",
    response_model=CompiledQueryOut,
)
async def compile_endpoint(payload: VocabularyIn) -> CompiledQueryOut:
    """Show exactly what will be searched for, before anything is searched.

    Concepts sharing a theme are OR'd; themes are AND'd:

        ("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")

    Pure and deterministic — the same vocabulary always produces the same query,
    and no model is consulted for either origin. That is what makes a
    `user_supplied` vocabulary strict by construction rather than by discipline.

    This endpoint exists so a reviewer can inspect and argue about the query
    itself, which is the transparency commitment made concrete.
    """
    try:
        vocabulary = payload.to_domain()
    except ValueError as exc:
        # Domain invariants are the real validation. Pydantic checks shape;
        # the domain checks meaning.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return CompiledQueryOut.from_domain(compile_vocabulary(vocabulary))
