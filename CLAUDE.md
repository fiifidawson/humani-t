# humani-t — working agreement

Privacy-preserving evidence synthesis for humanitarian decisions. A reviewer states a question, the system searches academic repositories, screens the results and synthesises them with citations — reproducibly, and without sending sensitive material anywhere it should not go.

This file is loaded into every session. It is kept short on purpose: everything here has to earn its place, or it dilutes the things that matter.

Deeper context lives in [`docs/architecture.md`](docs/architecture.md) (provider seams, the domain model, the security model) and [`docs/dependencies.md`](docs/dependencies.md) (build order, upstream projects). Decisions that are hard to reverse are in [`docs/adr/`](docs/adr/). Read those before structural work.

---

## The one rule

> humani-t is a self-hosted web application. Data leaves the deployment only to destinations the operator has configured, and only after it has been masked.

Before any implementation decision, ask: *does this send anything — a question, a document, a log line, a request — somewhere the operator did not choose?* If it might, put it behind a provider seam the operator configures, and default it to off or to something we can run ourselves.

There is a second test specific to this project:

> Could a reviewer reconstruct why this paper was included or excluded, six months from now, from what we stored?

Provenance is captured when a decision is made, never reconstructed later.

---

## Layering

```
app/api/          HTTP boundary — Pydantic, status codes, JSON
app/application/  use cases — orchestration
app/domain/       entities and rules — no I/O, no SDKs
app/<seam>/       provider Protocols + implementations
```

Dependencies point **downward only**.

| Layer | May import | Must never import |
| --- | --- | --- |
| `api/` | application, domain, schemas | a concrete provider |
| `application/` | domain, provider Protocols | a concrete provider, FastAPI |
| `domain/` | stdlib, other domain modules | anything else at all |
| `<seam>/base.py` | domain | a concrete provider |
| `<seam>/<impl>.py` | domain, its own SDK | api, application |

Concrete implementations are chosen in exactly one file: [`app/api/dependencies.py`](services/api/app/api/dependencies.py). Importing a provider class anywhere else is the architecture leaking — put it there instead.

---

## Commands

```bash
# once: local secrets + services/api/.env, then the dev database
python scripts/dev_secrets.py
docker compose up -d          # postgres, valkey, mailpit (inbox: 127.0.0.1:58025)

cd services/api
uv run --env-file .env alembic upgrade head        # migrate (schema-owner role)
uv run --env-file .env uvicorn app.main:app --reload --port 8731   # /docs
uv run pytest                                      # all tests (Postgres ones skip)
TEST_DATABASE_URL=…/humani_t_test TEST_VALKEY_URL=… uv run pytest   # + Postgres, Valkey
uv run pytest tests/test_domain.py -v              # one file
uv run ruff check . && uv run ruff format .        # lint, format
uv run pyright                                     # types
uv run lint-imports                                # layering rules
uv run python -m app.cli generate-kek              # operator commands
echo '<password>' | uv run --env-file .env python -m app.cli create-admin --email you@example.org --password-stdin
```

Settings never read `.env` on their own; `--env-file` loads it explicitly, so the test suite cannot pick up a developer's database by accident.

Port 8731 rather than 8000: `mkdocs serve` for `docs-site/` holds 8000, and on Windows both can bind it without an error — uvicorn reports healthy while the browser shows the docs site.

---

## Python

Target 3.13. Style is enforced by ruff (`E, F, I, UP, B, SIM, RUF`), line length
96. Do not argue with the formatter; run it.

### Standards that apply

| PEP | What it means here |
| --- | --- |
| **8** | Style. Ruff enforces it. Line length is 96, not 79 — a deliberate deviation for readable type signatures. |
| **20** | When two designs are equally correct, take the one that is easier to explain. |
| **257** | Docstrings: imperative mood, summary line, blank line, detail. |
| **484 / 585 / 604** | Everything public is annotated. `list[str]` not `List[str]`, `str \| None` not `Optional[str]`. |
| **544** | Provider seams are `Protocol`s, not ABCs. Implementations inherit from nothing. |
| **563** | `from __future__ import annotations` is the first import in every module — it lets a class reference itself in its own signatures without quoting. |
| **621** | Project metadata lives in `pyproject.toml`. No `setup.py`. |

### Types

- Annotate every public function, parameter and return. Locals only when the inference is genuinely unclear.
- `Protocol` for seams. An implementation that needs a base class to conform is a sign the interface is wrong.
- `Sequence`/`Mapping` for parameters, concrete `tuple`/`dict` for returns — accept broadly, return precisely.
- Immutable domain types: `@dataclass(frozen=True, slots=True)`.

### Pydantic belongs at the boundary, nowhere else

Pydantic appears in exactly two boundary modules, both reading untrusted input:

- `app/api/schemas` — HTTP. JSON is validated into a schema, then converted through `to_domain()` into a plain dataclass.
- `app/settings.py` — the environment and secret files. Only the composition root (`main.py`, `api/dependencies.py`, `cli.py`, `migrations/env.py`) imports `Settings`; it hands lower layers frozen domain policy objects instead.

Domain objects validate themselves in `__post_init__` and are valid by construction. Pydantic checks *shape*; the domain checks *meaning*. A domain `ValueError` becomes a 422 at the boundary, not deeper.

This is not stylistic. It keeps the wire format and the deployment's configuration from leaking into the rules the domain enforces.

---

## Comments

Default to none. Good names and small functions carry the *what*. A comment that restates the line below it is noise that still has to be maintained.

A comment earns its place when it carries something the code cannot:

