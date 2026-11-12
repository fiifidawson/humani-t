"""Request and response models for the HTTP boundary.

Pydantic lives here and nowhere deeper. Untrusted JSON arrives, is validated into
these models, and is then converted into domain dataclasses which are valid by
construction. The reverse trip turns domain objects back into JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.query import BooleanQuery
from app.domain.vocabulary import VocabularyOrigin


class TermGroupOut(BaseModel):
    concept: str
    terms: list[str]


class ThemeGroupOut(BaseModel):
    theme: str
    groups: list[TermGroupOut]


class ProvenanceOut(BaseModel):
    """Where the terms came from. Travels with every result set."""

    vocabulary_version: int
    vocabulary_origin: VocabularyOrigin
    is_strict: bool


class CompiledQueryOut(BaseModel):
    """A compiled Boolean query, in both structured and readable form."""

    rendered: str = Field(description="Human-readable. NOT what is sent to a source.")
    themes: list[ThemeGroupOut]
    term_count: int
    provenance: ProvenanceOut

    @classmethod
    def from_domain(cls, query: BooleanQuery) -> CompiledQueryOut:
        return cls(
            rendered=query.render(),
            themes=[
                ThemeGroupOut(
                    theme=theme.theme,
                    groups=[
                        TermGroupOut(concept=g.concept_term, terms=list(g.terms))
                        for g in theme.groups
                    ],
                )
                for theme in query.themes
            ],
            term_count=len(query.all_terms),
            provenance=ProvenanceOut(
                vocabulary_version=query.vocabulary_version,
                vocabulary_origin=query.vocabulary_origin,
                is_strict=query.is_strict,
            ),
        )


class HealthOut(BaseModel):
    status: str = "ok"


class ReadyOut(BaseModel):
    status: str = "ready"


class InfoOut(BaseModel):
    """What this process is and which implementations are wired into it."""

    name: str
    version: str
    api_prefix: str
    paper_sources: list[str]
    makes_network_calls: bool
