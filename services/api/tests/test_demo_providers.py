"""The development stand-ins: a demo model and a demo paper source, behind the real seams.

They are chosen only by operator settings that production refuses
(`tests/test_settings.py::TestDevelopmentConveniences`). These specs check that
what they answer is something the real use cases accept, so the whole flow
can be walked on one machine, and that they never pass for the real thing.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.application.brief_loop import CRITIC_SCHEMA, PLANNER_SCHEMA, WRITER_SCHEMA
from app.application.planning import CLARIFY_SCHEMA, DRAFT_SCHEMA
from app.application.screening import screening_request
from app.domain.inference import ChatMessage, ChatRequest, ChatRole
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.inference.demo import MODEL_ID, DemoInferenceProvider
from app.inference.schema import validate_output
from app.search.demo import DemoPaperSearchProvider
from tests.pipeline_support import accepted_plan

_REVIEW = str(UUID(int=1))
_PAPER = (
    "Cash transfers for displaced families in [LOCATION_1]. Malnutrition fell by a "
    "third after six months."
)


def _request(schema: dict[str, Any], user: str) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage(ChatRole.SYSTEM, "system"), ChatMessage(ChatRole.USER, user)),
        output_schema=schema,
    )


async def _answer(request: ChatRequest) -> dict[str, Any]:
    result = await DemoInferenceProvider().chat(request)
    validate_output(result.output, request.output_schema)
    return dict(result.output)


class TestDemoModel:
    async def test_it_never_passes_for_a_real_model(self):
        result = await DemoInferenceProvider().chat(_request(CLARIFY_SCHEMA, "Question: x"))

        assert result.model_id == MODEL_ID
        assert "not a model" in result.model_id

    @pytest.mark.parametrize(
        "schema", [CLARIFY_SCHEMA, DRAFT_SCHEMA, PLANNER_SCHEMA, CRITIC_SCHEMA]
    )
    async def test_its_planning_and_review_answers_match_their_schemas(self, schema):
        await _answer(_request(schema, json.dumps({"question": "Do cash transfers help?"})))

    async def test_a_draft_uses_the_words_of_the_question_and_never_a_placeholder(self):
        answer = await _answer(
            _request(
                DRAFT_SCHEMA, "Question: Should cash transfers replace food in [LOCATION_1]?"
            )
        )

        terms = [c["term"] for t in answer["themes"] for c in t["concepts"]]
        assert "transfers" in terms
        assert not any("[" in term for term in terms)

    async def test_a_screening_answer_agrees_with_its_criteria_and_quotes_the_text_it_was_sent(
        self,
    ):
        request = screening_request(accepted_plan(_REVIEW), _PAPER)

        answer = await _answer(request)

        if answer["label"] == "include":
            assert answer["criteria_matched"] == ["I1", "I2"]
        quotes = [a["quote"] for a in answer.get("answers", []) if a["quote"]]
        assert all(quote in _PAPER for quote in quotes)

    async def test_a_brief_cites_only_the_passages_it_was_given(self):
        payload = {
            "question": "Cash or food?",
            "options": [
                {"name": "Cash", "description": "d"},
                {"name": "Food", "description": "d"},
            ],
            "criteria": ["cost"],
            "passages": [{"tag": "P3", "text": "Cash worked."}],
        }

        answer = await _answer(_request(WRITER_SCHEMA, json.dumps(payload)))

        cited = {tag for claim in answer["bottom_line"] for tag in claim["citations"]} | {
            tag
            for option in answer["options"]
            for claim in option["benefits"]
            for tag in claim["citations"]
        }
        assert cited == {"P3"}
        assert [o["name"] for o in answer["options"]] == ["Cash", "Food"]
        assert "do no harm" in [row["criterion"] for row in answer["trade_offs"]]

    async def test_any_other_schema_gets_the_smallest_answer_that_matches_it(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer", "count", "tags"],
            "properties": {
                "answer": {"type": "string", "minLength": 20, "maxLength": 40},
                "count": {"type": "integer", "minimum": 2},
                "tags": {"type": "array", "minItems": 1, "items": {"enum": ["a", "b"]}},
            },
        }

        await _answer(_request(schema, "anything"))


class TestDemoSource:
    def _query(self) -> Any:
        return compile_vocabulary(
            Vocabulary(
                concepts=(
                    Concept("cash", theme="intervention"),
                    Concept("malnutrition", theme="outcome"),
                ),
                origin=VocabularyOrigin.USER_SUPPLIED,
            )
        )

    async def test_it_writes_papers_about_what_the_query_asks_for(self):
        papers = await DemoPaperSearchProvider().search(self._query(), limit=25)

        assert papers
        assert all(p.title.startswith("Demo paper") for p in papers)
        assert all("cash" in p.title or "malnutrition" in p.title for p in papers)

    async def test_it_respects_the_limit_and_answers_the_same_way_twice(self):
        source = DemoPaperSearchProvider()

        first = await source.search(self._query(), limit=3)

        assert len(first) == 3
        assert first == await source.search(self._query(), limit=3)

    async def test_its_papers_give_every_later_stage_something_to_show(self):
        papers = await DemoPaperSearchProvider().search(self._query(), limit=25)

        assert any(p.abstract is None for p in papers)
        assert any(p.abstract and "@example.org" in p.abstract for p in papers)
        assert any(p.doi and p.url is None for p in papers)
        assert all(p.doi is None or p.doi.startswith("10.5555/") for p in papers)
