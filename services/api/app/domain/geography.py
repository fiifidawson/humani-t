"""Countries and large regions: place names too coarse to identify a person.

An operator may keep these unmasked in questions and plans, so a review's
geography reaches the search and the screening criteria (ADR 0011). Only names
on these lists qualify; a town, district, camp, facility or anything else the
name model reports stays masked. Built in, never looked up: no network, and the
same answer on every machine.

What does not belong here: deciding whether names are kept (Settings) or how a
span is masked (`domain/masking.py`).
"""

from __future__ import annotations

import re

# The 193 UN member states and the two observer states, by their common English
# short names (un.org/en/about-us/member-states, checked 2026-11-08).
_COUNTRIES = (
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
    "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia",
    "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso",
    "Burundi", "Cabo Verde", "Cambodia", "Cameroon", "Canada", "Central African Republic",
    "Chad", "Chile", "China", "Colombia", "Comoros", "Congo", "Costa Rica", "Côte d'Ivoire",
    "Croatia", "Cuba", "Cyprus", "Czechia", "Democratic Republic of the Congo", "Denmark",
    "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
    "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji", "Finland",
    "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Grenada",
    "Guatemala", "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras", "Hungary",
    "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica",
    "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos",
    "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
    "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova",
    "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia",
    "Nauru", "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria",
    "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Panama",
    "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
    "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe",
    "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
    "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea",
    "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland",
    "Syria", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga",
    "Trinidad and Tobago", "Tunisia", "Türkiye", "Turkmenistan", "Tuvalu", "Uganda",
    "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay",
    "Uzbekistan", "Vanuatu", "Venezuela", "Viet Nam", "Yemen", "Zambia", "Zimbabwe",
    "Holy See", "Palestine",
)  # fmt: skip

# Other names the same countries go by in papers and questions.
_ALSO_KNOWN_AS = (
    "Cape Verde", "Ivory Coast", "DRC", "DR Congo", "Republic of the Congo", "Czech Republic",
    "Swaziland", "Burma", "East Timor", "Turkey", "Vietnam", "UK", "Great Britain", "USA",
    "United States of America", "Republic of Korea", "Democratic People's Republic of Korea",
    "Lao PDR", "Syrian Arab Republic", "Russian Federation", "Iran (Islamic Republic of)",
    "State of Palestine", "Occupied Palestinian Territory", "Kosovo",
)  # fmt: skip

# Regions spanning several countries: continents, UN geoscheme regions, and the
# regions humanitarian work names. Each is too large to point at anyone.
_REGIONS = (
    "Africa", "Asia", "Europe", "Oceania", "Americas", "North America", "South America",
    "Latin America", "Latin America and the Caribbean", "Central America", "Caribbean",
    "North Africa", "Northern Africa", "West Africa", "Western Africa", "East Africa",
    "Eastern Africa", "Central Africa", "Middle Africa", "Southern Africa",
    "Sub-Saharan Africa", "Sahel", "Lake Chad Basin", "Horn of Africa", "Maghreb",
    "Middle East", "Middle East and North Africa", "MENA", "Levant", "Arab States",
    "Central Asia", "South Asia", "Southern Asia", "East Asia", "Eastern Asia",
    "Southeast Asia", "South-East Asia", "South-eastern Asia", "Western Asia",
    "Eastern Europe", "Western Europe", "Northern Europe", "Southern Europe", "Balkans",
    "Western Balkans", "Caucasus", "South Caucasus", "Pacific", "Pacific Islands",
    "Melanesia", "Polynesia",
)  # fmt: skip

# Right single quotation mark, as in Côte d'Ivoire typed on a phone.
_CURLY_APOSTROPHE = chr(0x2019)
_SPACES = re.compile(r"\s+")
_LEADING_THE = re.compile(r"^the\s+")


def _key(name: str) -> str:
    """The comparison form: case-folded, spaces collapsed, no leading "the" or full stop."""
    folded = (
        _SPACES.sub(" ", name.replace(_CURLY_APOSTROPHE, "'"))
        .strip()
        .rstrip(".")
        .strip()
        .casefold()
    )
    return _LEADING_THE.sub("", folded)


_KNOWN = frozenset(_key(name) for name in (*_COUNTRIES, *_ALSO_KNOWN_AS, *_REGIONS))


def is_country_or_region(name: str) -> bool:
    """Whether `name`, exactly, is a country or a large region on the built-in lists.

    A longer name that merely contains one ("Mali Health Centre", "Chad Smith")
    is not: only the whole name counts.
    """
    return bool(name.strip()) and _key(name) in _KNOWN
