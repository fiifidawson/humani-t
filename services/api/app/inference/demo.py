"""A stand-in model for development: canned answers made in this process, no model server.

It lets a developer walk the whole review flow without Ollama. Production refuses
`demo_inference`, and its `model_id` names it in every answer, so nothing it made can
pass for a real model's. Answers are shaped by the request's schema and built from the
masked text it was sent. Tests script `FakeInferenceProvider` instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.inference import ChatRequest, ChatResult, TokenUsage
from app.inference.schema import validate_output

MODEL_ID = "demo-stand-in (not a model)"

_WORD = re.compile(r"[A-Za-z][A-Za-z-]{4,}")
_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "among",
        "before",
        "could",
        "every",
        "might",
        "other",
        "question",
        "should",
        "their",
        "there",
        "these",
        "those",
        "where",
        "which",
        "while",
        "would",
    }
)


class DemoInferenceProvider:
    """An `InferenceProvider` whose answers are made by code, deterministically."""

    @property
    def model_id(self) -> str:
        return MODEL_ID

    async def chat(self, request: ChatRequest) -> ChatResult:
        schema = request.output_schema
        text = request.messages[-1].content
        output = _answer(schema, text)
        validate_output(output, schema)
        return ChatResult(
            output=output,
            model_id=MODEL_ID,
            usage=TokenUsage(
                prompt_tokens=sum(len(m.content.split()) for m in request.messages),
                completion_tokens=len(json.dumps(output).split()),
            ),
        )


def _answer(schema: Mapping[str, Any], text: str) -> dict[str, Any]:
    properties: Mapping[str, Any] = schema.get("properties", {})
    if "criteria_matched" in properties:
        return _screening(schema, text)
    if "questions" in properties:
        return _clarifying(text)
    if "themes" in properties:
        return _draft(text)
    if "sub_questions" in properties:
        return _brief_plan(text)
    if "bottom_line" in properties:
        return _brief(schema, text)
    if "verdict" in properties:
        return _review()
    value = _minimal(schema)
    return value if isinstance(value, dict) else {}


def _words(text: str, limit: int) -> list[str]:
    """Distinct, longer words of `text`, in order: the demo's idea of what it is about."""
    seen: list[str] = []
    for word in _WORD.findall(_PLACEHOLDER.sub(" ", text)):
        lowered = word.lower()
        if lowered not in _STOPWORDS and lowered not in seen:
            seen.append(lowered)
        if len(seen) == limit:
            break
    return seen


def _question_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Question:"):
            return line.removeprefix("Question:").strip()
    return text


def _clarifying(text: str) -> dict[str, Any]:
    return {
        "questions": [
            {
                "text": "Which outcomes matter most for this decision?",
                "suggestions": ["Health", "Food security", "Protection risks"],
            },
            {
                "text": "Which settings should the search cover?",
                "suggestions": ["Camps", "Towns", "Any setting"],
            },
        ]
    }


