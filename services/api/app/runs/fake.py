"""A stage that does no real work but uses every part of the stage seam.

It stands in for each pipeline stage until the real ones are built, so a run
can be started, watched, paused at a gate and cancelled end to end. It sends
nothing anywhere and reads no project data.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from app.domain.runs import GateKind, JsonValue
from app.runs.base import StageContext

_AGENT_ROLES_PER_ROUND = ("retriever", "writer", "critic")


class FakeStage:
    """Count through `items`, after an optional gate, reporting each one.

    `agent_rounds` adds the trace of an agent loop (a planner step, then a
    retriever, writer and critic step per round), as the brief stage reports it.
    `delay_seconds` slows each item down so a person can watch it happen.
    """

    def __init__(
        self,
        items: int = 3,
        *,
        gate: GateKind | None = None,
        agent_rounds: int = 0,
        delay_seconds: float = 0.0,
    ) -> None:
        self._items = items
        self._gate = gate
        self._agent_rounds = agent_rounds
        self._delay_seconds = delay_seconds

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        summary: dict[str, JsonValue] = {"items": self._items}
        if self._gate is not None:
            resolution = await context.require_gate(
                self._gate, {"stage": context.stage, "items": self._items}
            )
            summary["decision"] = resolution.decision.value
        for done in range(1, self._items + 1):
            await context.raise_if_cancelled()
            await self._pause()
            await context.progress(done, self._items, f"Item {done} of {self._items}")
        if self._agent_rounds:
            await context.agent_step({"round": 0, "role": "planner", "status": "ok"})
        for round_number in range(1, self._agent_rounds + 1):
            for role in _AGENT_ROLES_PER_ROUND:
                await context.raise_if_cancelled()
                await self._pause()
                await context.agent_step({"round": round_number, "role": role, "status": "ok"})
        return summary

    async def _pause(self) -> None:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
