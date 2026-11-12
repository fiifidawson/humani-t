"""The run event stream's payloads, published as OpenAPI components.

These describe the wire, they do not build it: the domain builds each payload. They
exist so the web app generates its types, and a test checks every event against them.
Open-ended values are plain JSON objects, because TypeScript generators cannot name
a recursive JSON type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel

_Stage = Literal["queries", "discover", "ingest", "mask", "screen", "export", "index", "answer"]


class StageStartedData(BaseModel):
    stage: _Stage
    position: int = Field(ge=1, description="This stage's place within this run, from 1.")
    total: int = Field(ge=1, description="How many stages this run covers.")
    at: datetime


class ScreenedItemData(BaseModel):
    """The screen stage's row for one paper, as the live screening list shows it."""

    model_config = ConfigDict(extra="forbid")

    paper_id: UUID = Field(description="The paper, as `GET /reviews/{id}/papers` lists it.")
    outcome: Literal["screened", "failed"] = Field(
        description="failed: the privacy check stopped its text, so nothing was sent."
    )
    label: Literal["include", "exclude", "uncertain"] | None
    priority: Literal["high", "medium", "low"] | None
    tier: (
        Literal["highly_relevant", "relevant", "possibly_relevant", "excluded", "uncertain"]
        | None
    ) = Field(description="The name shown: Highly relevant, Relevant, …; null when failed.")
    title: str | None = Field(
        description="The first line of the masked text the model read; null when failed."
    )
    reason: str = Field(description="One line: the model's reason, or why it failed. Masked.")


class StageProgressData(BaseModel):
    stage: _Stage
    done: int = Field(ge=0)
    total: int = Field(ge=0)
    item: str | None = Field(
        description=(
            "A label for the latest item; masked text only. For the screen stage, "
            "deprecated in favour of `screened`."
        )
    )
    at: datetime | None = Field(
        default=None, description="When it happened. Absent on events stored before 2026-11."
    )
    screened: ScreenedItemData | None = Field(
        default=None, description="The screen stage's row for the paper just decided."
    )


class StageFinishedData(BaseModel):
    stage: _Stage
    seconds: float
    summary: dict[str, Any] = Field(description="The stage's counts.")


class AgentStepData(BaseModel):
    """One `AgentStep`. Per-role fields (seconds, tokens, output, …) follow these three."""

    model_config = ConfigDict(extra="allow")

    round: int = Field(ge=0, description="0 for the planner.")
    role: Literal["planner", "retriever", "writer", "checks", "critic"]
    status: Literal["ok", "refused", "invalid", "error"]


class _Exact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateConceptData(_Exact):
    term: str
    synonyms: list[str]


class GateThemeData(_Exact):
    name: str
    concepts: list[GateConceptData]


class GateCriterionData(_Exact):
    id: str = Field(description="I1, I2… for inclusion, E1… for exclusion.")
    kind: Literal["inclusion", "exclusion"]
    text: str


class VocabularyGatePayload(_Exact):
    """A `vocabulary` gate's payload: the draft to confirm, as the draft canvas shows it."""

    version: int = Field(ge=1, description="The plan version waiting for confirmation.")
    origin: Literal["user_supplied", "llm_assisted"]
    is_strict: bool = Field(description="True for your own terms, used exactly as given.")
    themes: list[GateThemeData]
    criteria: list[GateCriterionData]
    rendered: str = Field(description="The Boolean query the sources will be sent.")


class GateRequiredData(BaseModel):
    gate_id: UUID
    kind: Literal["vocabulary", "privacy_review"]
    payload: Annotated[
        VocabularyGatePayload | dict[str, Any], Field(union_mode="left_to_right")
    ] = Field(
        description=(
            "What the person needs to decide: a `VocabularyGatePayload` for a vocabulary "
            "gate; for a privacy review, the documents and their masked counts."
        )
    )


class GateResolvedData(BaseModel):
    gate_id: UUID
    by: UUID = Field(description="The member who decided.")
    decision: Literal["approve", "reject"]


class JobFinishedData(BaseModel):
    outcome: Literal["done", "failed", "cancelled"]
    seconds: float
    error: str | None = Field(description="Plain language, for members; never a stack trace.")


class _Event(BaseModel):
    id: int = Field(ge=1, description="Counts from 1 within the run; send it back to resume.")


class StageStartedEvent(_Event):
    event: Literal["stage.started"]
    data: StageStartedData


class StageProgressEvent(_Event):
    event: Literal["stage.progress"]
    data: StageProgressData


class StageFinishedEvent(_Event):
    event: Literal["stage.finished"]
    data: StageFinishedData


class AgentStepEvent(_Event):
    event: Literal["agent.step"]
    data: AgentStepData


class GateRequiredEvent(_Event):
    event: Literal["gate.required"]
    data: GateRequiredData


class GateResolvedEvent(_Event):
    event: Literal["gate.resolved"]
    data: GateResolvedData


class JobFinishedEvent(_Event):
    event: Literal["job.finished"]
    data: JobFinishedData


class RunStreamEvent(
    RootModel[
        Annotated[
            StageStartedEvent
            | StageProgressEvent
            | StageFinishedEvent
            | AgentStepEvent
            | GateRequiredEvent
            | GateResolvedEvent
            | JobFinishedEvent,
            Field(discriminator="event"),
        ]
    ]
):
    """One event on `GET /runs/{run_id}/events`: its `id:`, `event:` and parsed `data:`."""
