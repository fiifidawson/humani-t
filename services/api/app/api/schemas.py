"""Request and response models for the HTTP boundary.

Pydantic lives here and nowhere deeper. Untrusted JSON arrives, is validated into
these models, and is then converted into domain dataclasses which are valid by
construction. The reverse trip turns domain objects back into JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin

EXAMPLE_VOCABULARY = {
    "origin": "llm_assisted",
    "concepts": [
        {
            "term": "chatbot",
            "synonyms": ["conversational agent", "dialogue system"],
            "theme": "technology",
        },
        {
            "term": "humanitarian",
            "synonyms": ["crisis response"],
            "theme": "setting",
        },
    ],
}


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class ConceptIn(BaseModel):
    """One research concept and the words that stand in for it."""

    term: str = Field(min_length=1, description="Canonical spelling.")
    synonyms: list[str] = Field(
        default_factory=list, description="Alternatives matching the same idea."
    )
    theme: str = Field(
        default="general",
        min_length=1,
        description="Concepts sharing a theme are OR'd; themes are AND'd.",
    )


class VocabularyIn(BaseModel):
    """A vocabulary as submitted over HTTP."""

    model_config = ConfigDict(json_schema_extra={"example": EXAMPLE_VOCABULARY})

    concepts: list[ConceptIn] = Field(min_length=1)
    origin: VocabularyOrigin = Field(
        default=VocabularyOrigin.LLM_ASSISTED,
        description=(
            "user_supplied marks the vocabulary STRICT: it is used exactly as "
            "given, with no model-driven expansion."
        ),
    )
    version: int = Field(default=1, ge=1)

    def to_domain(self) -> Vocabulary:
        """Convert to the domain type, which validates on construction."""
        return Vocabulary(
            concepts=tuple(
                Concept(term=c.term, synonyms=tuple(c.synonyms), theme=c.theme)
                for c in self.concepts
            ),
            origin=self.origin,
            version=self.version,
        )


class SearchIn(BaseModel):
    """A vocabulary plus how many results to return."""

    model_config = ConfigDict(
        json_schema_extra={"example": {**EXAMPLE_VOCABULARY, "limit": 25}}
    )

    concepts: list[ConceptIn] = Field(min_length=1)
    origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED
    version: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=200)

    def to_domain(self) -> Vocabulary:
        return VocabularyIn(
            concepts=self.concepts, origin=self.origin, version=self.version
        ).to_domain()


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


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


class PaperOut(BaseModel):
    source_id: str
    external_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    is_open_access: bool = False
    abstract: str | None = None
    url: str | None = None
    identity: str = Field(description="Dedup key: DOI, else normalised title + year.")

    @classmethod
    def from_domain(cls, record: PaperRecord) -> PaperOut:
        return cls(
            source_id=record.source_id,
            external_id=record.external_id,
            title=record.title,
            year=record.year,
            doi=record.doi,
            is_open_access=record.is_open_access,
            abstract=record.abstract,
            url=record.url,
            identity=record.identity,
        )


class SearchOut(BaseModel):
    """Results, plus the query and provenance that produced them."""

    source_id: str
    query: CompiledQueryOut
    total: int
    results: list[PaperOut]


class SourceOut(BaseModel):
    source_id: str
    live: bool = Field(description="False for in-memory sources that make no requests.")
    description: str


class HealthOut(BaseModel):
    status: str = "ok"


class InfoOut(BaseModel):
    """What this process is and which implementations are wired into it."""

    name: str
    version: str
    api_prefix: str
    search_provider: str
    makes_network_calls: bool
