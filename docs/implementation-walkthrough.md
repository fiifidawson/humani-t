# Implementation Walkthrough — Sessions 1 and 2

Every change, in the order a person would make them. Each step says **why**, gives the **file** or **command**, and ends with something you can **check**.

Work through it top to bottom. Do not skip the checks — they are how you find out that step 7 broke before you have written step 12.

Time: about three hours if you type it, half an hour if you paste it.

---

## Before you start

You need: `uv` installed, Python 3.13 pinned, and `services/api/pyproject.toml` already holding `fastapi` and `uvicorn[standard]`. Confirm:

```bash
cd services/api
uv run python -c "import fastapi; print(fastapi.__version__)"
```

Make a branch:

```bash
git switch -c feat/api-foundation v1
```

---

# Session 1 — make it breathe

## Step 1 — mark `app/` as a package

**Why.** A directory only becomes an importable Python package when it holds an `__init__.py`. Without it, `app.main` does not resolve and `uvicorn` cannot find your application. The file stays empty — its existence is the whole point.

```bash
cd services/api
touch app/__init__.py        # PowerShell: New-Item -ItemType File app/__init__.py
```

## Step 2 — the health endpoint

**Why.** The smallest thing that proves the process runs and can answer HTTP. It deliberately touches no database and no provider: it answers *is this process up*, not *is the whole system healthy*. Conflating those makes deploys hard to debug, because a failing dependency then looks like a dead process.

**File:** `services/api/app/main.py`

```python
"""Composition root for the humani-t API.

Assembles routers and nothing else. The decision about *which* implementations
are used lives in `app/api/dependencies.py`; this module only wires URLs.
"""

from fastapi import APIRouter, FastAPI

from app.api.dependencies import API_PREFIX
from app.api.routes import search, system, vocabulary
from app.api.routes.system import API_NAME, API_VERSION

TAGS_METADATA = [
    {"name": "system", "description": "Liveness and what is wired into this process."},
    {
        "name": "vocabulary",
        "description": (
            "Turning curated terms into a Boolean query. Concepts sharing a theme "
            "are OR'd; themes are AND'd. A user-supplied vocabulary is used "
            "exactly as given."
        ),
    },
    {
        "name": "search",
        "description": (
            "Running a compiled query against a source. Results always travel "
            "with the query and provenance that produced them."
        ),
    },
]

app = FastAPI(
    title=API_NAME,
    version=API_VERSION,
    description=(
        "Privacy-preserving evidence synthesis for humanitarian decisions.\n\n"
        "**Status: walking skeleton.** Search is backed by an in-memory corpus, "
        "so nothing here reaches the network. Swapping in a real repository is a "
        "change to `app/api/dependencies.py` alone — the endpoints and payloads "
        "below stay exactly as they are."
    ),
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)

v1 = APIRouter(prefix=API_PREFIX)
v1.include_router(system.router)
v1.include_router(vocabulary.router)
v1.include_router(search.router)

app.include_router(v1)
```

## Step 3 — run it

```bash
uv run uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

Reading that command: `app.main` is the module, the `:` separates it from `app` — the `FastAPI()` object inside. `--reload` watches the directory and restarts on save. `--no-proxy-headers` stops uvicorn rewriting the client address from `X-Forwarded-For`; the API decides that itself ([05](walkthroughs/05-accounts-and-sessions.md) D5.6).

> **Port note.** 8000 is the obvious choice and it is what most tutorials use. If you are also running `mkdocs serve` for `docs-site/`, it already holds 8000 — and on Windows both processes can bind it without either reporting an error, so uvicorn will claim to be running while your browser shows the docs site. Using 8731 avoids the collision entirely.

**Check.** Open <http://127.0.0.1:8731/docs>. You should see a Swagger page with one endpoint. Click it, Try it out, Execute:

```bash
curl http://127.0.0.1:8731/api/v1/health
# {"status":"ok"}
```

Session 1 is done. Leave the server running; it reloads as you go.

> `main.py` grows a second endpoint in Step 17, and that is the moment it gets split into routers. Doing it now, with one route, would be ceremony.

---

# Session 2 — the shape, with the world switched off

## Step 4 — install what the tests need

**Why.** Two dev dependencies, both needed before the first test runs. `pytest-asyncio` lets pytest await an `async def` test — the provider interface is async, so without it every search test is skipped silently. `httpx2` is what FastAPI's `TestClient` runs on; with plain `httpx` it still works but emits a deprecation warning on every run.

```bash
uv add --dev pytest-asyncio httpx2
```

## Step 5 — configure pytest and ruff

**Why.** Three of these settings are load-bearing:

- `pythonpath = ["."]` puts `services/api/` on `sys.path`, so `from app.domain` resolves. Without it every test fails at import.
- `asyncio_mode = "auto"` means async tests need no `@pytest.mark.asyncio`.
- `--strict-markers` turns a typo'd marker into an error instead of a silent skip.

**Append to `services/api/pyproject.toml`:**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-q --strict-markers"

[tool.ruff]
line-length = 96
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
```

## Step 6 — declare what the domain may not do

**Why.** This docstring is a boundary you will be tempted to cross. Write it first, while the temptation is theoretical.

**File:** `services/api/app/domain/__init__.py`

```python
"""Domain entities and rules.

Nothing in this package may import a database, an HTTP client, an LLM SDK, or
FastAPI. These types are plain Python and must stay that way — they are the only
part of the system that can be tested without a database or a network.

Note the absence of Pydantic. Pydantic models belong at the API boundary, where
untrusted JSON arrives. Down here, a Concept is already valid or it was never
constructed.
"""
```

## Step 7 — `Vocabulary` and `Concept`

**Why.** The vocabulary is the most valuable artefact in the system — it is what makes a search reproducible and the thing reviewers argue about.

Three decisions are encoded here:

- **Immutability.** `revise()` returns version N+1; the original is untouched. Results reference the version that produced them, so in-place edits would destroy reproducibility silently.
- **`origin` is stored, never inferred.** A reviewer must be able to tell whether a term came from a model or a person.
- **`terms` puts the canonical spelling first** and drops case-insensitive duplicates, so a compiled query reads the way the author wrote it.

**File:** `services/api/app/domain/vocabulary.py`