def _draft(text: str) -> dict[str, Any]:
    words = _words(_question_of(text), 4) or ["humanitarian", "response"]
    current = _current_plan(text)
    if current is not None:
        change = _words(text.rsplit("Change request:", 1)[-1], 1)
        concepts: list[Any] = current["themes"][0]["concepts"]
        if change and "Change request:" in text and len(concepts) < 12:
            concepts.append({"term": change[0], "synonyms": []})
        return current
    half = max(1, len(words) // 2)
    groups = [words[:half], words[half:]] if len(words) > 1 else [words]
    return {
        "themes": [
            {
                "name": f"theme {n}",
                "concepts": [{"term": w, "synonyms": []} for w in group],
            }
            for n, group in enumerate(groups, start=1)
            if group
        ],
        "criteria": {
            "inclusion": [f"Studies about {words[0]}", "Reports outcomes for people affected"],
            "exclusion": ["Not a humanitarian or crisis setting"],
        },
    }


def _current_plan(text: str) -> dict[str, Any] | None:
    for line in text.splitlines():
        if line.startswith("Current plan (JSON):"):
            plan: dict[str, Any] = json.loads(line.removeprefix("Current plan (JSON):"))
            return plan
    return None


def _screening(schema: Mapping[str, Any], text: str) -> dict[str, Any]:
    ids: list[str] = list(schema["properties"]["criteria_matched"]["items"]["enum"])
    inclusion = [i for i in ids if i.startswith("I")]
    roll = int(hashlib.sha256(text.encode()).hexdigest(), 16) % 4
    is_included = roll < 3
    matched = inclusion if is_included else []
    answer: dict[str, Any] = {
        "label": "include" if is_included else "exclude",
        "score": 0.6 + 0.1 * roll,
        "priority": ("high", "medium", "low", "low")[roll],
        "rationale": (
            "Demo answer, not a model's: "
            + ("it meets the inclusion criteria." if is_included else "it misses them.")
        ),
        "criteria_matched": matched,
    }
    if "answers" in schema["properties"]:
        quote = _quote(text)
        answer["answers"] = [
            {
                "id": criterion,
                "answer": "yes" if criterion in matched else "no",
                "quote": quote if criterion in matched else None,
            }
            for criterion in ids
        ]
    return answer


def _quote(text: str) -> str | None:
    """A short piece of the text it was sent, so the quote check has something real."""
    sentence = text.strip().split(". ")[0].strip()
    return sentence[:120] or None


def _brief_plan(text: str) -> dict[str, Any]:
    question = str(_json(text).get("question") or text)[:200]
    words = _words(question, 3) or ["the response"]
    return {
        "decision": question,
        "options": [
            {"name": "Keep the current approach", "description": "Continue as now."},
            {"name": "Change the approach", "description": f"Change how {words[0]} is done."},
        ],
        "criteria": ["effectiveness", "do no harm"],
        "sub_questions": words,
    }


def _brief(schema: Mapping[str, Any], text: str) -> dict[str, Any]:
    payload = _json(text)
    passages: Sequence[Any] = payload.get("passages") or []
    tags = [str(p["tag"]) for p in passages if isinstance(p, dict) and "tag" in p][:2]
    options: Sequence[Any] = payload.get("options") or []
    names = [str(o["name"]) for o in options if isinstance(o, dict) and "name" in o][:4]
    names = names if len(names) >= 2 else ["Keep the current approach", "Change the approach"]
    criteria = [str(c) for c in payload.get("criteria") or []][:9]
    if "do no harm" not in [c.casefold() for c in criteria]:
        criteria.append("do no harm")

    def evidence(text: str) -> dict[str, Any]:
        if not tags:
            return reasoning(text)
        return {"text": text, "support": "evidence", "citations": tags[:1]}

    def reasoning(text: str) -> dict[str, Any]:
        return {"text": text, "support": "hypothesis", "citations": []}

    return {
        "bottom_line": [
            evidence("Demo text: the included papers say something about each option."),
            reasoning("Demo text: a real model would weigh them here."),
        ],
        "options": [
            {
                "name": name,
                "description": "Demo text describing this option.",
                "strength": "moderate" if tags else "insufficient",
                "benefits": [evidence(f"Demo benefit of {name}.")],
                "risks": [reasoning(f"Demo risk of {name}.")],
            }
            for name in names
        ],
        "trade_offs": [
            {
                "criterion": criterion,
                "assessments": [
                    {"option": name, "claim": reasoning(f"Demo view of {name}.")}
                    for name in names
                ],
            }
            for criterion in criteria
        ],
        "conflicts": [],
        "considerations": [],
        "gaps": ["Demo text: what a real brief would say is missing."],
    }


def _review() -> dict[str, Any]:
    return {
        "verdict": "accept",
        "scores": dict.fromkeys(
            (
                "coverage",
                "groundedness",
                "disagreements",
                "uncertainty",
                "humanitarian_awareness",
                "specificity",
            ),
            1,
        ),
        "issues": [],
        "queries": [],
    }


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _minimal(schema: Mapping[str, Any]) -> Any:
    """The smallest value matching `schema`, for answers the demo has no special case for."""
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return _minimal(options[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        properties: Mapping[str, Any] = schema.get("properties", {})
        return {name: _minimal(properties.get(name, {})) for name in schema.get("required", [])}
    if kind == "array":
        return [_minimal(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        text = "Demo text."
        minimum, maximum = schema.get("minLength", 0), schema.get("maxLength", len(text))
        return (text * (minimum // len(text) + 1))[: max(minimum, min(len(text), maximum))]
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    return None
