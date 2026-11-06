# 01 — Baseline and quality gates

> **Milestones:** M0, M1, M2 · **Status:** done · **Previous:** [00 — Setup](00-setup.md) · **Next:** [02 — Configuration and persistence](02-configuration-and-persistence.md)

This phase made the project something you can safely build on. M0 committed the existing search skeleton as it was (and repaired a broken `main.py`). M1 dropped the planned desktop version and wrote the decision down as [ADR-0001](../adr/0001-web-only-self-hosted.md). M2 added the gates every commit must pass: lint and format, types, layering, coverage, property-based tests, and secret scanning. Nothing here is user-facing security yet; it is the structure that makes the security work in phases 02 to 05 checkable.

## What this phase delivers

- **A web-only, self-hosted project.** The desktop app (V2), Render as a default host, `BackupProvider`, `url_for`, `apps/desktop`, `crates/`, the `packages/*` folders and the V1-to-V2 diagram are gone ([ADR-0001](../adr/0001-web-only-self-hosted.md)).
- **The layered architecture** of [`architecture.md` §1](../architecture.md#1-layering): `api/` → `application/` → `domain/`, with provider seams beside them, enforced by four import-linter contracts.
- **The search skeleton** (M0): `Vocabulary` and `Concept`, `compile_vocabulary` into a `BooleanQuery`, `PaperRecord` with a dedup identity, the `PaperSearchProvider` seam and its working fake, and `SearchService`.
- **Endpoints** (all under `/api/v1`):

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | anyone | Is the process up |
| GET | `/info` | anyone, development only | Which implementations are wired in |
| GET | `/sources` | anyone | Search sources available |
| POST | `/vocabulary/compile` | anyone | Vocabulary → Boolean query |
| POST | `/search` | anyone | Compile and search the corpus |

  `/vocabulary/compile`, `/search` and `/sources` are open to anyone today; they become signed-in and project-scoped in M10 (phase 07).
- **Quality gates** in `services/api/pyproject.toml` and `.pre-commit-config.yaml`: ruff (lint and format), pyright (strict on `domain/` and `application/`), import-linter, pytest with branch coverage of at least 85%, hypothesis, and pre-commit hooks including gitleaks, `uv lock --check` and Biome.

## Architecture choices and why

### D1.1 A self-hosted web application only

- **Decision:** one deployment shape, run by each organisation on infrastructure it controls, via Docker Compose (Caddy, the API, a worker, Postgres, Valkey). The planned desktop app (V2) is dropped, not postponed. Recorded in [ADR-0001](../adr/0001-web-only-self-hosted.md).
- **Why:** keeping a desktop version possible shaped every decision — a "will this still make sense on someone's laptop?" rule, a `BackupProvider` V1 never used, a `url_for` returning `None` for local files, an API client package with a `LocalAPIClient` — and doubled what had to be built, secured, tested and packaged. Self-hosting still keeps data inside the organisation.
- **Rejected:** a hosted SaaS (the organisation would have to trust us with sensitive data); a desktop app (double the surface); Render as the default host (vendor-specific; Compose runs anywhere Docker does).
- **Costs:** the data always lives on a server, so encryption at rest ([03](03-envelope-encryption.md)), access control (07) and audit ([04](04-audit-log.md)) carry the privacy claim. A user who wants nothing to leave their machine runs the stack locally with Docker; that is supported, but it is not a native app.
- **What changed meaning:** provider seams stay, but for a different reason — they keep the app testable with fakes and let an organisation choose its own LLM, storage and identity provider, not to enable a future local runtime. Files are always streamed through authorised routes, so every read is checked and audited.

### D1.2 One repository for everything

- **Decision:** a monorepo: `services/api` (Python), `apps/web` (frontend, planned), `infrastructure/`, `docs/`. The JavaScript workspace is `apps/*` only (`pnpm-workspace.yaml`).
- **Why:** an API change and the frontend change that uses it land in one commit, reviewed in one place. Generated TypeScript types (M13) will make a backend contract change break the frontend type check, not its runtime.
- **Rejected:** separate repositories (contract drift between them); the earlier shared `packages/` folders (nothing to share with one frontend).
- **Costs:** two toolchains (uv for Python, pnpm for TypeScript) in one place.

### D1.3 Layered architecture, enforced by a tool

- **Decision:** dependencies point downward only:

```text
app/api/          HTTP boundary — routes, Pydantic schemas, middleware, error mapping
app/application/  use cases — orchestrate domain rules and providers, own transactions
app/domain/       entities and rules — plain Python, no I/O, no libraries
app/<seam>/       provider Protocols (base.py) and their implementations
                  storage/  crypto/  auth/  email/  ratelimit/  search/
```

  The domain imports nothing but the standard library and itself. The application layer imports the domain and provider *Protocols*, never an implementation, and never FastAPI. Only `app/api/dependencies.py` chooses implementations. Four import-linter contracts in `pyproject.toml` encode the table in [`CLAUDE.md`](../../CLAUDE.md):

  1. *Domain imports nothing outside the domain and the standard library* — it may not import `app.api`, `app.application`, `app.main`, `app.settings`, `app.cli`, any provider package, or `fastapi`, `pydantic`, `pydantic_settings`, `sqlalchemy`, `cryptography`, `structlog`, `argon2`, `aiosmtplib`, `redis`.
  2. *Application depends on provider Protocols, never implementations or frameworks* — no concrete provider module (`app.storage.memory`, `app.storage.postgres`, `app.crypto.aesgcm`, `app.auth.argon2`, `app.email.smtp`, `app.ratelimit.valkey`, the fakes…), and no framework or SDK.
  3. *Providers never reach up into the API or application layers.*
  4. *Only api.dependencies chooses concrete providers* — routes, schemas, middleware, errors and `api.session` may not import a concrete provider or an SDK directly (indirect imports through `api.dependencies` are the design and are allowed).
- **Why:** the domain rules (what a valid vocabulary is, when a session expires) are the part most worth testing and least worth coupling to frameworks. A rule written in prose is broken eventually; a contract checked on every commit is not.
- **Rejected:** "routes call the database directly" (untestable, and nothing stops a route skipping a check — the forbidden shape `Route -> SQL -> LLM SDK`); relying on code review alone.
- **Costs:** more files and some indirection; a new package must be added to the contracts, or it is silently unchecked.
- **Proven by:** `uv run lint-imports` (`Contracts: 4 kept, 0 broken.`), and [T1.11](#t111-import-linter-catches-a-layering-violation).

### D1.4 Provider seams as Protocols, with fakes

- **Decision:** each outside dependency sits behind a `typing.Protocol` in `<seam>/base.py`, with a real implementation and a fake. This phase has one seam, `PaperSearchProvider` (`source_id`, `search(query, limit)`, `fetch_fulltext(record)`), and one implementation, `FakePaperSearchProvider`. Later phases add storage, crypto, password hashing, email and rate limiting the same way.
- **Why:** use cases are tested without a network or database, and an organisation can swap a provider (another key store, mail server, model or paper source) by changing one file. Protocols (PEP 544) need no inheritance, so implementations stay plain classes; `TestConformance` checks the fake satisfies the Protocol *without* inheriting it.
- **Fakes over mocks:** a fake really behaves. `FakePaperSearchProvider` evaluates the compiled query for real against a four-paper corpus chosen so that AND across themes actually discriminates (two papers match one theme only and must not be returned). A mock that returns canned values makes tests pass without proving anything.
- **Rejected:** abstract base classes (force inheritance); mocking libraries everywhere.
- **Costs:** every seam needs two implementations, and the fake must not drift from the real one — hence the contract suites from phase 02 onwards ([D2.8](02-configuration-and-persistence.md#d28-in-memory-storage-plus-contract-tests)).
- **Where:** [`app/search/base.py`](../../services/api/app/search/base.py), [`app/search/fake.py`](../../services/api/app/search/fake.py).

### D1.5 One composition root and an app factory

- **Decision:** `app/api/dependencies.py` is the **only** place concrete implementations are chosen; `app/main.py` (`create_app(settings, providers=…)`) builds the app from them. In this phase the choice is a single line (`FakePaperSearchProvider()`); from phase 02 on it is `build_providers(settings)`.
- **Why:** swapping an implementation is a one-file change, and tests build an app with whatever settings or fakes they need, without patching globals (`TestProviderSwap` swaps the search provider and checks only the data changes).
- **Rejected:** module-level singletons; choosing implementations inside routes.
- **Costs:** tests pass providers explicitly; the composition root grows as seams are added.

### D1.6 Domain objects are immutable and valid by construction

- **Decision:** entities are `@dataclass(frozen=True, slots=True)` that check themselves in `__post_init__` and raise `ValueError` naming the field (`"Concept.term must not be blank"`). Changes produce new objects: `Vocabulary.revise(...)` returns a new, unfrozen version; `freeze(at=…)` records the moment of confirmation and is idempotent.
- **Why:** an invalid object can never exist, so code receiving one need not re-check it. Immutability makes concurrent code easier to reason about, and a vocabulary that cannot change is what lets search results reference the version that produced them ([`architecture.md` §4.3](../architecture.md#43-how-a-vocabulary-is-acquired)).
- **Rejected:** Pydantic models as domain entities (couples the rules to the wire format; see [D2.3](02-configuration-and-persistence.md#d23-pydantic-only-at-the-two-boundaries)).
- **Costs:** "updates" are copies; mapping to and from rows or JSON is explicit.

### D1.7 A query is a structure, compiled per source; strict means strict

- **Decision:** `compile_vocabulary` turns a `Vocabulary` into a `BooleanQuery` — themes AND'd, concepts within a theme OR'd, each concept's terms OR'd — that is a structure, not a string. `render()` produces the readable form (`("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")`) for people, logs and audit only; each provider compiles the structure into its own dialect. Compilation is pure and deterministic and consults no model for either origin, so a `user_supplied` vocabulary is strict by construction. The query carries `vocabulary_version` and `vocabulary_origin`, and every search response returns the compiled query beside the results.
- **Why:** four repositories with four syntaxes agree on nothing; a structure keeps one search reproducible across them. A result set separated from the query that produced it cannot be audited (the second test in [`CLAUDE.md`](../../CLAUDE.md)).
- **Paper identity:** `PaperRecord.identity` is `doi:<normalised DOI>` when there is a DOI, otherwise `title:<normalised title>|<year>`. DOI wins because it is the only globally unique identifier. Fuzzy title matching was rejected: a wrong merge is far harder to notice than a missed one. Known and accepted: preprint and published versions differ in year and will not merge. `SearchService` deduplicates even a single source, because the moment a second provider is added that becomes the merge point.
- **Proven by:** `tests/test_domain.py::TestCompilation::test_strict_vocabulary_gains_no_terms`, `TestPaperIdentity`, `TestDeduplicate`, `tests/test_search.py::TestSearch::test_provider_receives_the_compiled_query_unmodified`.

### D1.8 Tests are the specification

- **Decision:** for every feature, tests are written first as plain-language requirements traced to the brief, NIST or OWASP; they are reviewed while failing; then the code is written until they pass. Tests are never weakened to pass. The spec and its implementation are committed together, so every commit passes.
- **Test architecture:** sentence-style names (`test_and_across_themes_excludes_single_theme_matches`); plain classes for grouping (`class TestVocabulary:`), no inheritance; fakes rather than mocks; fixtures build valid objects and each test perturbs one thing; `asyncio_mode = "auto"`, so async tests need no marker. Later phases add a controllable `FakeClock`, instrumented storage, hasher and email that record whether a transaction is open when slow work happens, and contract suites against real Postgres and Valkey.
- **Why:** the tests are the most reviewable statement of what the system must do, and they catch regressions — several were caught this way (one is described in [D5.5](05-accounts-and-sessions.md#d55-accounts-cannot-be-enumerated)).

### D1.9 Gates run before every commit, not only in CI

- **Decision:** the same checks run in a pre-commit hook and (from M4) in CI: ruff format and `ruff check --fix`, pyright, import-linter, `uv lock --check`, Biome, gitleaks, and the standard hygiene hooks (trailing whitespace, end-of-file newline, YAML and TOML syntax, merge-conflict markers, files over 1 MiB, private keys). Tests and coverage run by hand (`uv run pytest --cov`) and will run in CI.
- **Why:** broken code never reaches a commit, and a secret caught before the commit never enters history (a leaked key in git history is permanent).
- **Costs:** pre-commit stashes unstaged changes and checks only what is staged, so each commit must pass on its own — which is why a spec is committed together with its implementation.
- **Where:** [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml); install once with `cd services\api; uv run pre-commit install`.

### D1.10 Documentation lives with the code

- **Decision:** decisions that are hard to reverse get an ADR in `docs/adr/`. Security controls are mapped to their tests in [`security/controls.md`](../security/controls.md). Markdown is never hard-wrapped. Every module has a docstring saying what belongs in it and what does not; comments explain *why*, never *what*.
- **Why:** a reviewer can trace any claim to a test, and history explains itself.

### D1.11 Git workflow

- **Decision:** `main` stays releasable; work happens on short-lived `<type>/<topic>` branches and lands by pull request. Commits follow Conventional Commits (`feat(auth): …`; types and scopes in [`standards.md`](../standards.md)), one idea each, staged with `git add -p`. The owner reviews and commits by hand.
- **Why:** small, readable history; checks run before broken code reaches anyone.

### Deliberately not used yet

| Not used | Why not |
| --- | --- |
| MinIO, ClamAV | Deferred until uploads exist; earlier, they only add attack surface (MinIO's licensing and image status also need checking) |
| Celery | Not needed until background jobs arrive (the worker, M4; runs, phase 08) |
| mmore | Arrives with masking and indexing (phase 10), inside an isolated worker |
| Hosted email, LLM or search APIs by default | The one rule: nothing leaves the deployment unless the operator chooses it |

## Libraries

Versions are those locked in `services/api/uv.lock`.

### FastAPI 0.141.1 (with Starlette 1.6.0)

- **What it does:** the web framework. Routes are Python functions with type hints; FastAPI validates requests with Pydantic, injects dependencies (`Annotated[..., Depends(...)]`), and generates the OpenAPI description behind `/docs`. Starlette underneath provides the ASGI request/response machinery, middleware and the test client.
- **Why this one:** async-native and type-hint driven, and the OpenAPI it generates becomes the contract for the TypeScript frontend (M13). Dependency injection is how the composition root reaches routes.
- **Alternatives:** Django (batteries included, but synchronous-first and ORM-centric); Flask (synchronous, no built-in validation or OpenAPI); Litestar (similar, smaller community).
- **Trade-offs:** fast-moving; dependency injection can hide control flow if overused, so business logic stays out of routes.
- **What we gave up:** Django's admin and auth batteries; we build accounts ourselves ([05](05-accounts-and-sessions.md)) so every rule is visible and tested.

### Uvicorn 0.52.4 (`uvicorn[standard]`)

- **What it does:** the ASGI server that runs the app. The `standard` extra adds uvloop (a faster event loop, Linux and macOS only) and httptools (a faster HTTP parser).
- **Why this one:** the standard server for FastAPI; small and well understood.
- **Alternatives:** Hypercorn (HTTP/2 and HTTP/3); Granian (Rust-based).
- **Trade-offs:** speaks HTTP/1.1 only, which is fine behind Caddy, which will handle HTTPS and HTTP/2 to the browser.

### Pydantic 2.13.5

- **What it does:** validates data against type hints and serialises it. Used here for request and response schemas (`app/api/schemas/`).
- **Why this one:** FastAPI is built on it; the Rust core makes it fast; `extra="forbid"` rejects unexpected fields.
- **Alternatives:** msgspec (faster, less ecosystem); attrs + cattrs.
- **Trade-offs:** powerful enough to tempt use everywhere; confined to the two boundaries by rule ([D2.3](02-configuration-and-persistence.md#d23-pydantic-only-at-the-two-boundaries)).

### pytest 9.1.1 and pytest-asyncio 1.4.0

- **What it does:** pytest is the test runner — fixtures, parametrisation, plain `assert`. pytest-asyncio runs `async def` tests; `asyncio_mode = "auto"` means no markers. `--strict-markers --strict-config` turn a typo in a marker or setting into an error; the `postgres` marker labels tests that need a real database.
- **Why this one:** the Python standard; readable tests. Most of the code is async.
- **Trade-offs:** fixture magic can obscure where values come from; event-loop scoping needs care (`asyncio_default_fixture_loop_scope = "function"`).

### pytest-cov 7.1.0

- **What it does:** measures which lines and **branches** tests execute (`branch = true`, `source = ["app"]`) and fails the run under 85% (`fail_under = 85`). Protocol `...` bodies, `if TYPE_CHECKING:` and `raise NotImplementedError` are excluded.
- **Why this one:** shows untested code at a glance (`show_missing`, `skip_covered`).
- **Trade-offs:** coverage measures execution, not correctness; mutation testing (planned, M4) checks that the tests would notice a change.

### hypothesis 6.170.9

- **What it does:** property-based testing: generates many inputs to find one that breaks a property, then shrinks it to the smallest failing example.
- **Why this one:** proves AES-GCM round-trips for *any* bytes and associated data (`TestAesGcmCipher::test_round_trips_any_bytes`, phase 03), not three hand-picked examples.
- **Trade-offs:** slower; a failure needs its shrunk example read carefully.

### httpx2 2.12.0

- **What it does:** the HTTP client under Starlette's `TestClient`, so tests call the app in-process through the real middleware and routes.
- **Why this one:** required by the test client.
- **Trade-offs:** a fork of httpx; follow upstream changes.

### ruff 0.16.6

- **What it does:** linter and formatter in one, written in Rust. Line length 96 (not 79: readable type signatures). Rules: `E F I UP B SIM RUF` from `CLAUDE.md`, plus `S` (bandit security smells such as hard-coded passwords), `ASYNC` (blocking calls inside async functions), `PT` (pytest style), `DTZ` (naive datetimes — every timestamp is UTC-aware), `TID` (relative imports banned), `C4`, `PTH` (pathlib), `N` (naming). Tests may use `assert` and literal fake passwords (`S101`, `S105`, `S106` ignored under `tests/**`); migrations ignore `S105` (revision ids are not secrets).
- **Why this one:** replaces flake8, isort, black and a bandit subset; very fast.
- **Trade-offs:** the rule set is opinionated; some rules need justified per-file exceptions.
- **What it does not do:** check imports *between* packages — that is import-linter's job ([T1.11](#t111-import-linter-catches-a-layering-violation)).

### pyright 1.1.414

- **What it does:** static type checker. `typeCheckingMode = "standard"` everywhere, **strict** on `app/domain` and `app/application`, where the rules of the system live.
- **Why this one:** catches whole classes of bugs before running; it found a real Protocol contract gap during the checkpoint work.
- **Trade-offs:** strict mode needs precise annotations, and some test-only `# type: ignore[...]` comments for deliberate fakes.

### import-linter 2.15

- **What it does:** checks import rules between packages (`root_package = "app"`, `include_external_packages = true`, so third-party SDKs can be forbidden too).
- **Why this one:** enforces the layering (D1.3) automatically.
- **Trade-offs:** contracts must be kept in step with new packages.

### pre-commit 4.7.0 (with pre-commit-hooks v6.0.0)

- **What it does:** runs the checks in D1.9 before each commit. The Python hooks run through `uv run --directory services/api …`, so they use the exact versions in `uv.lock`.
- **Why this one:** broken code never reaches a commit.
- **Trade-offs:** it stashes unstaged files, so each commit must pass on its own.

### gitleaks v8.28.0 (pre-commit hook)

- **What it does:** scans what is being committed for secrets (keys, tokens, high-entropy strings).
- **Why this one:** a leaked key in git history is permanent; catching it before the commit is the only cheap fix.
- **Trade-offs:** occasional false positives; a line that is deliberately fictional is marked with a `gitleaks:allow` comment on the same line (one example password in [05](05-accounts-and-sessions.md) carries it).

### Biome 2.5.12 and pnpm 12.3.4

- **What it does:** pnpm manages the JavaScript workspace (`apps/*`); Biome formats and lints JavaScript, TypeScript and JSON (`biome.json`: tabs, double quotes, the recommended rule set, organise imports).
- **Why this one:** one fast tool instead of ESLint plus Prettier.
- **Trade-offs:** present but barely used until the web UI (M13), when `pnpm typecheck` joins the hook.

## Files to read, in order

Read from the decision outwards, then the domain, then the seam, then HTTP:

1. [`docs/adr/0001-web-only-self-hosted.md`](../adr/0001-web-only-self-hosted.md) — the context, the decision and what it removed.
2. [`CLAUDE.md`](../../CLAUDE.md), sections *Layering*, *Python*, *Comments*, *Tests* — the rules the gates enforce.
3. [`services/api/pyproject.toml`](../../services/api/pyproject.toml) — the four import-linter contracts, the ruff rules and their per-file exceptions, pyright's strict paths, the coverage gate.
4. [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) — which hooks run, in which order, and why they go through `uv run`.
5. [`app/domain/vocabulary.py`](../../services/api/app/domain/vocabulary.py) — `VocabularyOrigin`, `Concept.terms` (canonical first, de-duplicated case-insensitively), `Vocabulary.freeze` and `revise`.
6. [`app/domain/query.py`](../../services/api/app/domain/query.py) — `BooleanQuery`, `render()` and its warning, `compile_vocabulary`.
7. [`app/domain/paper.py`](../../services/api/app/domain/paper.py) — `identity` and its docstring (the accepted limitation), `deduplicate`.
8. [`app/search/base.py`](../../services/api/app/search/base.py) then [`app/search/fake.py`](../../services/api/app/search/fake.py) — the seam, and a fake that really evaluates queries.
9. [`app/application/search_service.py`](../../services/api/app/application/search_service.py) — depends on the Protocol only; the dedup landmine comment.
10. [`app/api/schemas/__init__.py`](../../services/api/app/api/schemas/__init__.py) — `VocabularyIn.to_domain()`, `CompiledQueryOut.from_domain()`: Pydantic at the edge, dataclasses inside.
11. [`app/api/routes/vocabulary.py`](../../services/api/app/api/routes/vocabulary.py), [`search.py`](../../services/api/app/api/routes/search.py), [`system.py`](../../services/api/app/api/routes/system.py) — a domain `ValueError` becomes a `422` at the boundary, with `raise … from exc`.
12. [`tests/test_domain.py`](../../services/api/tests/test_domain.py), [`tests/test_search.py`](../../services/api/tests/test_search.py), [`tests/test_api.py`](../../services/api/tests/test_api.py) — the specification, in sentences.

## Set up

Nothing beyond [00 — Setup](00-setup.md). Start the API (S0.9) for the HTTP tests; the gate tests need only `cd services\api`. The search endpoints need no sign-in yet, so the Postman collection headers do no harm.

## Manual tests

### T1.1 to T1.8 The search skeleton

Request body used by T1.1 and T1.2 (`Content-Type: application/json`):

```json
{
  "origin": "llm_assisted",
  "concepts": [
    {"term": "chatbot", "synonyms": ["conversational agent"], "theme": "technology"},
    {"term": "humanitarian", "synonyms": ["crisis response"], "theme": "setting"}
  ]
}
```

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T1.1 | `POST {{base}}/vocabulary/compile` with the body above | `200`; `rendered` = `("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")`; `term_count` 4; `provenance` = `{"vocabulary_version":1,"vocabulary_origin":"llm_assisted","is_strict":false}` | OR within a theme, AND across themes; provenance travels with the query (D1.7) | Wrong shape: `compile_vocabulary` or `render()` changed |
| T1.2 | `POST {{base}}/search` with the same body plus `"limit": 10` | `200`; `source_id` `fake`; `total` 2; results `fake-001` and `fake-002`, each with an `identity` (`doi:10.1000/fake.001`, `doi:10.1000/fake.002`); the compiled `query` alongside | AND discriminates: `fake-003` (technology only) and `fake-004` (setting only) are excluded | 4 results means AND became OR — a serious correctness bug |
| T1.3 | `POST {{base}}/vocabulary/compile` with `{"origin":"llm_assisted","concepts":"Jane Doe, +44 7700 900123"}` | `422`, `application/problem+json`; `errors[0].loc` = `["body","concepts"]`; the text you sent does **not** appear anywhere in the response | Validation errors never echo input, which may be the personal data the user was trying to send ([D2.11](02-configuration-and-persistence.md#d211-errors-as-problem-documents)) | The name or number appears: the error handler leaks input |
| T1.4 | T1.1's body with `"origin": "user_supplied"` | `200`; `provenance.is_strict` `true`; terms exactly as given, canonical first | A user-supplied vocabulary is strict: nothing is added ([`architecture.md` §4.3](../architecture.md#43-how-a-vocabulary-is-acquired)) | Extra terms: something expanded a strict vocabulary |
| T1.5 | T1.1's body with the first term set to `"   "` (spaces) | `422`, `detail` = `Concept.term must not be blank` | Pydantic checks shape (a non-empty string), the domain checks meaning (not blank) (D1.6) | `200`: the domain lost its check; `500`: the `ValueError` is no longer translated at the boundary |
| T1.6 | T1.2's body with `"limit": 201` | `422` | Limits are bounded (`le=200`) | `200`: an unbounded result set is possible |
| T1.7 | `GET {{base}}/sources` | `200`, `[{"source_id":"fake","live":false,"description":"In-memory corpus of four papers. No network, no key, no rate limit."}]` | A reviewer can see what a run did and did not cover | An unexpected source means a run is not reproducible |
| T1.8 | `GET {{base}}/info` | `200`; `search_provider` `fake`, `makes_network_calls` `false`, `api_prefix` `/api/v1` | The composition root's choice is visible in development (D1.5); `/info` is absent in production ([T2.8](02-configuration-and-persistence.md#t28-production-refuses-to-start-half-configured)) | Another provider: `api/dependencies.py` changed |

### T1.9 Ruff catches a naive timestamp

- **Do:** in `services\api`, lint a snippet without creating a file:

```powershell
"from __future__ import annotations`n`nfrom datetime import datetime`n`nSTAMP = datetime.now()`n" | uv run ruff check --stdin-filename app/domain/scratch.py -
```

- **Expect:** one finding, `DTZ005`, saying `datetime.datetime.now()` was called without a `tz` argument and pointing at line 5; then `Found 1 error.`
- **Proves:** every timestamp must be timezone-aware; the domain also refuses naive datetimes at run time (for example `Session`, `AuditEntry`).
- **If it fails:** `DTZ` was removed from `[tool.ruff.lint] select`.

### T1.10 Ruff catches a relative import and a hard-coded password

- **Do:**

```powershell
"from __future__ import annotations`n`nfrom .paper import PaperRecord`n`nPASSWORD = `"x`"`n" | uv run ruff check --stdin-filename app/domain/scratch.py -
```

- **Expect:** three findings — `TID252` (prefer absolute imports over relative imports), `F401` (`.paper.PaperRecord` imported but unused) and `S105` (possible hard-coded password assigned to `PASSWORD`) — then `Found 3 errors.`
- **Proves:** the security (`S`) and import-style (`TID`) rules are active in application code.
- **If it fails:** the rule set or `ban-relative-imports = "all"` changed.

### T1.11 import-linter catches a layering violation

- **Do:** open `services\api\app\domain\query.py`, add a line `import sqlalchemy  # noqa: F401` under the other imports, save, then run `uv run lint-imports`. Afterwards delete the line again (or `git restore app/domain/query.py`) and run `uv run lint-imports` once more.
- **Expect:** first run: `Domain imports nothing outside the domain and the standard library BROKEN` and `Contracts: 3 kept, 1 broken.`, with the offending import named. Second run: `Contracts: 4 kept, 0 broken.`
- **Proves:** the layering is enforced by a tool, not by memory (D1.3). Ruff alone would not have caught it: the import is legal Python.
- **If it fails:** the contract no longer lists `sqlalchemy`, or `include_external_packages` was turned off.

### T1.12 Types and format

- **Do:** `uv run pyright`, then `uv run ruff format --check .`
- **Expect:** `0 errors, 0 warnings, 0 informations`; every file already formatted.
- **Proves:** the committed code type-checks (strictly in `domain/` and `application/`) and matches the formatter.
- **If it fails:** read the first error. Note: work in progress for later phases may add spec files that are deliberately red; judge only the files of the phase you are checking.

### T1.13 No secrets in the repository

- **Do:** `uv run pre-commit run gitleaks --all-files`, then `uv run pre-commit run detect-private-key --all-files`
- **Expect:** both `Passed`.
- **Proves:** nothing in the tracked files looks like a credential or a private key; `.env` and `secrets/` are ignored.
- **If it fails:** gitleaks names the file and line. If it is a real secret, rotate it — removing it from the file does not remove it from history.

### T1.14 The coverage gate

- **Do:** `uv run pytest --cov`
- **Expect:** the last lines read `Required test coverage of 85.0% reached. Total coverage: …%` (about 90% without services) and `268 passed, 23 skipped`.
- **Proves:** branch coverage is measured and enforced.
- **If it fails:** the table above the summary lists the uncovered lines per file.

### T1.15 The desktop version is really gone

- **Do:** from the repository root: `git grep -n -i -E "tauri|sidecar" -- . ":!docs/adr" ":!docs/checkpoints" ":!docs/walkthroughs"`
- **Expect:** no output.
- **Proves:** M1's done-when condition: nothing outside the decision records still assumes a desktop runtime.
- **If it fails:** a file still references V2; it is either stale documentation or code that should go.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T1.1 Compile | ☐ | |
| T1.2 Search | ☐ | |
| T1.3 No echo of input | ☐ | |
| T1.4 Strict vocabulary | ☐ | |
| T1.5 Blank term | ☐ | |
| T1.6 Bounded limit | ☐ | |
| T1.7 Sources | ☐ | |
| T1.8 Info | ☐ | |
| T1.9 Ruff DTZ | ☐ | |
| T1.10 Ruff TID and S | ☐ | |
| T1.11 import-linter | ☐ | |
| T1.12 pyright and format | ☐ | |
| T1.13 No secrets | ☐ | |
| T1.14 Coverage gate | ☐ | |
| T1.15 No V2 leftovers | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| OR within a theme, AND across themes | `tests/test_domain.py::TestCompilation`, `tests/test_api.py::TestCompile::test_renders_or_within_theme_and_and_across_themes` |
| A strict vocabulary gains no terms | `tests/test_domain.py::TestCompilation::test_strict_vocabulary_gains_no_terms` |
| Compilation is deterministic | `tests/test_domain.py::TestCompilation::test_compilation_is_deterministic` |
| Vocabularies are immutable; revising makes a new version | `tests/test_domain.py::TestVocabulary` |
| DOI wins for identity; no wrong merges | `tests/test_domain.py::TestPaperIdentity`, `TestDeduplicate` |
| The fake really evaluates queries and satisfies the Protocol without inheriting it | `tests/test_search.py::TestSearch`, `TestConformance` |
| Closed-access full text is `None`, not an error | `tests/test_search.py::TestFulltext::test_closed_paper_returns_none_rather_than_raising` |
| The provider receives the compiled query unmodified | `tests/test_search.py::TestSearch::test_provider_receives_the_compiled_query_unmodified` |
| Results travel with their query; AND excludes single-theme matches | `tests/test_api.py::TestSearch` |
| Swapping the provider changes only the data | `tests/test_api.py::TestProviderSwap` |
| Every endpoint is documented and tagged | `tests/test_api.py::TestOpenAPI` |
| Layering | `uv run lint-imports` (not a pytest test) |

```powershell
uv run pytest tests/test_domain.py tests/test_search.py tests/test_api.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G3 | The search endpoints are open to anyone | No per-project access control on searching yet | M10 (phase 07) |
| G4 | No SSRF guard for outbound requests | Not exploitable yet: the only source is in memory | M11 (phase 09), before any real paper source |
| G9 | No mutation testing, API fuzzing or SAST | Weak tests and some vulnerability classes could slip through | M4: mutmut, Schemathesis, Semgrep/CodeQL |
| G14 | Open-source files (CONTRIBUTING, SECURITY.md, code of conduct, templates) and CI missing | Contributors lack guidance; nothing checks pull requests automatically | M3, M4 |
| — | `VocabularyIn` and `SearchIn` do not set `extra="forbid"` (the account schemas do) | Unknown fields in a search body are ignored rather than refused | Tighten when the endpoints become project-scoped (M10) |
| — | Tests and coverage are not in the pre-commit hook | A commit can pass the hook with a failing test | CI (M4) runs the suite on every pull request |

## Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a short document recording a hard-to-reverse decision and why. |
| **ASGI** | The Python interface between async web servers (Uvicorn) and frameworks (FastAPI). |
| **BooleanQuery** | The compiled, source-agnostic structure of a search: themes AND'd, terms OR'd. |
| **Composition root** | The single place where implementations are chosen and wired together (`api/dependencies.py`). |
| **Fake** | A working, simplified implementation used in tests (unlike a mock, which only returns canned answers). |
| **Identity (paper)** | The key two records must share to be the same paper: DOI, else normalised title plus year. |
| **Import contract** | An import-linter rule saying which packages may not import which. |
| **Pre-commit hook** | A check git runs before recording a commit; a failure stops the commit. |
| **Property-based test** | A test that checks a property over many generated inputs (hypothesis). |
| **Protocol** | A Python type describing the methods a class must have, without inheritance (PEP 544). |
| **Seam** | A boundary where an implementation can be swapped: a Protocol and its implementations. |
| **SSRF** | Server-side request forgery: tricking the server into fetching internal addresses (gap G4, closed by `HttpFetcher` in phase 09). |
| **Strict vocabulary** | A `user_supplied` vocabulary, used exactly as given: no model, no expansion. |
| **Vocabulary** | Curated concepts with synonyms, grouped into themes; immutable once confirmed, versioned. |
