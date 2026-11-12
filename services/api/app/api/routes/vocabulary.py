"""Turning a vocabulary into a Boolean query."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas import CompiledQueryOut, VocabularyIn
from app.api.session import get_current
from app.domain.query import compile_vocabulary

# Signed in, but no project: compiling is pure and reads no project data.
router = APIRouter(
    prefix="/vocabulary", tags=["vocabulary"], dependencies=[Depends(get_current)]
)


@router.post(
    "/compile",
    summary="Compile a vocabulary into a Boolean query",
    response_model=CompiledQueryOut,
)
async def compile_endpoint(payload: VocabularyIn) -> CompiledQueryOut:
    """Show exactly what will be searched for, before anything is searched.

    Concepts in a theme are OR'd and themes are AND'd. No model is consulted, so the
    same vocabulary always gives the same query.
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