```python
"""The curated term list a review searches with.

A Vocabulary is the most valuable artefact in the system: it is what makes a
search reproducible, and it is the thing a reviewer argues about. Everything
downstream inherits its provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

DEFAULT_THEME = "general"


class VocabularyOrigin(StrEnum):
    """Where the terms came from.

    Recorded and surfaced, never inferred. A reviewer must be able to tell
    whether a term was proposed by a model or written by a person.
    """

    LLM_ASSISTED = "llm_assisted"
    USER_SUPPLIED = "user_supplied"


def _dedupe_preserving_order(values: tuple[str, ...]) -> tuple[str, ...]:
    """Drop blanks and case-insensitive duplicates, keeping first spelling."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            kept.append(cleaned)
    return tuple(kept)


@dataclass(frozen=True, slots=True)
class Concept:
    """One research concept and the words that stand in for it.

    `term` is the canonical spelling. `synonyms` are alternatives that should
    match the same idea. Together they become one OR-group in a query.
    """

    term: str
    synonyms: tuple[str, ...] = ()
    theme: str = DEFAULT_THEME

    def __post_init__(self) -> None:
        if not self.term.strip():
            raise ValueError("Concept.term must not be blank")
        if not self.theme.strip():
            raise ValueError("Concept.theme must not be blank")

    @property
    def terms(self) -> tuple[str, ...]:
        """Canonical term first, then synonyms, de-duplicated.

        Order matters: it is what a compiled query will show, and a reviewer
        reading the query should see the term they chose at the front.
        """
        return _dedupe_preserving_order((self.term, *self.synonyms))


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """A versioned, immutable set of Concepts.

    Immutable on purpose. Editing produces a new version rather than mutating
    this one, because search results reference the version they came from. Allow
    in-place edits and reproducibility is gone — silently.
    """

    concepts: tuple[Concept, ...]
    origin: VocabularyOrigin
    version: int = 1
    frozen_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.concepts:
            raise ValueError("Vocabulary must contain at least one Concept")
        if self.version < 1:
            raise ValueError("Vocabulary.version starts at 1")

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None

    @property
    def is_strict(self) -> bool:
        """True when the user supplied these terms themselves.

        A strict Vocabulary must reach the search providers exactly as given:
        no model-driven expansion, no synonym enrichment. See architecture.md
        section 4.1 — this is a correctness property, not a preference.
        """
        return self.origin is VocabularyOrigin.USER_SUPPLIED

    @property
    def themes(self) -> tuple[str, ...]:
        """Distinct themes, in first-appearance order."""
        return _dedupe_preserving_order(tuple(c.theme for c in self.concepts))

    def concepts_in(self, theme: str) -> tuple[Concept, ...]:
        key = theme.strip().casefold()
        return tuple(c for c in self.concepts if c.theme.strip().casefold() == key)

    def freeze(self, *, at: datetime | None = None) -> Vocabulary:
        """Return a frozen copy, marking the moment the user confirmed it."""
        if self.is_frozen:
            return self
        return replace(self, frozen_at=at or datetime.now(UTC))

    def revise(self, concepts: tuple[Concept, ...]) -> Vocabulary:
        """Return the next version. The current one is left untouched."""
        return Vocabulary(
            concepts=concepts,
            origin=self.origin,
            version=self.version + 1,
            frozen_at=None,
        )
```

## Step 8 — `BooleanQuery` and the compiler

**Why.** A `BooleanQuery` is **not a string**. Four repositories have four syntaxes, four escaping rules and four sets of field names. Keeping the structure is what lets one search run against all of them and stay reproducible.

The shape follows systematic-review practice:

```
each Concept        ->  (term OR synonym OR synonym)
concepts in a theme ->  OR'd together
themes              ->  AND'd together
```

`compile_vocabulary` is pure and total: same vocabulary, same query, no model consulted. That is what makes a user-supplied vocabulary strict **by construction** rather than by everyone remembering to be careful.

**File:** `services/api/app/domain/query.py`

```python
"""Source-agnostic Boolean queries.

A BooleanQuery is NOT a string. Each repository (arXiv, OpenAlex, Europe PMC,
Google Scholar) has its own syntax, escaping rules and field names; a provider
compiles this structure into its own dialect. Keeping the structure means one
search is reproducible across four sources that agree on nothing.

Shape, following systematic-review practice:

    each Concept        ->  (term OR synonym OR synonym)
    concepts in a theme ->  OR'd together
    themes              ->  AND'd together

    ("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis")
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin


@dataclass(frozen=True, slots=True)
class TermGroup:
    """The terms of a single Concept, OR'd together."""

    concept_term: str
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("TermGroup needs at least one term")


@dataclass(frozen=True, slots=True)
class ThemeGroup:
    """Every Concept in one theme. Their groups are OR'd together."""

    theme: str
    groups: tuple[TermGroup, ...]

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("ThemeGroup needs at least one TermGroup")

    @property
    def terms(self) -> tuple[str, ...]:
        """Every term in the theme, flattened."""
        return tuple(term for group in self.groups for term in group.terms)


@dataclass(frozen=True, slots=True)
class BooleanQuery:
    """Themes AND'd together, plus the provenance of the terms.

    `vocabulary_version` and `vocabulary_origin` travel with the query rather
    than being looked up later. A result set must be able to answer 'which terms
    produced me' without a database round trip, because it may be read on a
    different machine.
    """

    themes: tuple[ThemeGroup, ...]
    vocabulary_version: int
    vocabulary_origin: VocabularyOrigin

    def __post_init__(self) -> None:
        if not self.themes:
            raise ValueError("BooleanQuery needs at least one ThemeGroup")

    @property
    def is_strict(self) -> bool:
        return self.vocabulary_origin is VocabularyOrigin.USER_SUPPLIED

    @property
    def all_terms(self) -> tuple[str, ...]:
        return tuple(term for theme in self.themes for term in theme.terms)

    def render(self) -> str:
        """Human-readable form, for logs, tests and the audit trail.

        NOT for sending to a repository. Providers compile their own dialect —
        if you find yourself passing this to an HTTP client, a seam has leaked.
        """
        clauses = []
        for theme in self.themes:
            terms = " OR ".join(f'"{t}"' for t in theme.terms)
            clauses.append(f"({terms})")
        return " AND ".join(clauses)


def _group_for(concept: Concept) -> TermGroup:
    return TermGroup(concept_term=concept.term, terms=concept.terms)


def compile_vocabulary(vocabulary: Vocabulary) -> BooleanQuery:
    """Turn a Vocabulary into a BooleanQuery.

    Pure and total: the same Vocabulary always compiles to the same query. No
    model is consulted here, for either origin — expansion, if it happens at
    all, happens before a Vocabulary exists. That is what makes a user-supplied
    vocabulary strict by construction rather than by discipline.
    """
    themes = tuple(
        ThemeGroup(
            theme=theme,
            groups=tuple(_group_for(c) for c in vocabulary.concepts_in(theme)),
        )
        for theme in vocabulary.themes
    )
    return BooleanQuery(
        themes=themes,
        vocabulary_version=vocabulary.version,
        vocabulary_origin=vocabulary.origin,
    )
```

## Step 9 — `PaperRecord` and identity

**Why.** The hard part is identity. Four sources will return the same paper with different ids, different capitalisation and sometimes a different year. If they do not collapse into one record, every count downstream is wrong.

DOI wins when present — it is the only globally unique identifier available. Otherwise normalised title plus year, which is imperfect: a preprint and its published version differ in year and will not merge. That is deliberate. Fuzzy matching would hide wrong merges, and a wrong merge is far harder to notice than a missed one.

**File:** `services/api/app/domain/paper.py`

