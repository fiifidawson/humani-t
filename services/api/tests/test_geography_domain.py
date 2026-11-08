"""Countries and large regions: names too coarse to identify anyone.

Requirement: an operator may keep these names in questions and plans, so a
review's geography reaches the search and its criteria (ADR 0011). Anything
smaller (a town, district, camp or facility) is never on the list.
"""

from __future__ import annotations

import pytest

from app.domain.geography import is_country_or_region


class TestCountriesAndRegions:
    @pytest.mark.parametrize(
        "name",
        [
            "Mali",
            "Niger",
            "Chad",
            "Burkina Faso",
            "South Sudan",
            "Democratic Republic of the Congo",
            "Côte d'Ivoire",
            "Ivory Coast",
            "Syria",
            "Yemen",
            "Bangladesh",
            "Myanmar",
            "Afghanistan",
            "Haiti",
            "Ukraine",
            "Palestine",
        ],
    )
    def test_a_country_is_recognised(self, name):
        assert is_country_or_region(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Sahel",
            "the Sahel",
            "Lake Chad Basin",
            "Horn of Africa",
            "the Horn of Africa",
            "West Africa",
            "Sub-Saharan Africa",
            "East Africa",
            "Middle East",
            "Southeast Asia",
            "Latin America",
        ],
    )
    def test_a_large_region_is_recognised(self, name):
        assert is_country_or_region(name)

    @pytest.mark.parametrize("name", ["SAHEL", "the  sahel", "mali.", "  Niger  "])
    def test_case_spacing_and_a_trailing_full_stop_do_not_matter(self, name):
        assert is_country_or_region(name)

    def test_a_curly_apostrophe_counts_as_a_straight_one(self):
        assert is_country_or_region("Côte d" + chr(0x2019) + "Ivoire")

    @pytest.mark.parametrize(
        "name",
        [
            "Kakuma",
            "Mbera camp",
            "Hodan district",
            "Gao",
            "Bamako",
            "Cox's Bazar",
            "Gaza",
            "Marib",
            "Chad Smith",
            "Mali Health Centre",
            "",
        ],
    )
    def test_towns_districts_camps_facilities_and_people_are_not(self, name):
        assert not is_country_or_region(name)
