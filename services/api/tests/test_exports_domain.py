"""The rules every download keeps, as plain functions.

Spreadsheet programs run a CSV cell starting with = + - @, a tab or a carriage return as a
formula (OWASP "CSV Injection"); `Content-Disposition` follows RFC 6266.
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.exports import (
    ExportName,
    content_disposition,
    download_filename,
    neutralise_cell,
)


class TestCsvInjection:
    @pytest.mark.parametrize(
        "value",
        [
            '=HYPERLINK("http://evil.example","click")',
            "+1+1",
            "-2+3",
            "@SUM(A1:A2)",
            "\tTabbed",
            "\rReturn",
        ],
    )
    def test_a_cell_that_a_spreadsheet_would_run_as_a_formula_is_neutralised(self, value: str):
        cell = neutralise_cell(value)

        assert cell == "'" + value
        assert cell[0] not in "=+-@\t\r"

    @pytest.mark.parametrize("value", ["Cash transfers", "", "0.8", "10.1016/x", "P12"])
    def test_ordinary_text_is_left_alone(self, value: str):
        assert neutralise_cell(value) == value


class TestFilenames:
    def test_a_download_name_says_what_and_which_run_in_safe_characters(self):
        run_id = uuid.UUID("0f1e2d3c-0000-4000-8000-000000000000")

        name = download_filename(ExportName.DATASET_CSV, run_id)

        assert name == "Humani-T-run-0f1e2d3c-dataset.csv"

    def test_the_header_is_an_attachment_with_a_quoted_ascii_name(self):
        header = content_disposition("Humani-T-run-0f1e2d3c-brief.md")

        assert header == 'attachment; filename="Humani-T-run-0f1e2d3c-brief.md"'

    @pytest.mark.parametrize(
        "name", ['a".csv', "a\r\nSet-Cookie: x", "../../etc/passwd", "é.csv"]
    )
    def test_a_name_that_could_break_the_header_or_escape_a_folder_is_refused(self, name: str):
        with pytest.raises(ValueError, match="filename"):
            content_disposition(name)


class TestTheList:
    def test_the_downloads_the_spec_names_are_all_offered(self):
        names = {e.value for e in ExportName}

        assert names >= {
            "dataset.csv",
            "dataset.jsonl",
            "prisma.md",
            "brief.md",
            "brief.json",
            "brief.html",
            "included.ris",
            "manifest.json",
        }

    @pytest.mark.parametrize(
        ("name", "media"),
        [
            (ExportName.DATASET_CSV, "text/csv; charset=utf-8"),
            (ExportName.INCLUDED_RIS, "application/x-research-info-systems"),
            (ExportName.BRIEF_HTML, "text/html; charset=utf-8"),
            (ExportName.MANIFEST_JSON, "application/json"),
        ],
    )
    def test_each_download_has_its_media_type(self, name: ExportName, media: str):
        assert name.media_type == media

    def test_an_unknown_name_is_not_an_export(self):
        assert ExportName.parse("passwords.txt") is None
        assert ExportName.parse("dataset.csv") is ExportName.DATASET_CSV