```python
"""A paper as the system knows it, independent of which repository found it.

The hard part here is identity. Four sources will return the same paper with
different ids, different capitalisation and sometimes a different year, and they
must collapse into one record or every count downstream is wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


def normalise_doi(doi: str) -> str:
    """Strip resolver prefixes and case. DOIs are case-insensitive by spec."""
    cleaned = doi.strip().casefold()
    for prefix in _DOI_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned.strip("/")


def normalise_title(title: str) -> str:
    """Reduce a title to comparable form: lowercase, alphanumeric, single spaces."""
    return _NON_ALNUM.sub(" ", title.casefold()).strip()


@dataclass(frozen=True, slots=True)
class PaperRecord:
    """One paper, as reported by one source.

    `source_id` plus `external_id` says where it came from and is never used for
    equality — two sources describing the same paper are the same paper.
    """

    source_id: str
    external_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    is_open_access: bool = False
    abstract: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("PaperRecord.source_id must not be blank")
        if not self.external_id.strip():
            raise ValueError("PaperRecord.external_id must not be blank")
        if not self.title.strip():
            raise ValueError("PaperRecord.title must not be blank")

    @property
    def identity(self) -> str:
        """The key two records must share to be considered the same paper.

        DOI when there is one — it is the only globally unique identifier here.
        Otherwise normalised title plus year, which is imperfect: preprint and
        published versions differ in year and will not merge. That is a known
        limitation, and deliberately not hidden behind fuzzy matching, because a
        wrong merge is far harder to notice than a missed one.
        """
        if self.doi:
            return f"doi:{normalise_doi(self.doi)}"
        return f"title:{normalise_title(self.title)}|{self.year or ''}"

    @property
    def searchable_text(self) -> str:
        """Title and abstract, for matching against query terms."""
        return f"{self.title} {self.abstract or ''}".casefold()


def deduplicate(records: Iterable[PaperRecord]) -> tuple[PaperRecord, ...]:
    """Collapse records sharing an identity, keeping the first seen.

    Stable: input order decides which record wins, so callers control source
    precedence by the order they merge in. Field-level merging — taking the
    abstract from one source and the open-access flag from another — is a real
    improvement and a separate concern; it is not done here.
    """
    seen: dict[str, PaperRecord] = {}
    for record in records:
        seen.setdefault(record.identity, record)
    return tuple(seen.values())
```

## Step 10 — the search package

**File:** `services/api/app/search/__init__.py`

```python
"""Paper search: the PaperSearchProvider seam and its implementations.

One interface over arXiv, OpenAlex, Europe PMC and Google Scholar. Each has its
own query syntax, rate limits and metadata shape, and none of that is allowed
above this package.
"""
```

## Step 11 — the `PaperSearchProvider` Protocol

**Why.** The most important interface in the system: everything downstream inherits the provenance it establishes.

A `Protocol`, not an abstract base class. Implementations inherit from nothing, so a provider can be written, tested and swapped without importing this module at runtime — conformance is checked by shape. `fetch_fulltext` returning `None` is a normal outcome, not an error: much of the corpus is metadata-only and screening has to cope with abstracts alone.

**File:** `services/api/app/search/base.py`

```python
"""The PaperSearchProvider Protocol.

This is the most important interface in the system. Everything downstream —
screening, the decision brief, the audit trail — inherits the provenance it
establishes.

A Protocol rather than an ABC: implementations do not inherit from anything, so
a provider can be written, tested and swapped without importing this module at
runtime. Structural typing means conformance is checked by shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery

DEFAULT_LIMIT = 25


@runtime_checkable
class PaperSearchProvider(Protocol):
    """One academic repository, behind a uniform interface."""

    @property
    def source_id(self) -> str:
        """Stable short name, e.g. 'openalex'. Stored on every PaperRecord.

        Must not change once results referencing it have been persisted.
        """
        ...

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        """Run a compiled query and return what this source knows.

        The implementation compiles `query` into its own dialect. It must not
        add, expand or reinterpret terms — a strict vocabulary stays strict all
        the way down.
        """
        ...

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        """Return full text, or None when the document is not openly available.

        None is a normal outcome, not an error. Much of the corpus will be
        metadata-only, and screening has to work on abstracts alone.
        """
        ...
```

## Step 12 — the fake provider

**Why.** This exists to prove the interface is right before any real source is involved. **If the fake is awkward to write, the interface is wrong** — and finding that out here costs nothing.

It evaluates the query for real rather than returning a fixed list. The four-paper corpus is built so that AND-across-themes actually discriminates: `fake-003` matches *chatbot* only, `fake-004` matches *humanitarian* only, and a correct implementation must exclude both. A fake returning three hardcoded records would make every test pass without proving anything.

**File:** `services/api/app/search/fake.py`

```python
"""An in-memory PaperSearchProvider. No network, no key, no rate limit.

This exists to prove the interface is right before any real source is involved.
If a fake is awkward to write against an interface, the interface is wrong — and
finding that out here costs nothing.

It genuinely evaluates the query rather than returning a fixed list, so tests
exercise the compiled Boolean logic instead of trivially passing.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.search.base import DEFAULT_LIMIT

SOURCE_ID = "fake"

# A small corpus chosen so that AND across themes actually discriminates:
# some records match one theme only, and must not be returned.
DEFAULT_CORPUS: tuple[PaperRecord, ...] = (
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-001",
        title="Conversational agents for crisis response in humanitarian settings",
        year=2024,
        doi="10.1000/fake.001",
        is_open_access=True,
        abstract=(
            "We study chatbot deployment by humanitarian organisations during "
            "acute crisis response, with attention to transparency."
        ),
        url="https://example.org/fake-001",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-002",
        title="Interpretability of large language models in clinical triage",
        year=2023,
        doi="10.1000/fake.002",
        is_open_access=True,
        abstract=(
            "An evaluation of interpretability methods applied to a chatbot "
            "used for humanitarian triage in low-resource settings."
        ),
        url="https://example.org/fake-002",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-003",
        title="A survey of chatbot architectures",
        year=2022,
        doi=None,
        is_open_access=False,
        abstract="General architectures for conversational agents. No deployment context.",
        url="https://example.org/fake-003",
    ),
    PaperRecord(
        source_id=SOURCE_ID,
        external_id="fake-004",
        title="Humanitarian logistics under uncertainty",
        year=2021,
        doi="10.1000/fake.004",
        is_open_access=True,
        abstract="Supply chain optimisation for humanitarian response. No language models.",
        url="https://example.org/fake-004",
    ),
)

FULLTEXT: dict[str, str] = {
    "fake-001": "Full text of the crisis response study.",
    "fake-002": "Full text of the interpretability evaluation.",
    # fake-003 and fake-004 are deliberately absent: not every paper is open.
}


def _matches(record: PaperRecord, query: BooleanQuery) -> bool:
    """AND across themes, OR within a theme — the same shape as render()."""
    haystack = record.searchable_text
    return all(
        any(term.casefold() in haystack for term in theme.terms) for theme in query.themes
    )


class FakePaperSearchProvider:
    """Satisfies PaperSearchProvider structurally, without inheriting from it."""

    def __init__(self, corpus: Sequence[PaperRecord] | None = None) -> None:
        self._corpus = tuple(corpus) if corpus is not None else DEFAULT_CORPUS
        self.calls: list[BooleanQuery] = []

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def search(
        self, query: BooleanQuery, limit: int = DEFAULT_LIMIT
    ) -> Sequence[PaperRecord]:
        self.calls.append(query)
        hits = [record for record in self._corpus if _matches(record, query)]
        return tuple(hits[:limit])

    async def fetch_fulltext(self, record: PaperRecord) -> str | None:
        return FULLTEXT.get(record.external_id)
```

