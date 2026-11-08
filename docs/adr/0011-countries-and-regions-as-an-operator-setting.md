# ADR-0011: Country and large-region names in questions may be kept, as an operator setting

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Questions, clarifying answers and change requests are masked before any model sees them ([`architecture.md` §3.9.1](../architecture.md)). The local name model (`en_core_web_sm`) reports places, and every reported place is masked. In the first real run, the question "…displaced households in the Sahel?" reached the planner as "…in the [PERSON_1]?": the model labelled the Sahel a person, as it does Niger. Even when a place is labelled correctly, masking it removes the review's geography. The plan said "the region associated with [PERSON_1]", the search sent to the paper sources had no geography at all, and screening was asked about papers from Russia and Sri Lanka against a criterion it could not read.

A country or a region the size of the Sahel does not identify a person. Small areas do: towns, districts, camps, facilities. Data-protection practice draws the line there. HIPAA's Safe Harbor, for example, treats state-level geography as non-identifying.

## Decision

- `mask_countries_and_regions` (default `true`) is the operator's choice. While it is true, masking is unchanged.
- When it is `false`, a name the detectors report is kept only if the whole name is on built-in lists (`domain/geography.py`): the 193 UN member states, the two observer states, their common other names, and regions spanning several countries (continents, UN geoscheme regions, the Sahel, Lake Chad Basin, Horn of Africa, Middle East…). It is kept whatever label the detector gave it.
- Everything else stays masked, including a longer name containing a listed one ("Mali Health Centre", "Chad Smith"), and anything not on the lists, such as "Gaza", "Bamako" or "Cox's Bazar".
- A project's "always mask" rule still masks a listed name. Uploaded documents are not affected: they keep the broad list. Papers are not affected either; their list never included places.
- The lists are built in. They are never fetched, and give the same answer on every machine.

## Rejected

- **Keeping every place the detector reports:** sub-national places do identify people, and the detector cannot tell a district from a country.
- **Turning it on by default:** the owner chose an operator setting, off by default, so no deployment changes what it sends without deciding to.
- **Fixing the labels with a larger name model:** it would label better, but a correctly labelled place is still masked. The geography would still be lost.

## Consequences

- With it on, the planner and the search see the countries and regions in the question, and the privacy receipt shows them in "what was sent".
- In some contexts a country is itself sensitive, for example a small diaspora or a single named organisation's country of operation. A project rule masks it there.
- Proven by `tests/test_geography_domain.py` and `tests/test_privacy_sanitizer.py::TestCountriesAndRegions`.
