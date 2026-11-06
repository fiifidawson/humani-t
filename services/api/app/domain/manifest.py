"""The run manifest: everything needed to reproduce a run, or to say why two runs differ.

Each stage records its own facts at the moment it finishes, under a
`"manifest"` key in its summary (architecture §4.2). This module gathers them,
with the run's own dates, into one `RunManifest`. It never reconstructs a fact
a stage did not record, and it never picks one value when two stages disagree:
both are kept, so a run that mixed versions says so.

The keys a stage may record, and how they combine:

- `vocabulary_version`, `criteria_version`, `plan_version` (int): every distinct value;
- `sources` (list of str) and `queries` (source -> rendered query);
- `model_id` (str) or `model_ids` (list of str): every distinct model;
- `prompt_version` (str), filed under the stage, or `prompt_versions`
  (name -> version), filed under `stage.name`;
- `papers_from_run` (str): the run whose discovery a later run worked from.

Any other key is kept in that stage's `facts` as it was recorded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from app.domain.runs import JsonValue, Run

MANIFEST_KEY = "manifest"


@dataclass(frozen=True, slots=True)
class StageSummary:
    """What a finished stage reported, as its `stage.finished` event carries it."""

    stage: str
    seconds: float
    summary: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StageManifest:
    """One finished stage's own manifest facts; empty if it recorded none."""

    stage: str
    seconds: float
    facts: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: UUID
    review_id: UUID
    project_id: UUID
    stages: tuple[str, ...]
    outcome: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stage_facts: tuple[StageManifest, ...]

    @property
    def is_complete(self) -> bool:
        """Whether every stage the run asked for finished and reported."""
        return tuple(s.stage for s in self.stage_facts) == self.stages

    @property
    def vocabulary_versions(self) -> tuple[int, ...]:
        return self._versions("vocabulary_version")

    @property
    def criteria_versions(self) -> tuple[int, ...]:
        return self._versions("criteria_version")

    @property
    def plan_versions(self) -> tuple[int, ...]:
        return self._versions("plan_version")

    @property
    def sources(self) -> tuple[str, ...]:
        """Every source any stage named, in the order first named."""
        seen: dict[str, None] = {}
        for facts in self._facts():
            for source in _strings(facts.get("sources")):
                seen.setdefault(source)
        return tuple(seen)

    @property
    def queries(self) -> Mapping[str, str]:
        found: dict[str, str] = {}
        for facts in self._facts():
            value = facts.get("queries")
            if isinstance(value, dict):
                found.update({k: v for k, v in value.items() if isinstance(v, str)})
        return MappingProxyType(found)

    @property
    def model_ids(self) -> tuple[str, ...]:
        models: set[str] = set()
        for facts in self._facts():
            model = facts.get("model_id")
            if isinstance(model, str):
                models.add(model)
            models.update(_strings(facts.get("model_ids")))
        return tuple(sorted(models))

    @property
    def prompt_versions(self) -> Mapping[str, str]:
        found: dict[str, str] = {}
        for stage in self.stage_facts:
            single = stage.facts.get("prompt_version")
            if isinstance(single, str):
                found[stage.stage] = single
            named = stage.facts.get("prompt_versions")
            if isinstance(named, dict):
                found.update(
                    {f"{stage.stage}.{k}": v for k, v in named.items() if isinstance(v, str)}
                )
        return MappingProxyType(dict(sorted(found.items())))

    @property
    def papers_from_runs(self) -> tuple[str, ...]:
        found = {
            value
            for facts in self._facts()
            if isinstance(value := facts.get("papers_from_run"), str)
        }
        return tuple(sorted(found))

    def _facts(self) -> list[Mapping[str, JsonValue]]:
        return [s.facts for s in self.stage_facts]

    def _versions(self, key: str) -> tuple[int, ...]:
        values = {
            value
            for facts in self._facts()
            if isinstance(value := facts.get(key), int) and not isinstance(value, bool)
        }
        return tuple(sorted(values))


def _strings(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def assemble_manifest(run: Run, finished: Sequence[StageSummary]) -> RunManifest:
    """The run's manifest from its finished stages' summaries, in the order they finished."""
    stage_facts: list[StageManifest] = []
    for stage in finished:
        facts = stage.summary.get(MANIFEST_KEY)
        recorded: dict[str, JsonValue] = dict(facts) if isinstance(facts, dict) else {}
        stage_facts.append(
            StageManifest(
                stage=stage.stage, seconds=stage.seconds, facts=MappingProxyType(recorded)
            )
        )
    return RunManifest(
        run_id=run.id,
        review_id=run.review_id,
        project_id=run.project_id,
        stages=run.stages,
        outcome=None if run.outcome is None else run.outcome.value,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stage_facts=tuple(stage_facts),
    )