---

# Tests

## Step 13 — domain tests

**File:** `services/api/tests/test_domain.py`

The one to read closely is `test_strict_vocabulary_gains_no_terms`. It asserts the compiled term set equals the supplied term set **exactly**. When LLM expansion is added later, that test is what stops it reaching user-supplied terms. Strict mode as an executable property, not a comment.

```python
"""Domain rules: vocabulary immutability, query compilation, paper identity."""

from __future__ import annotations

import pytest

from app.domain.paper import PaperRecord, deduplicate, normalise_doi, normalise_title
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin


def make_vocabulary(origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED) -> Vocabulary:
    return Vocabulary(
        concepts=(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", ("crisis response",), theme="setting"),
        ),
        origin=origin,
    )


class TestConcept:
    def test_terms_put_canonical_first(self):
        concept = Concept("chatbot", ("conversational agent", "dialogue system"))
        assert concept.terms == ("chatbot", "conversational agent", "dialogue system")

    def test_terms_drop_case_insensitive_duplicates(self):
        concept = Concept("Chatbot", ("chatbot", "CHATBOT", "conversational agent"))
        assert concept.terms == ("Chatbot", "conversational agent")

    def test_terms_drop_blanks(self):
        assert Concept("chatbot", ("", "   ")).terms == ("chatbot",)

    def test_blank_term_is_rejected(self):
        with pytest.raises(ValueError, match="term must not be blank"):
            Concept("   ")


class TestVocabulary:
    def test_empty_vocabulary_is_rejected(self):
        with pytest.raises(ValueError, match="at least one Concept"):
            Vocabulary(concepts=(), origin=VocabularyOrigin.USER_SUPPLIED)

    def test_themes_are_in_first_appearance_order(self):
        assert make_vocabulary().themes == ("technology", "setting")

    def test_revise_leaves_the_original_untouched(self):
        original = make_vocabulary()
        revised = original.revise((Concept("triage"),))

        assert original.version == 1
        assert revised.version == 2
        assert len(original.concepts) == 2
        assert len(revised.concepts) == 1

    def test_freeze_records_the_moment_of_confirmation(self):
        frozen = make_vocabulary().freeze()
        assert frozen.is_frozen
        assert frozen.frozen_at is not None

    def test_freeze_is_idempotent(self):
        once = make_vocabulary().freeze()
        assert once.freeze() is once

    def test_revising_unfreezes_the_new_version(self):
        revised = make_vocabulary().freeze().revise((Concept("triage"),))
        assert not revised.is_frozen

    def test_user_supplied_vocabulary_is_strict(self):
        assert make_vocabulary(VocabularyOrigin.USER_SUPPLIED).is_strict
        assert not make_vocabulary(VocabularyOrigin.LLM_ASSISTED).is_strict


class TestCompilation:
    def test_renders_or_within_theme_and_and_across_themes(self):
        query = compile_vocabulary(make_vocabulary())
        assert query.render() == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")'
        )

    def test_provenance_travels_with_the_query(self):
        vocabulary = make_vocabulary(VocabularyOrigin.USER_SUPPLIED).revise(
            (Concept("chatbot"),)
        )
        query = compile_vocabulary(vocabulary)

        assert query.vocabulary_version == 2
        assert query.vocabulary_origin is VocabularyOrigin.USER_SUPPLIED
        assert query.is_strict

    def test_concepts_sharing_a_theme_are_or_ed_together(self):
        vocabulary = Vocabulary(
            concepts=(
                Concept("chatbot", theme="technology"),
                Concept("llm", ("language model",), theme="technology"),
            ),
            origin=VocabularyOrigin.LLM_ASSISTED,
        )
        query = compile_vocabulary(vocabulary)

        assert len(query.themes) == 1
        assert query.render() == '("chatbot" OR "llm" OR "language model")'

    def test_compilation_is_deterministic(self):
        vocabulary = make_vocabulary()
        assert compile_vocabulary(vocabulary) == compile_vocabulary(vocabulary)

    def test_strict_vocabulary_gains_no_terms(self):
        """The property that makes 'strict' mean strict.

        Compilation is pure: every term in the query came from the vocabulary,
        and nothing was added. If an expansion step is ever introduced upstream,
        this test is what catches it reaching user-supplied terms.
        """
        vocabulary = make_vocabulary(VocabularyOrigin.USER_SUPPLIED)
        supplied = {t.casefold() for c in vocabulary.concepts for t in c.terms}

        compiled = {t.casefold() for t in compile_vocabulary(vocabulary).all_terms}

        assert compiled == supplied


class TestPaperIdentity:
    def test_doi_wins_over_title(self):
        record = PaperRecord("openalex", "W1", "A title", year=2024, doi="10.1/x")
        assert record.identity == "doi:10.1/x"

    def test_doi_resolver_prefix_and_case_are_normalised(self):
        assert normalise_doi("https://doi.org/10.1/ABC") == "10.1/abc"
        assert normalise_doi("doi:10.1/abc") == "10.1/abc"

    def test_same_paper_from_two_sources_shares_identity(self):
        a = PaperRecord("openalex", "W1", "Chatbots In Crisis", 2024, "https://doi.org/10.1/X")
        b = PaperRecord("arxiv", "2401.1", "chatbots in crisis", 2024, "10.1/x")
        assert a.identity == b.identity

    def test_without_doi_identity_falls_back_to_title_and_year(self):
        a = PaperRecord("arxiv", "1", "Chatbots, in crisis!", 2024)
        b = PaperRecord("pmc", "2", "chatbots in crisis", 2024)
        assert a.identity == b.identity

    def test_same_title_different_year_does_not_merge(self):
        """Preprint and published versions stay separate. Known limitation."""
        a = PaperRecord("arxiv", "1", "Chatbots in crisis", 2023)
        b = PaperRecord("pmc", "2", "Chatbots in crisis", 2024)
        assert a.identity != b.identity

    def test_normalise_title_collapses_punctuation_and_case(self):
        assert normalise_title("Chatbots:  In-Crisis!") == "chatbots in crisis"

    def test_blank_title_is_rejected(self):
        with pytest.raises(ValueError, match="title must not be blank"):
            PaperRecord("openalex", "W1", "  ")


class TestDeduplicate:
    def test_first_record_wins_and_order_is_stable(self):
        first = PaperRecord("openalex", "W1", "Shared title", 2024, "10.1/x")
        second = PaperRecord("arxiv", "2401.1", "Shared title", 2024, "10.1/x")
        other = PaperRecord("pmc", "P1", "Different", 2024, "10.1/y")

        merged = deduplicate([first, second, other])

        assert merged == (first, other)
        assert merged[0].source_id == "openalex"

    def test_empty_input_gives_empty_output(self):
        assert deduplicate([]) == ()
```

## Step 14 — search tests

**File:** `services/api/tests/test_search.py`

`test_fake_satisfies_the_protocol_without_inheriting_it` is the one that proves the seam: `isinstance` passes against a Protocol the class never inherited from.

