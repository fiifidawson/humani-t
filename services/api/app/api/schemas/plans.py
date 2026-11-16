"""Request and response models for the search-planning conversation.

Request models forbid unknown fields and bound every size. Pydantic checks
shape; the domain checks meaning (`parse_strict_vocabulary`, `ChangeRequest`,
`Criterion`), and its `ValueError` becomes a 422 in the route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import CompiledQueryOut
from app.application.planning import PlanVersionView, PlanView
from app.domain.plans import (
    MAX_ANSWER_LENGTH,
    MAX_CHANGE_REQUEST_LENGTH,
    MAX_CRITERION_LENGTH,
    ChangeKind,
    ChangeRequest,
    ClarifyingQuestion,
    CriterionKind,
    ModelCallPurpose,
    PlanAuthor,
    PlanDiff,
    PlanMode,
    PrivacyReceipt,
    ReviewPlan,
    SentTextKind,
    TermKey,
    VocabularyFormat,
    term_key,
)
from app.domain.query import compile_vocabulary
from app.domain.reviews import ReviewStatus
from app.domain.vocabulary import Concept, VocabularyOrigin

# 256 KiB of terms is far beyond any validated search vocabulary; the body limit is 1 MiB.
_MAX_UPLOAD_CHARACTERS = 256 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanStartIn(_Strict):
    mode: PlanMode = Field(
        description="shallow (Quick: draft at once) or deep (questions first)."
    )


class AnswerIn(_Strict):
    question_id: str = Field(min_length=1, max_length=16)
    answer: str | None = Field(
        default=None, max_length=MAX_ANSWER_LENGTH, description="null skips the question."
    )


class AnswersIn(_Strict):
    answers: list[AnswerIn] = Field(max_length=8)
    draft_now: bool = Field(
        default=False, description="Skip every question not yet answered and draft now."
    )


class ChangesIn(_Strict):
    request: str = Field(min_length=1, max_length=MAX_CHANGE_REQUEST_LENGTH)
    based_on_version: int = Field(ge=1, description="The version the person was looking at.")


class AcceptIn(_Strict):
    version: int = Field(ge=1, description="The draft being accepted; it must be the latest.")


class CriterionIn(_Strict):
    kind: CriterionKind
    text: str = Field(min_length=1, max_length=MAX_CRITERION_LENGTH)


class VocabularyUploadIn(_Strict):
    format: VocabularyFormat = Field(
        description=(
            "text: one concept per line, `term; synonym`, `[theme]` starts a theme, `#` "
            "comments. csv: header `theme,term,synonyms`, synonyms separated by `;`."
        )
    )
    content: str = Field(min_length=1, max_length=_MAX_UPLOAD_CHARACTERS)
    criteria: list[CriterionIn] | None = Field(
        default=None,
        max_length=40,
        description="Screening criteria; omitted, the previous version's carry over.",
    )


class AuthorOut(BaseModel):
    kind: str = Field(description="model or person.")
    user_id: UUID | None

    @classmethod
    def from_domain(cls, author: PlanAuthor) -> AuthorOut:
        return cls(kind=author.kind.value, user_id=author.user_id)


class QuestionOut(BaseModel):
    id: str
    text: str
    suggestions: list[str]
    answer: str | None
    skipped: bool

    @classmethod
    def from_domain(cls, q: ClarifyingQuestion) -> QuestionOut:
        return cls(
            id=q.id,
            text=q.text,
            suggestions=list(q.suggestions),
            answer=q.answer,
            skipped=q.is_skipped,
        )


class ChangeRequestOut(BaseModel):
    id: UUID
    text: str
    requested_by: UUID
    requested_at: datetime
    based_on_version: int

    @classmethod
    def from_domain(cls, change: ChangeRequest) -> ChangeRequestOut:
        return cls(
            id=change.id,
            text=change.text,
            requested_by=change.requested_by,
            requested_at=change.requested_at,
            based_on_version=change.based_on_version,
        )


class TermOut(BaseModel):
    text: str
    added_by: AuthorOut = Field(description="Who wrote the version that brought this term.")


class ConceptOut(BaseModel):
    term: str
    synonyms: list[str]
    terms: list[TermOut] = Field(
        description="The term, then its synonyms, each with its author."
    )


class ThemeOut(BaseModel):
    name: str
    concepts: list[ConceptOut]


class CriterionOut(BaseModel):
    id: str
    kind: CriterionKind
    text: str


class VersionOut(BaseModel):
    version: int
    origin: VocabularyOrigin
    is_strict: bool = Field(description="True for your own terms, used exactly as given.")
    author: AuthorOut
    created_at: datetime
    accepted_at: datetime | None
    accepted_by: UUID | None
    model_id: str | None
    prompt_version: str | None
    themes: list[ThemeOut]
    criteria: list[CriterionOut]
    notes: list[str] = Field(description="Changes made automatically to the model's draft.")
    change_request: ChangeRequestOut | None
    clarifications: list[QuestionOut]
    query: CompiledQueryOut

    @classmethod
    def from_domain(cls, plan: ReviewPlan, authors: dict[TermKey, PlanAuthor]) -> VersionOut:
        vocabulary = plan.vocabulary

        def term(concept: Concept, text: str) -> TermOut:
            author = authors.get(term_key(concept, text), plan.author)
            return TermOut(text=text, added_by=AuthorOut.from_domain(author))

        return cls(
            version=plan.version,
            origin=vocabulary.origin,
            is_strict=vocabulary.is_strict,
            author=AuthorOut.from_domain(plan.author),
            created_at=plan.created_at,
            accepted_at=plan.accepted_at,
            accepted_by=plan.accepted_by,
            model_id=plan.model_id,
            prompt_version=plan.prompt_version,
            themes=[
                ThemeOut(
                    name=theme,
                    concepts=[
                        ConceptOut(
                            term=c.term,
                            synonyms=list(c.synonyms),
                            terms=[term(c, t) for t in c.terms],
                        )
                        for c in vocabulary.concepts_in(theme)
                    ],
                )
                for theme in vocabulary.themes
            ],
            criteria=[CriterionOut(id=c.id, kind=c.kind, text=c.text) for c in plan.criteria],
            notes=list(plan.notes),
            change_request=(
                None
                if plan.change_request is None
                else ChangeRequestOut.from_domain(plan.change_request)
            ),
            clarifications=[QuestionOut.from_domain(q) for q in plan.clarifications],
            query=CompiledQueryOut.from_domain(compile_vocabulary(vocabulary)),
        )


class VersionSummaryOut(BaseModel):
    version: int
    origin: VocabularyOrigin
    author: AuthorOut
    created_at: datetime
    accepted_at: datetime | None


class SentTextOut(BaseModel):
    kind: SentTextKind
    ref: str | None = Field(description="The clarifying question an answer belongs to.")
    sent: str = Field(description="Exactly what the model received; see `is_masked`.")
    counts: dict[str, int] = Field(description="Masked items by type. Never the values.")


class ReceiptOut(BaseModel):
    id: UUID
    purpose: ModelCallPurpose
    created_at: datetime
    created_by: UUID | None = Field(description="null when a run's first stage made the call.")
    model_id: str
    prompt_version: str
    delivered: bool = Field(
        description="false when the call failed: the text may still have been read."
    )
    masked_total: int
    items: list[SentTextOut]
    plan_version: int | None = Field(
        default=None,
        description=(
            "The version this call was drafting, named even when it failed; null for "
            "clarifying questions."
        ),
    )
    change_request_id: UUID | None = Field(
        default=None, description="The change request a revision was drafted from."
    )
    is_masked: bool = Field(
        description=(
            "false when the project's masking was off: the text was sent as written, "
            "apart from the project's own masking rules."
        )
    )

    @classmethod
    def from_domain(cls, receipt: PrivacyReceipt) -> ReceiptOut:
        return cls(
            id=receipt.id,
            purpose=receipt.purpose,
            created_at=receipt.created_at,
            created_by=receipt.created_by,
            model_id=receipt.model_id,
            prompt_version=receipt.prompt_version,
            delivered=receipt.is_delivered,
            masked_total=receipt.masked_total,
            items=[
                SentTextOut(kind=i.kind, ref=i.ref, sent=i.sent, counts=dict(i.counts))
                for i in receipt.items
            ],
            plan_version=receipt.plan_version,
            change_request_id=receipt.change_request_id,
            is_masked=receipt.is_masked,
        )


class PlanOut(BaseModel):
    review_id: UUID
    status: ReviewStatus
    mode: PlanMode | None
    questions: list[QuestionOut]
    pending_change: ChangeRequestOut | None = Field(
        description="A change request the model is drafting from."
    )
    current: VersionOut | None = Field(description="The latest version, accepted or not.")
    accepted_version: int | None = Field(description="The version runs use; null if none.")
    rerun_from: Literal["queries", "screen"] | None = Field(
        description=(
            "Where a run of the current version would start: `queries` when the search "
            "changed since the version accepted before it, or none was; `screen` when only "
            "the criteria changed, re-judging the papers already found; null when nothing did."
        )
    )
    versions: list[VersionSummaryOut]
    receipts: list[ReceiptOut] = Field(description="What each model call received.")

    @classmethod
    def from_view(cls, view: PlanView) -> PlanOut:
        current = view.current
        return cls(
            review_id=view.review_id,
            status=view.status,
            mode=view.mode,
            questions=[QuestionOut.from_domain(q) for q in view.questions],
            pending_change=(
                None
                if view.pending_change is None
                else ChangeRequestOut.from_domain(view.pending_change)
            ),
            current=None
            if current is None
            else VersionOut.from_domain(current, dict(view.term_authors)),
            accepted_version=view.accepted_version,
            rerun_from=view.rerun_from,
            versions=[
                VersionSummaryOut(
                    version=p.version,
                    origin=p.vocabulary.origin,
                    author=AuthorOut.from_domain(p.author),
                    created_at=p.created_at,
                    accepted_at=p.accepted_at,
                )
                for p in view.versions
            ],
            receipts=[ReceiptOut.from_domain(r) for r in view.receipts],
        )


class ConceptChangeOut(BaseModel):
    theme: str
    term: str
    change: ChangeKind
    by: AuthorOut


class TermChangeOut(BaseModel):
    theme: str
    concept: str
    term: str
    change: ChangeKind
    by: AuthorOut


class CriterionChangeOut(BaseModel):
    kind: CriterionKind
    text: str
    change: ChangeKind
    by: AuthorOut


class DiffOut(BaseModel):
    from_version: int | None = Field(description="null for the first version.")
    to_version: int
    concepts: list[ConceptChangeOut]
    terms: list[TermChangeOut]
    criteria: list[CriterionChangeOut]

    @classmethod
    def from_domain(cls, diff: PlanDiff) -> DiffOut:
        return cls(
            from_version=diff.from_version,
            to_version=diff.to_version,
            concepts=[
                ConceptChangeOut(
                    theme=c.theme, term=c.term, change=c.change, by=AuthorOut.from_domain(c.by)
                )
                for c in diff.concepts
            ],
            terms=[
                TermChangeOut(
                    theme=t.theme,
                    concept=t.concept,
                    term=t.term,
                    change=t.change,
                    by=AuthorOut.from_domain(t.by),
                )
                for t in diff.terms
            ],
            criteria=[
                CriterionChangeOut(
                    kind=c.kind, text=c.text, change=c.change, by=AuthorOut.from_domain(c.by)
                )
                for c in diff.criteria
            ],
        )


class PlanVersionOut(BaseModel):
    plan: VersionOut
    diff: DiffOut = Field(description="What changed from the version before.")

    @classmethod
    def from_view(cls, view: PlanVersionView) -> PlanVersionOut:
        return cls(
            plan=VersionOut.from_domain(view.plan, dict(view.term_authors)),
            diff=DiffOut.from_domain(view.diff),
        )
