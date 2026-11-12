"""The decision brief and the trace of the loop that wrote it, as rules with no I/O.

Every statement is a `Claim`: evidence citing retrieved passages, or an uncited hypothesis.
Code, not the model, checks that every cited passage was retrieved. Nothing here ranks the
options or names a winner: the person decides.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Self, cast
from uuid import UUID

from app.domain.runs import JsonValue
from app.domain.screening import Passage, PassageSourceKind

CAVEAT = (
    "This brief is an aid to a decision, not the decision. It covers only the papers and "
    "documents in this run; check the cited sources before acting."
)

HUMANITARIAN_PRINCIPLES = frozenset(
    {"humanity", "impartiality", "neutrality", "independence", "do no harm"}
)

MIN_OPTIONS = 2
MAX_OPTIONS = 4
MIN_BOTTOM_LINE = 2
MAX_BOTTOM_LINE = 3

# An option named "Cash (recommended)" would rank options inside the brief
# itself, which the brief must never do.
_WINNER_LABEL = re.compile(
    r"\b(recommended|recommendation|winner|winning|best option|top choice|preferred)\b|#\s*\d",
    re.IGNORECASE,
)


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"


class ClaimSupport(StrEnum):
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"


# One vocabulary for where a passage came from, shared with the pipeline that tags it.
SourceKind = PassageSourceKind


class ConflictType(StrEnum):
    HIERARCHY = "hierarchy"
    CONTEXT = "context"
    NOISE = "noise"


class StopReason(StrEnum):
    ACCEPTED = "accepted"
    MAX_ROUNDS = "max_rounds"
    INVALID_OUTPUT = "invalid_output"
    NO_PROGRESS = "no_progress"


class AgentRole(StrEnum):
    PLANNER = "planner"
    RETRIEVER = "retriever"
    WRITER = "writer"
    CHECKS = "checks"
    CRITIC = "critic"


class StepStatus(StrEnum):
    OK = "ok"
    REFUSED = "refused"
    INVALID = "invalid"
    ERROR = "error"


def _require_text(owner: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{owner} must not be blank")


@dataclass(frozen=True, slots=True)
class Citation:
    """A claim bound to one retrieved passage, from a paper or a team's own document."""

    tag: str
    source_kind: SourceKind

    def __post_init__(self) -> None:
        _require_text("Citation.tag", self.tag)


@dataclass(frozen=True, slots=True)
class Claim:
    """One statement: evidence cites what it rests on; a hypothesis cites nothing."""

    text: str
    support: ClaimSupport
    citations: tuple[Citation, ...] = ()

    def __post_init__(self) -> None:
        _require_text("Claim.text", self.text)
        if self.support is ClaimSupport.EVIDENCE and not self.citations:
            raise ValueError("Claim.citations must name a passage for an evidence claim")
        if self.support is ClaimSupport.HYPOTHESIS and self.citations:
            raise ValueError("Claim.citations must be empty for a hypothesis")
        tags = [c.tag for c in self.citations]
        if len(set(tags)) != len(tags):
            raise ValueError("Claim.citations must not cite a passage twice")

    @property
    def is_evidence(self) -> bool:
        return self.support is ClaimSupport.EVIDENCE


@dataclass(frozen=True, slots=True)
class Option:
    name: str
    description: str
    strength: EvidenceStrength
    benefits: tuple[Claim, ...]
    risks: tuple[Claim, ...]

    def __post_init__(self) -> None:
        _require_text("Option.name", self.name)
        if _WINNER_LABEL.search(self.name):
            raise ValueError("Option.name must not rank the option or call it the winner")
        _require_text("Option.description", self.description)


@dataclass(frozen=True, slots=True)
class Assessment:
    """One cell of the trade-off matrix: what the evidence says of one option."""

    option: str
    claim: Claim


@dataclass(frozen=True, slots=True)
class TradeOff:
    """One criterion, assessed for every option. No cell carries a number."""

    criterion: str
    assessments: tuple[Assessment, ...]

    def __post_init__(self) -> None:
        _require_text("TradeOff.criterion", self.criterion)

    @property
    def is_humanitarian(self) -> bool:
        return self.criterion.strip().casefold() in HUMANITARIAN_PRINCIPLES