```python
"""The PaperSearchProvider seam, exercised through the fake."""

from __future__ import annotations

import pytest

from app.domain.paper import PaperRecord
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider


def query_for(*concepts: Concept, origin=VocabularyOrigin.LLM_ASSISTED):
    return compile_vocabulary(Vocabulary(concepts=concepts, origin=origin))


@pytest.fixture
def provider() -> FakePaperSearchProvider:
    return FakePaperSearchProvider()


class TestConformance:
    def test_fake_satisfies_the_protocol_without_inheriting_it(self, provider):
        """Structural typing: no base class, no registration, still conforms."""
        assert isinstance(provider, PaperSearchProvider)
        assert FakePaperSearchProvider.__mro__[1:] == (object,)

    def test_source_id_is_stable(self, provider):
        assert provider.source_id == "fake"


class TestSearch:
    async def test_and_across_themes_excludes_single_theme_matches(self, provider):
        """The discriminating case: 'chatbot' alone must not be enough."""
        query = query_for(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", ("crisis",), theme="setting"),
        )

        found = await provider.search(query)
        ids = {r.external_id for r in found}

        assert "fake-001" in ids  # chatbot + humanitarian
        assert "fake-002" in ids  # chatbot + humanitarian
        assert "fake-003" not in ids  # chatbot only
        assert "fake-004" not in ids  # humanitarian only

    async def test_or_within_a_theme_widens_the_result(self, provider):
        narrow = await provider.search(query_for(Concept("logistics")))
        wide = await provider.search(query_for(Concept("logistics", ("triage",))))

        assert len(wide) > len(narrow)

    async def test_synonym_alone_is_enough_to_match(self, provider):
        query = query_for(Concept("nonexistent term", ("crisis response",)))
        found = await provider.search(query)

        assert {r.external_id for r in found} == {"fake-001"}

    async def test_no_match_returns_empty_not_error(self, provider):
        assert await provider.search(query_for(Concept("quantum chromodynamics"))) == ()

    async def test_limit_is_respected(self, provider):
        query = query_for(Concept("chatbot", ("humanitarian",)))
        assert len(await provider.search(query, limit=1)) == 1

    async def test_every_record_is_stamped_with_the_source(self, provider):
        found = await provider.search(query_for(Concept("chatbot")))

        assert found
        assert all(r.source_id == provider.source_id for r in found)

    async def test_provider_receives_the_compiled_query_unmodified(self, provider):
        query = query_for(Concept("chatbot"), origin=VocabularyOrigin.USER_SUPPLIED)

        await provider.search(query)

        assert provider.calls == [query]
        assert provider.calls[0].is_strict


class TestFulltext:
    async def test_open_access_paper_returns_text(self, provider):
        found = await provider.search(query_for(Concept("crisis response")))
        assert await provider.fetch_fulltext(found[0]) is not None

    async def test_closed_paper_returns_none_rather_than_raising(self, provider):
        closed = PaperRecord("fake", "fake-003", "A survey of chatbot architectures")
        assert await provider.fetch_fulltext(closed) is None


class TestSubstitution:
    async def test_a_custom_corpus_swaps_in_without_touching_callers(self):
        """The seam working: the caller does not know which corpus it queried."""
        custom = FakePaperSearchProvider(
            corpus=(PaperRecord("fake", "x-1", "Ethics of triage algorithms", 2025),)
        )

        found = await custom.search(query_for(Concept("triage")))

        assert [r.external_id for r in found] == ["x-1"]
```

## Step 15 — health endpoint test

**File:** `services/api/tests/test_health.py`

`TestClient` calls the app in-process — no server, no port, no network.

```python
"""The health endpoint, through FastAPI's test client — no running server needed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_versioned(client):
    """Unversioned paths must not work: clients pin to /api/v1."""
    assert client.get("/health").status_code == 404


def test_openapi_schema_is_served(client):
    schema = client.get("/openapi.json").json()

    assert "/api/v1/health" in schema["paths"]
    assert schema["info"]["title"] == "humani-t API"
```

---

# Step 16 — run everything

```bash
cd services/api
uv run pytest
```

Expected:

```
........................................                    [100%]
40 passed in 0.52s
```

Then lint and format:

```bash
uv run ruff check .
uv run ruff format .
```

Expected: `All checks passed!`

---

# Session 2b — put the seam on the wire

One endpoint does not demo well, and a seam nobody can see is hard to argue about. These five steps expose the domain and the provider over HTTP — still backed by the fake, so nothing reaches the network.

## Step 17 — the API package boundary

**Why.** Pydantic lives here and nowhere deeper. Untrusted JSON is validated into these models, then converted into domain dataclasses that are valid by construction. Writing the boundary down is what stops the wire format leaking inward.

**File:** `services/api/app/api/__init__.py`

```python
"""The HTTP boundary.

This is the only layer that knows about Pydantic, status codes and JSON. Domain
objects are plain dataclasses; the schemas here translate between untrusted
request bodies and those dataclasses, and back again.

Keeping the translation explicit is what lets the same domain serve a different
transport without the wire format leaking inwards.
"""
```

## Step 18 — request and response schemas

**Why.** Each schema carries a `to_domain()` or `from_domain()` so translation happens in one place. `EXAMPLE_VOCABULARY` is attached via `json_schema_extra`, which makes Swagger prefill a body that actually works — the difference between a demo someone can click and one they have to be talked through.

**File:** `services/api/app/api/schemas.py`

