"""The seam a pipeline stage plugs into: what a stage is given, and what it may raise.

Every stage must keep two rules. It may run more than once after a gate, crash or
deploy, so stored work must be found and skipped, not repeated. And everything it
reports is shown to every member, so it carries counts, labels and masked text only:
never raw text, an unmasked prompt or an exception's text.
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

        Send one per item. Only the screen stage passes `screened`.
        """
        ...

    async def agent_step(self, step: Mapping[str, JsonValue]) -> None:
        """Report one `AgentStep`: `round`, `role` and `status`, then any per-role fields."""
        ...

    async def require_gate(
        self, kind: GateKind, payload: Mapping[str, JsonValue]
    ) -> GateResolution:
        """Return the decision on this stage's gate of `kind`, pausing the run until one exists.

        The first call ends this execution of the stage. Once someone decides, the stage
        runs again from its start and this call returns the decision.
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
