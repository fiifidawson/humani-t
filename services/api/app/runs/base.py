"""The seam a pipeline stage plugs into: what a stage is given, and what it may raise.

A stage (discover, mask, screen, the brief…) is a `Stage`. The worker hands it a
`StageContext` to report through and to pause with; the stage returns its
summary. Concrete stages live in their own lanes and are chosen in
`api/dependencies.py`, like every other provider.

Two rules every stage must keep, because the worker relies on them:

- **A stage may run more than once.** A gate, a crash or a deploy interrupts
  it, and the worker then runs it again from its start. Work already stored
  must be found and skipped, not repeated or duplicated.
- **Everything reported is shown to every member.** Progress items, agent
  steps, gate payloads, summaries and `StageFailedError` messages carry
  counts, labels and masked text only — never a document's raw text, an
  unmasked prompt or an exception's text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.domain.runs import GateKind, GateResolution, JsonValue


class StageFailedError(Exception):
    """A failure the stage can explain. The message is shown to members as it is.

    Write it for a humanitarian reviewer: plain language, what happened, no
    document text and no internals ("The model server isn't answering.").
    Any other exception fails the run with a generic message instead.
    """


class StageContext(Protocol):
    """What a running stage may do besides its own work. Each call is one short transaction."""

    @property
    def run_id(self) -> UUID: ...

    @property
    def review_id(self) -> UUID: ...

    @property
    def project_id(self) -> UUID: ...

    @property
    def stage(self) -> str:
        """This stage's id, e.g. `screen`."""
        ...

    async def progress(
        self,
        done: int,
        total: int,
        item: str | None = None,
        *,
        screened: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Report one step: `done` of `total`, and a label for the latest item.

        Send one per item; the interface throttles its own redraws. `screened`
        is the screen stage's structured row for the paper just decided
        (`domain.screening.screened_item`); other stages leave it out.
        """
        ...

    async def agent_step(self, step: Mapping[str, JsonValue]) -> None:
        """Report one `AgentStep`: `round`, `role` and `status`, then any per-role fields."""
        ...

    async def require_gate(
        self, kind: GateKind, payload: Mapping[str, JsonValue]
    ) -> GateResolution:
        """Return the decision on this stage's gate of `kind`, pausing the run until one exists.

        The first call stores the gate, tells every member through the stream,
        and ends this execution of the stage: the run waits without a worker.
        Once someone decides, the run is queued again, the stage runs again from
        its start, and this call returns the decision instead of pausing.
        """
        ...

    async def raise_if_cancelled(self) -> None:
        """End this execution if someone cancelled the run. Call between items.

        A stage that never calls it is cancelled when it finishes, before the
        next stage starts.
        """
        ...


class Stage(Protocol):
    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        """Do the stage's work and return its summary for `stage.finished`."""
        ...