```python
"""Request and response models for the HTTP boundary.

Pydantic lives here and nowhere deeper. Untrusted JSON arrives, is validated into
these models, and is then converted into domain dataclasses which are valid by
construction. The reverse trip turns domain objects back into JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.paper import PaperRecord
from app.domain.query import BooleanQuery
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin

EXAMPLE_VOCABULARY = {
    "origin": "llm_assisted",
    "concepts": [
        {
            "term": "chatbot",
            "synonyms": ["conversational agent", "dialogue system"],
            "theme": "technology",
        },
        {
            "term": "humanitarian",
            "synonyms": ["crisis response"],
            "theme": "setting",
        },
    ],
}


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class ConceptIn(BaseModel):
    """One research concept and the words that stand in for it."""

    term: str = Field(min_length=1, description="Canonical spelling.")
    synonyms: list[str] = Field(
        default_factory=list, description="Alternatives matching the same idea."
    )
    theme: str = Field(
        default="general",
        min_length=1,
        description="Concepts sharing a theme are OR'd; themes are AND'd.",
    )


class VocabularyIn(BaseModel):
    """A vocabulary as submitted over HTTP."""

    model_config = ConfigDict(json_schema_extra={"example": EXAMPLE_VOCABULARY})

    concepts: list[ConceptIn] = Field(min_length=1)
    origin: VocabularyOrigin = Field(
        default=VocabularyOrigin.LLM_ASSISTED,
        description=(
            "user_supplied marks the vocabulary STRICT: it is used exactly as "
            "given, with no model-driven expansion."
        ),
    )
    version: int = Field(default=1, ge=1)

    def to_domain(self) -> Vocabulary:
        """Convert to the domain type, which validates on construction."""
        return Vocabulary(
            concepts=tuple(
                Concept(term=c.term, synonyms=tuple(c.synonyms), theme=c.theme)
                for c in self.concepts
            ),
            origin=self.origin,
            version=self.version,
        )


class SearchIn(BaseModel):
    """A vocabulary plus how many results to return."""

    model_config = ConfigDict(
        json_schema_extra={"example": {**EXAMPLE_VOCABULARY, "limit": 25}}
    )

    concepts: list[ConceptIn] = Field(min_length=1)
    origin: VocabularyOrigin = VocabularyOrigin.LLM_ASSISTED
    version: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=200)

    def to_domain(self) -> Vocabulary:
        return VocabularyIn(
            concepts=self.concepts, origin=self.origin, version=self.version
        ).to_domain()


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class TermGroupOut(BaseModel):
    concept: str
    terms: list[str]


class ThemeGroupOut(BaseModel):
    theme: str
    groups: list[TermGroupOut]


class ProvenanceOut(BaseModel):
    """Where the terms came from. Travels with every result set."""

    vocabulary_version: int
    vocabulary_origin: VocabularyOrigin
    is_strict: bool


class CompiledQueryOut(BaseModel):
    """A compiled Boolean query, in both structured and readable form."""

    rendered: str = Field(description="Human-readable. NOT what is sent to a source.")
    themes: list[ThemeGroupOut]
    term_count: int
    provenance: ProvenanceOut

    @classmethod
    def from_domain(cls, query: BooleanQuery) -> CompiledQueryOut:
        return cls(
            rendered=query.render(),
            themes=[
                ThemeGroupOut(
                    theme=theme.theme,
                    groups=[
                        TermGroupOut(concept=g.concept_term, terms=list(g.terms))
                        for g in theme.groups
                    ],
                )
                for theme in query.themes
            ],
            term_count=len(query.all_terms),
            provenance=ProvenanceOut(
                vocabulary_version=query.vocabulary_version,
                vocabulary_origin=query.vocabulary_origin,
                is_strict=query.is_strict,
            ),
        )


class PaperOut(BaseModel):
    source_id: str
    external_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    is_open_access: bool = False
    abstract: str | None = None
    url: str | None = None
    identity: str = Field(description="Dedup key: DOI, else normalised title + year.")

    @classmethod
    def from_domain(cls, record: PaperRecord) -> PaperOut:
        return cls(
            source_id=record.source_id,
            external_id=record.external_id,
            title=record.title,
            year=record.year,
            doi=record.doi,
            is_open_access=record.is_open_access,
            abstract=record.abstract,
            url=record.url,
            identity=record.identity,
        )


class SearchOut(BaseModel):
    """Results, plus the query and provenance that produced them."""

    source_id: str
    query: CompiledQueryOut
    total: int
    results: list[PaperOut]


class SourceOut(BaseModel):
    source_id: str
    live: bool = Field(description="False for in-memory sources that make no requests.")
    description: str


class HealthOut(BaseModel):
    status: str = "ok"


class InfoOut(BaseModel):
    """What this process is and which implementations are wired into it."""

    name: str
    version: str
    api_prefix: str
    search_provider: str
    makes_network_calls: bool
```

## Step 19 — the application service

**Why.** Routes should not orchestrate. `SearchService` compiles the vocabulary, calls the provider and binds the results to the query that produced them. It depends on the Protocol, so it cannot tell an in-memory corpus from OpenAlex.

The `deduplicate` call is near-pointless with one source. It is there because the moment a second provider is added this becomes the merge point, and a missing dedup step is invisible until the counts are already wrong.

**Files:** `services/api/app/application/__init__.py` and `search_service.py`

```python
"""Use cases.

Orchestrates domain objects and provider interfaces. Knows nothing about HTTP,
and nothing about which concrete provider it was handed.
"""
```

```python
"""Running a search: compile the vocabulary, ask a provider, keep the provenance."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.paper import PaperRecord, deduplicate
from app.domain.query import BooleanQuery, compile_vocabulary
from app.domain.vocabulary import Vocabulary
from app.search.base import DEFAULT_LIMIT, PaperSearchProvider


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Results bound to the query that produced them.

    Returned together on purpose. A result set that has been separated from its
    query cannot be audited, and auditability is the point of the system.
    """

    source_id: str
    query: BooleanQuery
    records: tuple[PaperRecord, ...]

    @property
    def total(self) -> int:
        return len(self.records)


class SearchService:
    """Depends on the Protocol, never on a concrete provider.

    It cannot tell whether it is talking to an in-memory corpus or to OpenAlex,
    which is exactly the property that lets tests run against the fake.
    """

    def __init__(self, provider: PaperSearchProvider) -> None:
        self._provider = provider

    async def run(self, vocabulary: Vocabulary, limit: int = DEFAULT_LIMIT) -> SearchOutcome:
        query = compile_vocabulary(vocabulary)
        found = await self._provider.search(query, limit=limit)
        return SearchOutcome(
            source_id=self._provider.source_id,
            query=query,
            # Deduplicating a single source is almost a no-op today. It is here
            # because the moment a second provider is added this becomes the
            # merge point, and a missing dedup step is invisible until counts
            # are already wrong.
            records=deduplicate(found),
        )
```

## Step 20 — the injection point

**Why.** This is the seam made operational, and the most important file in the session. Every provider choice is made here. Session 3 changes one `return` statement and nothing above it moves.

If you ever import a concrete provider inside a route, the architecture has sprung a leak — import it here instead.

**File:** `services/api/app/api/dependencies.py`

```python
"""Which concrete implementations the routes get.

This is the seam made operational. Every provider choice in the system is made
here and nowhere else, so swapping the fake for OpenAlex — or one LLM provider
for another — is an edit to this file alone.

If you ever find yourself importing a concrete provider inside a route, the
architecture has sprung a leak. Import it here instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.application.search_service import SearchService
from app.search.base import PaperSearchProvider
from app.search.fake import FakePaperSearchProvider

API_PREFIX = "/api/v1"


@lru_cache(maxsize=1)
def _search_provider_singleton() -> FakePaperSearchProvider:
    """One instance per process. Providers hold connection pools and caches."""
    return FakePaperSearchProvider()


def get_search_provider() -> PaperSearchProvider:
    """THE switch.

    Session 3 changes this one return statement to an OpenAlexProvider and
    nothing above this line moves. That is the whole claim the architecture
    makes, expressed in one function.
    """
    return _search_provider_singleton()


SearchProviderDep = Annotated[PaperSearchProvider, Depends(get_search_provider)]


def get_search_service(provider: SearchProviderDep) -> SearchService:
    return SearchService(provider)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
```

## Step 21 — the routes

**Why.** Now there is a second endpoint, `main.py` is genuinely crowded, so `APIRouter` earns its place. Three modules, one per tag, which is also how Swagger groups them.

`/info` is worth the ten lines: the whole design rests on implementations being swappable, and this makes the current choice visible rather than buried in a config file.

**File:** `services/api/app/api/routes/__init__.py`

```python
"""HTTP routes, grouped by resource. Each module exposes a `router`."""
```

**File:** `services/api/app/api/routes/system.py`

