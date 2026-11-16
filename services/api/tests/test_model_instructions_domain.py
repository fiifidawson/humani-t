"""Requirements for a project's instructions to the model (ADR 0015).

Two texts, one for judging papers and one for writing. They are added after the built-in
prompt under a fixed heading, never in place of it, and blank means none.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.model_instructions import (
    INSTRUCTIONS_HEADING,
    MAX_INSTRUCTIONS_LENGTH,
    InstructionsKind,
    ModelInstructions,
    ModelInstructionsVersion,
    with_instructions,
)

SYSTEM = "You screen research papers.\nAnswer with JSON only."
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


class TestModelInstructions:
    def test_blank_text_means_no_instructions(self):
        instructions = ModelInstructions(screening="  \n ", writing="")

        assert instructions.screening == ""
        assert instructions.writing == ""
        assert instructions.is_empty

    def test_text_is_kept_without_its_surrounding_space(self):
        instructions = ModelInstructions(screening="  Treat camps as displaced.\n", writing="")

        assert instructions.screening == "Treat camps as displaced."
        assert not instructions.is_empty

    def test_each_text_may_hold_up_to_the_limit(self):
        text = "x" * MAX_INSTRUCTIONS_LENGTH

        assert ModelInstructions(screening=text, writing=text).writing == text

    @pytest.mark.parametrize("field", ["screening", "writing"])
    def test_a_text_over_the_limit_is_refused_naming_its_field(self, field):
        texts = {"screening": "", "writing": ""} | {field: "x" * (MAX_INSTRUCTIONS_LENGTH + 1)}

        with pytest.raises(ValueError, match=rf"ModelInstructions\.{field}"):
            ModelInstructions(**texts)

    def test_line_breaks_and_tabs_are_kept(self):
        text = "First rule.\n\tSecond rule.\r\nThird rule."

        assert ModelInstructions(screening=text, writing="").screening == text

    @pytest.mark.parametrize("hidden", [chr(0x07), chr(0x202E), chr(0x2066), chr(0x2028)])
    def test_characters_that_hide_or_reorder_text_are_refused(self, hidden):
        with pytest.raises(ValueError, match=r"ModelInstructions\.writing"):
            ModelInstructions(screening="", writing=f"Write plainly{hidden}.")

    def test_each_kind_reads_its_own_text(self):
        instructions = ModelInstructions(screening="Judge this way.", writing="Write this way.")

        assert instructions.text_for(InstructionsKind.SCREENING) == "Judge this way."
        assert instructions.text_for(InstructionsKind.WRITING) == "Write this way."


class TestWithInstructions:
    def test_without_instructions_the_prompt_is_unchanged(self):
        assert with_instructions(SYSTEM, "") == SYSTEM

    def test_instructions_follow_the_built_in_prompt_under_the_fixed_heading(self):
        prompt = with_instructions(SYSTEM, "Treat camps as displaced settings.")

        assert prompt.startswith(SYSTEM)
        assert prompt.endswith("Treat camps as displaced settings.")
        assert prompt.index(INSTRUCTIONS_HEADING) > len(SYSTEM)

    def test_the_heading_says_the_built_in_rules_come_first(self):
        assert "unless they conflict with the rules above" in INSTRUCTIONS_HEADING
        assert "output format" in INSTRUCTIONS_HEADING


class TestModelInstructionsVersion:
    def _version(self) -> ModelInstructionsVersion:
        return ModelInstructionsVersion(
            project_id=uuid.uuid4(),
            version=1,
            instructions=ModelInstructions(screening="Judge.", writing=""),
            created_by=uuid.uuid4(),
            created_at=NOW,
        )

    def test_versions_count_from_one(self):
        with pytest.raises(ValueError, match=r"ModelInstructionsVersion\.version"):
            replace(self._version(), version=0)

    def test_the_time_saved_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match=r"ModelInstructionsVersion\.created_at"):
            replace(self._version(), created_at=datetime(2026, 11, 13, 9, 0))  # noqa: DTZ001
