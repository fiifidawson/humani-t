"""The `queries` stage: the review's accepted plan, compiled into each source's query.

A run searches only a confirmed vocabulary: with no accepted plan, the stage drafts one and
waits at the `vocabulary` gate. Its summary carries the manifest facts, including the exact
query each source will be sent. Running the queries is the `discover` stage's job.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.application.planning import PlanService
from app.domain.plans import ReviewPlan
from app.domain.query import BooleanQuery, compile_vocabulary
from app.domain.runs import GateDecision, GateKind, JsonValue
from app.runs.base import StageContext, StageFailedError

type QueryCompiler = Callable[[BooleanQuery], str]

_REJECTED = (
    "The search plan was not confirmed, so the run stopped. Change the plan and start "
    "the run again."
)


class QueriesStage:
    def __init__(self, plans: PlanService, compilers: Mapping[str, QueryCompiler]) -> None:
        self._plans = plans
        self._compilers = dict(compilers)

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        plan = await self._plans.plan_for_run(context.review_id)
        if plan is None:
            plan = await self._plans.draft_for_run(context.review_id)
        if not plan.is_accepted:
            resolution = await context.require_gate(GateKind.VOCABULARY, gate_payload(plan))
            if resolution.decision is GateDecision.REJECT:
                raise StageFailedError(_REJECTED)
            plan = await self._plans.accept_at_gate(context.review_id, plan.version, resolution)
        await context.raise_if_cancelled()
        query = compile_vocabulary(plan.vocabulary)
        compiled: dict[str, JsonValue] = {}
        for done, (source, compile_for) in enumerate(self._compilers.items(), start=1):
            compiled[source] = compile_for(query)
            await context.progress(done, len(self._compilers), source)
        return {
            "plan_version": plan.version,
            "themes": len(plan.vocabulary.themes),
            "concepts": len(plan.vocabulary.concepts),
            "terms": len(query.all_terms),
            "sources": list(self._compilers),
            "manifest": {
                "vocabulary_version": plan.vocabulary.version,
                "vocabulary_origin": plan.vocabulary.origin.value,
                "criteria_version": plan.version,
                "boolean_query": query.render(),
                "compiled_queries": compiled,
                "sources": list(self._compilers),
                "plan_model_id": plan.model_id,
                "plan_prompt_version": plan.prompt_version,
            },
        }


def gate_payload(plan: ReviewPlan) -> dict[str, JsonValue]:
    """What a member needs to confirm the vocabulary: the draft, as the canvas shows it."""
    vocabulary = plan.vocabulary
    return {
        "version": plan.version,
        "origin": vocabulary.origin.value,
        "is_strict": vocabulary.is_strict,
        "themes": [
            {
                "name": theme,
                "concepts": [
                    {"term": c.term, "synonyms": list(c.synonyms)}
                    for c in vocabulary.concepts_in(theme)
                ],
            }
            for theme in vocabulary.themes
        ],
        "criteria": [{"id": c.id, "kind": c.kind.value, "text": c.text} for c in plan.criteria],
        "rendered": compile_vocabulary(vocabulary).render(),
    }