```python
"""Liveness and configuration visibility."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import API_PREFIX, SearchProviderDep
from app.api.schemas import HealthOut, InfoOut

router = APIRouter(tags=["system"])

API_NAME = "humani-t API"
API_VERSION = "0.1.0"

# Sources that answer from memory rather than over the network. Used to tell a
# reader of /info whether this process would phone anyone.
OFFLINE_SOURCES = {"fake"}


@router.get("/health", summary="Liveness probe")
async def health() -> HealthOut:
    """Answers 'is this process up', not 'is the whole system healthy'.

    Deliberately touches no provider and no database. Conflating the two makes
    deploys hard to debug, because a failing dependency looks like a dead process.
    """
    return HealthOut(status="ok")


@router.get("/info", summary="What is wired into this process")
async def info(provider: SearchProviderDep) -> InfoOut:
    """Reports which implementations are active behind the interfaces.

    Worth having early: the whole design rests on implementations being
    swappable, and this is the endpoint that makes the current choice visible
    instead of buried in a config file.
    """
    return InfoOut(
        name=API_NAME,
        version=API_VERSION,
        api_prefix=API_PREFIX,
        search_provider=provider.source_id,
        makes_network_calls=provider.source_id not in OFFLINE_SOURCES,
    )
```

**File:** `services/api/app/api/routes/vocabulary.py`

```python
"""Turning a vocabulary into a Boolean query."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import CompiledQueryOut, VocabularyIn
from app.domain.query import compile_vocabulary

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


@router.post(
    "/compile",
    summary="Compile a vocabulary into a Boolean query",
    response_model=CompiledQueryOut,
)
async def compile_endpoint(payload: VocabularyIn) -> CompiledQueryOut:
    """Show exactly what will be searched for, before anything is searched.

    Concepts sharing a theme are OR'd; themes are AND'd:

        ("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")

    Pure and deterministic — the same vocabulary always produces the same query,
    and no model is consulted for either origin. That is what makes a
    `user_supplied` vocabulary strict by construction rather than by discipline.

    This endpoint exists so a reviewer can inspect and argue about the query
    itself, which is the transparency commitment made concrete.
    """
    try:
        vocabulary = payload.to_domain()
    except ValueError as exc:
        # Domain invariants are the real validation. Pydantic checks shape;
        # the domain checks meaning.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return CompiledQueryOut.from_domain(compile_vocabulary(vocabulary))
```

**File:** `services/api/app/api/routes/search.py`

```python
"""Searching for papers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import SearchProviderDep, SearchServiceDep
from app.api.schemas import CompiledQueryOut, PaperOut, SearchIn, SearchOut, SourceOut

router = APIRouter(tags=["search"])

SOURCE_DESCRIPTIONS = {
    "fake": "In-memory corpus of four papers. No network, no key, no rate limit.",
    "openalex": "OpenAlex works index. Keyless; polite pool via mailto.",
    "arxiv": "arXiv preprints.",
    "europepmc": "Europe PMC life-sciences literature.",
    "scholar": "Google Scholar.",
}


@router.get("/sources", summary="Search sources available in this process")
async def sources(provider: SearchProviderDep) -> list[SourceOut]:
    """Which repositories this deployment can currently reach.

    One entry today. It becomes the list a reviewer checks to know what a run
    did and did not cover — and an empty or unexpected entry here is the first
    sign a run is not reproducible.
    """
    source_id = provider.source_id
    return [
        SourceOut(
            source_id=source_id,
            live=source_id != "fake",
            description=SOURCE_DESCRIPTIONS.get(source_id, "Unknown source."),
        )
    ]


@router.post("/search", summary="Compile a vocabulary and search for papers")
async def search(payload: SearchIn, service: SearchServiceDep) -> SearchOut:
    """Run a vocabulary against the configured source.

    The response carries the compiled query and its provenance alongside the
    results, because a result set separated from the query that produced it
    cannot be audited.

    Currently backed by an in-memory corpus. The endpoint, the request body and
    the response shape do not change when a real source is wired in — only
    `api/dependencies.py` does.
    """
    try:
        vocabulary = payload.to_domain()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    outcome = await service.run(vocabulary, limit=payload.limit)

    return SearchOut(
        source_id=outcome.source_id,
        query=CompiledQueryOut.from_domain(outcome.query),
        total=outcome.total,
        results=[PaperOut.from_domain(r) for r in outcome.records],
    )
```

## Step 22 — rewrite main.py to assemble routers

**Why.** The composition root now only wires URLs. `TAGS_METADATA` and the description are not decoration — they are what makes `/docs` readable to someone who has not seen the codebase.

**File:** `services/api/app/main.py`

```python
"""Composition root for the humani-t API.

Assembles routers and nothing else. The decision about *which* implementations
are used lives in `app/api/dependencies.py`; this module only wires URLs.
"""

from fastapi import APIRouter, FastAPI

from app.api.dependencies import API_PREFIX
from app.api.routes import search, system, vocabulary
from app.api.routes.system import API_NAME, API_VERSION

TAGS_METADATA = [
    {"name": "system", "description": "Liveness and what is wired into this process."},
    {
        "name": "vocabulary",
        "description": (
            "Turning curated terms into a Boolean query. Concepts sharing a theme "
            "are OR'd; themes are AND'd. A user-supplied vocabulary is used "
            "exactly as given."
        ),
    },
    {
        "name": "search",
        "description": (
            "Running a compiled query against a source. Results always travel "
            "with the query and provenance that produced them."
        ),
    },
]

app = FastAPI(
    title=API_NAME,
    version=API_VERSION,
    description=(
        "Privacy-preserving evidence synthesis for humanitarian decisions.\n\n"
        "**Status: walking skeleton.** Search is backed by an in-memory corpus, "
        "so nothing here reaches the network. Swapping in a real repository is a "
        "change to `app/api/dependencies.py` alone — the endpoints and payloads "
        "below stay exactly as they are."
    ),
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)

v1 = APIRouter(prefix=API_PREFIX)
v1.include_router(system.router)
v1.include_router(vocabulary.router)
v1.include_router(search.router)

app.include_router(v1)
```

## Step 23 — API tests

**File:** `services/api/tests/test_api.py`

`TestProviderSwap` is the one that matters. FastAPI's `dependency_overrides` replaces the provider; the endpoint returns different data; the route, service, schemas and domain are untouched. That is the architectural claim, executable.