**1. Why this, and not the obvious alternative.** Name the rejected option.

```python
# DOI wins when present — it is the only globally unique identifier here.
# Fuzzy title matching would hide wrong merges, and a wrong merge is far
# harder to notice than a missed one.
```

**2. A constraint from outside the codebase.** An API quirk, a rate limit, a spec.

```python
# OpenAlex puts mailto= callers in the polite pool with higher rate limits.
```

**3. A limitation that is known and accepted.**

```python
# Preprint and published versions differ in year and will not merge.
```

**4. A landmine.** Something that looks removable and is not. This one matters most, because both humans and agents delete code that appears redundant:

```python
# Deduplicating a single source is almost a no-op today. It is here because
# the moment a second provider is added this becomes the merge point, and a
# missing dedup step is invisible until counts are already wrong.
```

### Rules

- One to three lines. Longer belongs in a docstring or in `docs/`.
- Explain **why**, never **what**.
- Say the fact, not the history. Not "we changed this because…".
- Delete rather than comment out. Git remembers.
- No section banners, no `# ---- helpers ----`, no changelog headers.
- If a comment is needed to explain *what* a block does, extract a named function instead.

### Docstrings

Module docstring on every module, saying what belongs there and what does not. That boundary statement is the most useful line in the file.

Public functions get a docstring when the name is not sufficient. One imperative summary line; add detail only where a caller could reasonably get it wrong — `None` meaning "not available" rather than "error", for instance.

Skip docstrings on obvious properties and private helpers. `def is_frozen(self)` does not need prose.

---

## Naming

- Reveal intent: `deduplicate`, not `process`. `vocabulary_version`, not `ver`.
- No abbreviations except `id`, `db`, `api`, `url`, `doi`.
- Booleans read as assertions: `is_strict`, `is_open_access`, `makes_network_calls`.
- Domain vocabulary is fixed — `Review`, `Vocabulary`, `Concept`, `BooleanQuery`, `PaperRecord`, `ScreeningDecision`, `Synthesis`, `RunManifest`. Do not invent synonyms for these; if a new concept is needed, add it to `docs/architecture.md` first.
- Private module members take a leading underscore.

---

## Errors

- Raise `ValueError` from the domain with a message naming the field: `"Concept.term must not be blank"`.
- Translate to HTTP only at the boundary, with `raise ... from exc` so the chain survives.
- Never swallow an exception silently. Never `except Exception: pass`.
- Absence is not failure: `fetch_fulltext` returns `None` for a closed-access paper because that is a normal outcome. Reserve exceptions for the unexpected.

---

## Tests

- Mirror the source tree: `tests/test_domain.py`, `tests/test_api.py`.
- Test names are sentences: `test_and_across_themes_excludes_single_theme_matches`.
- Group with plain classes (`class TestVocabulary:`), no inheritance.
- Assert on behaviour, not implementation.
- Fixtures build valid objects; each test perturbs one thing.
- `asyncio_mode = "auto"` — async tests need no marker.

**Fakes over mocks.** `FakePaperSearchProvider` evaluates queries for real. A fake that returns a fixed list makes every test pass without proving anything. If a fake is awkward to write against an interface, the interface is wrong — fix it then, while it is still free.

**Encode invariants as tests.** Some rules are too important to leave as prose. `test_strict_vocabulary_gains_no_terms` asserts the compiled term set equals the supplied set exactly — that is what will stop a future expansion step reaching user-supplied terms.

---

## TypeScript

Biome formats and lints; tabs, double quotes. `pnpm lint`, `pnpm format`.

TypeScript is type-checked as strictly as Python: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. `pnpm typecheck` runs in pre-commit and CI. No `any`, no `@ts-ignore`; `@ts-expect-error` only with a reason.

API types are **generated**, never hand-written:

```bash
pnpm exec openapi-typescript http://127.0.0.1:8731/openapi.json \
  -o apps/web/src/api/schema.gen.ts
```

A backend contract change should break the frontend type check, not its runtime. `apps/web` holds no business logic: every rule — who may see a project, what gets masked — is enforced by the API, because the browser is not trusted.

---

## Before saying it is done

```bash
cd services/api && uv run pytest --cov && uv run ruff check . \n  && uv run ruff format --check . && uv run pyright && uv run lint-imports
```

Then:

- Did a new seam get a Protocol in `<area>/base.py` and a fake?
- Does anything outside `api/dependencies.py` import a concrete provider?
- Did a new endpoint get tests and an entry in [`docs/api.md`](docs/api.md)?
- Does a new domain entity appear in `docs/architecture.md` §4?
- If a decision was hard to reverse, is there an ADR in `docs/adr/`?

Report what actually happened. A failing test named in full is more useful than a summary that omits it.

---

## Do not

- Put business logic in `apps/web`.
- Hold a database connection across hashing, email or HTTP, or overwrite a shared record without a condition or version check (`docs/architecture.md` §5.7).
- Point tests at the development database: contract tests delete what they write.
- Let a provider SDK type appear in `domain/` or `application/`.
- Add a dependency without saying why in the commit message.
- Use `openai_filter` as the mmore privacy detection engine — it sends unsanitised text to a third party, inverting the point of the feature.
- Default any model call to a hosted API. Every call goes through `InferenceProvider` and the default must be one we can run ourselves.
- Commit or push unless asked.

---

## Commits

`<type>(<scope>): <description>` — see [`docs/standards.md`](docs/standards.md) for types, scopes and the git command reference. Small commits, one idea each; `git add -p` is how that stays possible.