@dataclass(frozen=True, slots=True)
class Conflict:
    """Where sources disagree, why (`conflict_type`), and optionally what that means."""

    question: str
    conflict_type: ConflictType
    positions: tuple[Claim, ...]
    meaning: Claim | None = None

    def __post_init__(self) -> None:
        _require_text("Conflict.question", self.question)
        if len(self.positions) < 2:
            raise ValueError("Conflict.positions must hold at least two positions")
        if self.meaning is not None and self.meaning.is_evidence:
            raise ValueError("Conflict.meaning is the model's reasoning, so a hypothesis")


@dataclass(frozen=True, slots=True)
class Consideration:
    """'If this criterion matters most, this option, because …'. Never a verdict."""

    criterion: str
    option: str
    because: Claim

    def __post_init__(self) -> None:
        _require_text("Consideration.criterion", self.criterion)


@dataclass(frozen=True, slots=True)
class Gap:
    text: str

    def __post_init__(self) -> None:
        _require_text("Gap.text", self.text)


@dataclass(frozen=True, slots=True)
class ClaimPlace:
    """Where a claim sits in a brief: a stable path and the labels a person reads."""

    path: str
    section: str
    option: str | None = None
    criterion: str | None = None


@dataclass(frozen=True, slots=True)
class BriefBody:
    """What the Writer drafts: everything in a brief that comes from the model."""

    bottom_line: tuple[Claim, ...]
    options: tuple[Option, ...]
    trade_offs: tuple[TradeOff, ...]
    conflicts: tuple[Conflict, ...]
    considerations: tuple[Consideration, ...]
    gaps: tuple[Gap, ...]

    def __post_init__(self) -> None:
        if not MIN_BOTTOM_LINE <= len(self.bottom_line) <= MAX_BOTTOM_LINE:
            raise ValueError(
                f"BriefBody.bottom_line must be {MIN_BOTTOM_LINE} or {MAX_BOTTOM_LINE} claims"
            )
        if not MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(
                f"BriefBody.options must hold between {MIN_OPTIONS} and {MAX_OPTIONS} options"
            )
        keys = [_option_key(o.name) for o in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("BriefBody.options must have distinct names")
        for row in self.trade_offs:
            assessed = [_option_key(a.option) for a in row.assessments]
            if sorted(assessed) != sorted(keys):
                raise ValueError(
                    "TradeOff.assessments must assess every option in the brief exactly once"
                )
        for consideration in self.considerations:
            if _option_key(consideration.option) not in keys:
                raise ValueError("Consideration.option must name an option in the brief")

    def claims(self) -> Iterator[tuple[ClaimPlace, Claim]]:
        """Every claim in reading order, with where it sits."""
        for i, claim in enumerate(self.bottom_line):
            yield ClaimPlace(f"bottom_line/{i}", "bottom_line"), claim
        for i, option in enumerate(self.options):
            for j, claim in enumerate(option.benefits):
                yield ClaimPlace(f"options/{i}/benefits/{j}", "benefit", option.name), claim
            for j, claim in enumerate(option.risks):
                yield ClaimPlace(f"options/{i}/risks/{j}", "risk", option.name), claim
        for i, row in enumerate(self.trade_offs):
            for j, cell in enumerate(row.assessments):
                place = ClaimPlace(
                    f"trade_offs/{i}/assessments/{j}", "trade_off", cell.option, row.criterion
                )
                yield place, cell.claim
        for i, conflict in enumerate(self.conflicts):
            for j, claim in enumerate(conflict.positions):
                yield ClaimPlace(f"conflicts/{i}/positions/{j}", "conflict"), claim
            if conflict.meaning is not None:
                yield ClaimPlace(f"conflicts/{i}/meaning", "conflict"), conflict.meaning
        for i, item in enumerate(self.considerations):
            place = ClaimPlace(
                f"considerations/{i}/because", "consideration", item.option, item.criterion
            )
            yield place, item.because

    def cited_tags(self) -> tuple[str, ...]:
        """Every tag the brief cites, once each, in order of first appearance."""
        seen: dict[str, None] = {}
        for _, claim in self.claims():
            for citation in claim.citations:
                seen.setdefault(citation.tag, None)
        return tuple(seen)


def _option_key(name: str) -> str:
    return " ".join(name.casefold().split())


@dataclass(frozen=True, slots=True)
class Problem:
    """Something the checks found wrong with a draft. `where` is a claim or section path."""

    code: str
    message: str
    where: str | None = None

    def __post_init__(self) -> None:
        _require_text("Problem.code", self.code)
        _require_text("Problem.message", self.message)

    def to_payload(self) -> dict[str, JsonValue]:
        return {"code": self.code, "message": self.message, "where": self.where}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        where = payload.get("where")
        return cls(
            str(payload["code"]), str(payload["message"]), None if where is None else str(where)
        )


def check_claims(
    claims: Iterable[tuple[str, Claim]], retrieved_tags: Collection[str]
) -> tuple[Problem, ...]:
    """A problem for every citation, among `(path, claim)` pairs, of a passage never retrieved.

    A model can invent a plausible tag as easily as a plausible sentence; only
    the record of what retrieval returned can tell them apart.
    """
    return tuple(
        Problem(
            "citation_not_retrieved",
            f"{citation.tag} is cited but was never retrieved; cite only the passages given.",
            path,
        )
        for path, claim in claims
        for citation in claim.citations
        if citation.tag not in retrieved_tags
    )


def check_citations(brief: BriefBody, retrieved_tags: Collection[str]) -> tuple[Problem, ...]:
    """`check_claims` over every claim of a brief."""
    return check_claims(
        ((place.path, claim) for place, claim in brief.claims()), retrieved_tags
    )


def check_brief(brief: BriefBody, retrieved_tags: Collection[str]) -> tuple[Problem, ...]:
    """Everything code checks in a draft that the types alone cannot refuse.

    The citations, a humanitarian principle among the criteria, and evidence behind every
    option rated on evidence.
    """
    problems = list(check_citations(brief, retrieved_tags))
    if not any(row.is_humanitarian for row in brief.trade_offs):
        problems.append(
            Problem(
                "no_humanitarian_criterion",
                "The trade-offs must include the relevant humanitarian principles "
                "(humanity, impartiality, neutrality, independence, do no harm).",
                "trade_offs",
            )
        )
    for i, option in enumerate(brief.options):
        if option.strength is EvidenceStrength.INSUFFICIENT:
            continue
        if not any(c.is_evidence for c in (*option.benefits, *option.risks)):
            problems.append(
                Problem(
                    "option_without_evidence",
                    f"{option.name} is rated {option.strength.value} but none of its "
                    "benefits or risks cites a source; cite one or rate it insufficient.",
                    f"options/{i}",
                )
            )
    return tuple(problems)


@dataclass(frozen=True, slots=True)
class CriticScores:
    """The Critic's review of a draft: each 0 (poor), 1 (partly) or 2 (good). Never summed."""

    coverage: int
    groundedness: int
    disagreements: int
    uncertainty: int
    humanitarian_awareness: int
    specificity: int

    def __post_init__(self) -> None:
        for name in CRITIC_DIMENSIONS:
            value: object = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2:
                raise ValueError(f"CriticScores.{name} must be 0, 1 or 2")

    def to_payload(self) -> dict[str, JsonValue]:
        return {name: cast(int, getattr(self, name)) for name in CRITIC_DIMENSIONS}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        return cls(**{name: int(payload[name]) for name in CRITIC_DIMENSIONS})


CRITIC_DIMENSIONS = (
    "coverage",
    "groundedness",
    "disagreements",
    "uncertainty",
    "humanitarian_awareness",
    "specificity",
)


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """A passage the Retriever returned, which the Writer saw, and the search that found it."""

    passage: Passage
    found_by: str
    round_number: int

    def __post_init__(self) -> None:
        _require_text("RetrievedPassage.found_by", self.found_by)
        if self.round_number < 0:
            raise ValueError("RetrievedPassage.round_number must not be negative")


@dataclass(frozen=True, slots=True)
class SourceDetails:
    """What the pipeline knew of a cited source when the brief was written.

    Every field but the title is optional: `None` means "not available",
    and the interface omits it rather than guessing.
    """

    title: str
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    screening_label: str | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        _require_text("SourceDetails.title", self.title)


@dataclass(frozen=True, slots=True)
class AgentStep:
    """One step of the loop that wrote a brief.

    `round_number` is 0 for the Planner and the first retrieval. `output` is masked;
    `error` is a plain sentence for a person, never an exception's text.
    """

    round_number: int
    role: AgentRole
    status: StepStatus
    seconds: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    output: Mapping[str, JsonValue] | None = None
    error: str | None = None
    queries: tuple[str, ...] = ()
    passages_found: int | None = None
    passages_new: int | None = None
    passages_total: int | None = None
    problems: tuple[Problem, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.round_number < 0:
            raise ValueError("AgentStep.round_number must not be negative")
        if self.role is AgentRole.PLANNER and self.round_number != 0:
            raise ValueError("AgentStep.round_number is 0 for the planner")
        if self.status is not StepStatus.OK and not (self.error or "").strip():
            raise ValueError("AgentStep.error must say why a step did not succeed")
        if self.seconds < 0:
            raise ValueError("AgentStep.seconds must not be negative")

    @property
    def is_model_call(self) -> bool:
        return self.prompt_version is not None and self.status is not StepStatus.REFUSED

    def to_payload(self) -> dict[str, JsonValue]:
        """The `agent.step` event: round, role and status, then whatever this role reports."""
        payload: dict[str, JsonValue] = {
            "round": self.round_number,
            "role": self.role.value,
            "status": self.status.value,
            "seconds": self.seconds,
        }
        if self.prompt_tokens is not None or self.completion_tokens is not None:
            payload["tokens"] = {
                "prompt": self.prompt_tokens or 0,
                "completion": self.completion_tokens or 0,
            }
        optional: dict[str, JsonValue] = {
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "output": None if self.output is None else dict(self.output),
            "error": self.error,
            "passages_found": self.passages_found,
            "passages_new": self.passages_new,
            "passages_total": self.passages_total,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        if self.queries:
            payload["queries"] = list(self.queries)
        if self.role is AgentRole.CHECKS:
            payload["problems"] = [p.to_payload() for p in self.problems]
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        tokens: Mapping[str, Any] = payload.get("tokens") or {}
        return cls(
            round_number=int(payload["round"]),
            role=AgentRole(payload["role"]),
            status=StepStatus(payload["status"]),
            seconds=float(payload.get("seconds", 0.0)),
            prompt_tokens=tokens.get("prompt"),
            completion_tokens=tokens.get("completion"),
            model_id=payload.get("model_id"),
            prompt_version=payload.get("prompt_version"),
            output=payload.get("output"),
            error=payload.get("error"),
            queries=tuple(payload.get("queries", ())),
            passages_found=payload.get("passages_found"),
            passages_new=payload.get("passages_new"),
            passages_total=payload.get("passages_total"),
            problems=tuple(Problem.from_payload(p) for p in payload.get("problems", ())),
        )


def _require_aware(owner: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{owner} must be timezone-aware")


def _check_rounds(owner: str, rounds: int, kept_round: int, max_rounds: int) -> None:
    if not 1 <= rounds <= max_rounds:
        raise ValueError(f"{owner}.rounds must be between 1 and max_rounds")
    if not 1 <= kept_round <= rounds:
        raise ValueError(f"{owner}.kept_round must be one of the rounds run")


@dataclass(frozen=True, slots=True)
class DecisionBrief:
    """The loop's output for one run: one version of a review's brief.

    `question` is the masked question as the model saw it. `body` is the draft of
    `kept_round`, and `warnings` are the checks still failing on it.
    """

    id: UUID
    project_id: UUID
    review_id: UUID
    run_id: UUID
    version: int
    question: str
    body: BriefBody
    model_id: str
    rounds: int
    kept_round: int
    max_rounds: int
    stopped_because: StopReason
    created_at: datetime
    caveat: str = CAVEAT
    warnings: tuple[Problem, ...] = ()
    quality: CriticScores | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("DecisionBrief.version counts from 1")
        _require_text("DecisionBrief.question", self.question)
        _require_text("DecisionBrief.model_id", self.model_id)
        _require_text("DecisionBrief.caveat", self.caveat)
        _check_rounds("DecisionBrief", self.rounds, self.kept_round, self.max_rounds)
        _require_aware("DecisionBrief.created_at", self.created_at)


@dataclass(frozen=True, slots=True)
class BriefRecord:
    """A brief as storage holds it: ids and loop facts in the clear, every text sealed.

    Each sealed field is encrypted under the project's key and bound to this row.
    """

    id: UUID
    project_id: UUID
    review_id: UUID
    run_id: UUID
    version: int
    created_at: datetime
    model_id: str
    rounds: int
    kept_round: int
    max_rounds: int
    stopped_because: StopReason
    content_sealed: bytes
    evidence_sealed: bytes
    trace_sealed: bytes

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("BriefRecord.version counts from 1")
        _check_rounds("BriefRecord", self.rounds, self.kept_round, self.max_rounds)
        _require_aware("BriefRecord.created_at", self.created_at)
        for name in ("content_sealed", "evidence_sealed", "trace_sealed"):
            if not getattr(self, name):
                raise ValueError(f"BriefRecord.{name} must not be empty")


def claim_to_dict(claim: Claim) -> dict[str, JsonValue]:
    return {
        "text": claim.text,
        "support": claim.support.value,
        "citations": [
            {"tag": c.tag, "source_kind": c.source_kind.value} for c in claim.citations
        ],
    }


def claim_from_dict(data: Mapping[str, Any]) -> Claim:
    return Claim(
        str(data["text"]),
        ClaimSupport(data["support"]),
        tuple(Citation(str(c["tag"]), SourceKind(c["source_kind"])) for c in data["citations"]),
    )


def body_to_dict(body: BriefBody) -> dict[str, JsonValue]:
    """The body as plain JSON, for sealing. `body_from_dict` reverses it exactly."""
    return {
        "bottom_line": [claim_to_dict(c) for c in body.bottom_line],
        "options": [
            {
                "name": o.name,
                "description": o.description,
                "strength": o.strength.value,
                "benefits": [claim_to_dict(c) for c in o.benefits],
                "risks": [claim_to_dict(c) for c in o.risks],
            }
            for o in body.options
        ],
        "trade_offs": [
            {
                "criterion": row.criterion,
                "assessments": [
                    {"option": a.option, "claim": claim_to_dict(a.claim)}
                    for a in row.assessments
                ],
            }
            for row in body.trade_offs
        ],
        "conflicts": [
            {
                "question": c.question,
                "conflict_type": c.conflict_type.value,
                "positions": [claim_to_dict(p) for p in c.positions],
                "meaning": None if c.meaning is None else claim_to_dict(c.meaning),
            }
            for c in body.conflicts
        ],
        "considerations": [
            {"criterion": c.criterion, "option": c.option, "because": claim_to_dict(c.because)}
            for c in body.considerations
        ],
        "gaps": [g.text for g in body.gaps],
    }


def body_from_dict(data: Mapping[str, Any]) -> BriefBody:
    """Rebuild a body from `body_to_dict`'s output. Raises `ValueError` if it breaks a rule."""
    return BriefBody(
        bottom_line=tuple(claim_from_dict(c) for c in data["bottom_line"]),
        options=tuple(
            Option(
                name=str(o["name"]),
                description=str(o["description"]),
                strength=EvidenceStrength(o["strength"]),
                benefits=tuple(claim_from_dict(c) for c in o["benefits"]),
                risks=tuple(claim_from_dict(c) for c in o["risks"]),
            )
            for o in data["options"]
        ),
        trade_offs=tuple(
            TradeOff(
                str(row["criterion"]),
                tuple(
                    Assessment(str(a["option"]), claim_from_dict(a["claim"]))
                    for a in row["assessments"]
                ),
            )
            for row in data["trade_offs"]
        ),
        conflicts=tuple(
            Conflict(
                str(c["question"]),
                ConflictType(c["conflict_type"]),
                tuple(claim_from_dict(p) for p in c["positions"]),
                None if c.get("meaning") is None else claim_from_dict(c["meaning"]),
            )
            for c in data["conflicts"]
        ),
        considerations=tuple(
            Consideration(str(c["criterion"]), str(c["option"]), claim_from_dict(c["because"]))
            for c in data["considerations"]
        ),
        gaps=tuple(Gap(str(g)) for g in data["gaps"]),
    )


def passages_by_tag(
    evidence: Sequence[RetrievedPassage],
) -> dict[str, tuple[RetrievedPassage, ...]]:
    """The retrieved passages grouped by the tag a claim cites them by, in retrieval order."""
    grouped: dict[str, list[RetrievedPassage]] = {}
    for item in evidence:
        grouped.setdefault(item.passage.tag, []).append(item)
    return {tag: tuple(items) for tag, items in grouped.items()}