```python
"""The HTTP boundary: compilation, search, and the provider switch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_search_provider
from app.domain.paper import PaperRecord
from app.main import app
from app.search.fake import FakePaperSearchProvider

VOCABULARY = {
    "origin": "llm_assisted",
    "concepts": [
        {"term": "chatbot", "synonyms": ["conversational agent"], "theme": "technology"},
        {"term": "humanitarian", "synonyms": ["crisis response"], "theme": "setting"},
    ],
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSystem:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_info_reports_the_active_provider(self, client):
        body = client.get("/api/v1/info").json()

        assert body["search_provider"] == "fake"
        assert body["makes_network_calls"] is False
        assert body["api_prefix"] == "/api/v1"

    def test_sources_lists_what_is_reachable(self, client):
        body = client.get("/api/v1/sources").json()

        assert [s["source_id"] for s in body] == ["fake"]
        assert body[0]["live"] is False


class TestCompile:
    def test_renders_or_within_theme_and_and_across_themes(self, client):
        body = client.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        assert body["rendered"] == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")'
        )
        assert body["term_count"] == 4

    def test_provenance_is_returned(self, client):
        body = client.post(
            "/api/v1/vocabulary/compile",
            json={**VOCABULARY, "origin": "user_supplied", "version": 3},
        ).json()

        assert body["provenance"] == {
            "vocabulary_version": 3,
            "vocabulary_origin": "user_supplied",
            "is_strict": True,
        }

    def test_structure_groups_terms_under_their_concept(self, client):
        body = client.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        technology = body["themes"][0]
        assert technology["theme"] == "technology"
        assert technology["groups"][0] == {
            "concept": "chatbot",
            "terms": ["chatbot", "conversational agent"],
        }

    def test_empty_concepts_is_rejected(self, client):
        response = client.post(
            "/api/v1/vocabulary/compile", json={"concepts": [], "origin": "llm_assisted"}
        )
        assert response.status_code == 422

    def test_blank_term_is_rejected(self, client):
        response = client.post(
            "/api/v1/vocabulary/compile",
            json={"concepts": [{"term": "   "}], "origin": "llm_assisted"},
        )
        assert response.status_code == 422


class TestSearch:
    def test_returns_results_with_the_query_that_produced_them(self, client):
        body = client.post("/api/v1/search", json={**VOCABULARY, "limit": 25}).json()

        assert body["source_id"] == "fake"
        assert body["total"] == 2
        assert "chatbot" in body["query"]["rendered"]
        assert body["query"]["provenance"]["is_strict"] is False

    def test_and_across_themes_excludes_single_theme_matches(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()
        ids = {r["external_id"] for r in body["results"]}

        assert ids == {"fake-001", "fake-002"}

    def test_every_result_carries_a_dedup_identity(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()

        assert all(r["identity"].startswith("doi:") for r in body["results"])

    def test_limit_is_respected(self, client):
        body = client.post("/api/v1/search", json={**VOCABULARY, "limit": 1}).json()
        assert body["total"] == 1

    def test_limit_is_bounded(self, client):
        assert client.post("/api/v1/search", json={**VOCABULARY, "limit": 0}).status_code == 422
        assert (
            client.post("/api/v1/search", json={**VOCABULARY, "limit": 999}).status_code == 422
        )

    def test_no_match_returns_empty_not_error(self, client):
        body = client.post(
            "/api/v1/search",
            json={"concepts": [{"term": "quantum chromodynamics"}]},
        ).json()

        assert body["total"] == 0
        assert body["results"] == []


class TestProviderSwap:
    """The claim the architecture makes, tested.

    Overriding the provider changes what the endpoint returns without touching
    the route, the service, the schemas or the domain.
    """

    def test_swapping_the_provider_changes_only_the_data(self, client):
        custom = FakePaperSearchProvider(
            corpus=(
                PaperRecord(
                    source_id="fake",
                    external_id="swap-1",
                    title="Chatbot ethics in humanitarian triage",
                    year=2025,
                    doi="10.1/swap",
                ),
            )
        )
        app.dependency_overrides[get_search_provider] = lambda: custom
        try:
            body = client.post("/api/v1/search", json=VOCABULARY).json()
        finally:
            app.dependency_overrides.clear()

        assert [r["external_id"] for r in body["results"]] == ["swap-1"]

    def test_original_provider_is_restored_after_the_override(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()
        assert {r["external_id"] for r in body["results"]} == {"fake-001", "fake-002"}


class TestOpenAPI:
    def test_all_endpoints_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert set(paths) == {
            "/api/v1/health",
            "/api/v1/info",
            "/api/v1/sources",
            "/api/v1/vocabulary/compile",
            "/api/v1/search",
        }

    def test_request_bodies_carry_a_worked_example(self, client):
        """Swagger's 'Try it out' should prefill something that actually works."""
        schema = client.get("/openapi.json").json()
        example = schema["components"]["schemas"]["VocabularyIn"]["example"]

        assert example["concepts"][0]["term"] == "chatbot"

    def test_endpoints_are_grouped_by_tag(self, client):
        schema = client.get("/openapi.json").json()
        tags = {t["name"] for t in schema["tags"]}

        assert tags == {"system", "vocabulary", "search"}
```

## Step 24 — verify the whole surface

```bash
uv run pytest            # 59 passed
uv run ruff check .      # All checks passed!
uv run ruff format .
```

Then with the server running:

```bash
curl http://127.0.0.1:8731/api/v1/info
curl http://127.0.0.1:8731/api/v1/sources
curl -X POST http://127.0.0.1:8731/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"concepts":[{"term":"chatbot","theme":"technology"},
       {"term":"humanitarian","theme":"setting"}]}'
```

**Check.** `/docs` now shows five endpoints in three groups, and Try it out on `/api/v1/search` returns two papers without you typing a body.

---

# Step 25 — commit

One branch, several commits. Use `git add -p` to stage them separately.

```bash
git add services/api/app/__init__.py services/api/app/main.py
git commit -m "feat(api): add /api/v1/health route"

git add services/api/pyproject.toml services/api/uv.lock
git commit -m "build(api): add pytest-asyncio, httpx2, ruff and pytest config"

git add services/api/app/domain
git commit -m "feat(api): add Vocabulary, BooleanQuery and PaperRecord"

git add services/api/app/search
git commit -m "feat(api): define PaperSearchProvider and add a fake"

git add services/api/tests/test_domain.py services/api/tests/test_search.py
git commit -m "test(api): cover domain rules and the search seam"

git add services/api/app/api services/api/app/application services/api/app/main.py
git commit -m "feat(api): expose vocabulary compile, search, sources and info"

git add services/api/tests/test_api.py
git commit -m "test(api): cover the HTTP boundary and the provider swap"
```

---

# Two things I got wrong

Written into the steps above already, but worth knowing why:

**`(str, Enum)` should be `StrEnum`.** I wrote `class VocabularyOrigin(str, Enum)`, which is the pre-3.11 idiom. Ruff's `UP042` flagged it. `StrEnum` has been available since Python 3.11 and does the same job without the double inheritance.

**`httpx` should be `httpx2`.** I added `httpx` for `TestClient` and every test run printed a starlette deprecation warning. The `TestClient` now expects `httpx2`. Swapping it cleared the warning and cut the run from 4.94s to 0.52s, which was the compatibility shim being slow.

**`HTTP_422_UNPROCESSABLE_ENTITY` should be `HTTP_422_UNPROCESSABLE_CONTENT`.** Starlette renamed it to match the current HTTP spec, where 422 is 'Unprocessable Content'. The old name still works and warns.

All three were caught by tooling rather than by reading. That is the argument for installing ruff on day one instead of day thirty.

---

# What you have now

```
HTTP  ->  domain  ->  provider interface  ->  fake implementation
```

Five endpoints, 59 tests, and the network switched off. [Session 3] (./implementation-plan.md) swaps the fake for OpenAlex without changing anything above `search/` — which is the same move that lets every test run against fakes.
