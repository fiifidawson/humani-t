# Checkpoint 1 — The secure foundation

This document is a checkpoint of humani-t after the security foundation was built (milestones M0 to M8, plus a concurrency and resource-safety pass). It has three jobs:

1. **Explain every architectural choice** made so far, the reasoning behind it, the alternatives that were rejected, and what each choice costs.
2. **Explain every library and tool**: what it does, why it was chosen, and its trade-offs.
3. **Give you a complete manual test guide** (Part J), so you can verify the system end to end yourself rather than taking anyone's word for it.

Commands are written for **PowerShell** on Windows. Paths are relative to the repository root, `C:\Users\fiifidawson\devdawson\GitHub\humani-t`, unless a step says otherwise.

How to read it: Parts A and B give the big picture in ten minutes. Part C (decisions) and Part D (libraries) are reference material: read them in full once, then come back to individual entries. Part J is the hands-on test guide. Part L lists what is known to be missing or broken.

---

## Contents

- [Part A — Where we are](#part-a--where-we-are)
- [Part B — The big picture](#part-b--the-big-picture)
- [Part C — Architectural decisions](#part-c--architectural-decisions)
- [Part D — Libraries and tools](#part-d--libraries-and-tools)
- [Part E — Repository tour](#part-e--repository-tour)
- [Part F — Data model](#part-f--data-model)
- [Part G — HTTP API reference](#part-g--http-api-reference)
- [Part H — Configuration reference](#part-h--configuration-reference)
- [Part I — Security model](#part-i--security-model)
- [Part J — Manual end-to-end test guide](#part-j--manual-end-to-end-test-guide)
- [Part K — Troubleshooting](#part-k--troubleshooting)
- [Part L — Known gaps and what comes next](#part-l--known-gaps-and-what-comes-next)
- [Part M — Glossary](#part-m--glossary)

---

## Part A — Where we are

### A.1 Milestone status

| Milestone | Status | Notes |
| --- | --- | --- |
| M0 Commit the existing work | ✅ Done | Session 1–2 search skeleton committed; `main.py` repaired |
| M1 Strip the desktop (V2) version | ✅ Done | [ADR-0001](adr/0001-web-only-self-hosted.md) |
| M2 Python quality gates | ✅ Done | ruff, pyright, import-linter, coverage, hypothesis, pre-commit |
| M3 Open-source files | 🟡 Partial | README only; CONTRIBUTING, SECURITY.md, code of conduct, templates missing |
| M4 Containers and CI | 🟡 Partial | Development Compose only; no Dockerfile, Caddy or GitHub Actions |
| M5 Configuration and persistence | ✅ Done | Settings, storage seam, Postgres, migrations, middleware |
| M6 Envelope encryption | ✅ Done | Per-project keys, crypto-shredding, key rotation |
| M7 Audit log | ✅ Done | Hash chain, append-only, verifiable |
| M8 Accounts and sessions | ✅ Done | Plus the concurrency and resource-safety hardening |
| M9 Multi-factor authentication | ⬜ Next | Passkeys, TOTP, recovery codes |
| M10 Projects and roles | ⬜ | Owner, reviewer, viewer |
| M11 SSRF-guarded outbound HTTP | ⬜ | |
| M12 Optional OIDC single sign-on | ⬜ | |
| M13 Web UI | ⬜ | React, strict TypeScript |
| M14 Security documentation | 🟡 Partial | Control map done; threat model to do |

Rough completion: **about 44% of the foundation plan**, and **about 15% of the whole product** in the brief (the review workflow itself — search, masking, screening, Q&A — has not started). Treat both as ±10 points.

### A.2 What works end to end today

- An administrator is created from the command line, signs in, and invites people by email.
- An invited person registers from the emailed link and is signed in.
- Sessions live on the server; users can list them, sign out one device, or sign out everywhere else.
- Passwords can be changed (with re-confirmation after 15 minutes) and reset by email.
- Accounts can be disabled by an administrator.
- Every sign-in, failure, sign-out and credential change is written to a tamper-evident audit log.
- Brute-force attempts are throttled per account, per client and per email address.
- Cross-site request forgery is blocked in three independent ways.
- Per-project encryption keys exist and can be destroyed (crypto-shredding) and rotated, ready for projects in M10.
- The original search skeleton still works: compiling a vocabulary into a Boolean query and searching an in-memory corpus.

### A.3 What is deliberately not built yet

Multi-factor authentication, projects and roles, the web UI, real paper sources, document upload, masking, screening, indexing, Q&A, production containers and CI. Each is planned; none is half-built.

### A.4 Numbers

- **291 automated tests**, all passing, including tests against real PostgreSQL and real Valkey.
- **94% branch coverage** (the gate is 85%).
- **0 errors** from ruff (lint and format), pyright (types) and import-linter (layering).

---

## Part B — The big picture

### B.1 What humani-t is

A reviewer asks a research question. humani-t helps turn it into a search, finds papers, masks personal data, screens papers against criteria with a language model, and answers follow-up questions with cited sources. It is for humanitarian teams, so the data it holds can be sensitive: names, health details, locations. The whole design follows from that.

### B.2 The one rule and the second test

From [`CLAUDE.md`](../CLAUDE.md):

> humani-t is a self-hosted web application. Data leaves the deployment only to destinations the operator has configured, and only after it has been masked.

> Could a reviewer reconstruct why this paper was included or excluded, six months from now, from what we stored?

The first rule drives the security design. The second drives provenance: decisions are recorded with what produced them, at the moment they are made.

### B.3 Deployment shape

**Today (development):**

```text
your machine
├── uvicorn (the API, run with uv)          http://127.0.0.1:8731
└── Docker Compose
    ├── postgres:18-alpine                  127.0.0.1:55432
    ├── valkey/valkey:8-alpine              127.0.0.1:56379
    └── axllent/mailpit:v1.27               SMTP 127.0.0.1:51025, inbox http://127.0.0.1:58025
```

**Target (M4, production self-hosting):**

```text
internet ─► caddy (HTTPS, serves the web app, proxies /api)
               └─► api (FastAPI) ─► postgres, valkey
                   worker (background jobs, scheduled purge and audit checks)
                   migrate (one-shot, runs Alembic as the schema owner)
```

Only Caddy will publish a port. Every other service stays on an internal network.

### B.4 The layers

```text
app/api/          HTTP boundary — routes, Pydantic schemas, middleware, error mapping
app/application/  use cases — orchestrate domain rules and providers, own transactions
app/domain/       entities and rules — plain Python, no I/O, no libraries
app/<seam>/       provider Protocols (base.py) and their implementations
                  storage/  crypto/  auth/  email/  ratelimit/  search/
```

Dependencies point **downward only**. The domain imports nothing but the standard library and itself. The application layer imports the domain and provider *Protocols*, never an implementation. Only `app/api/dependencies.py` chooses implementations. This is enforced by **import-linter** on every commit, not by discipline (see C.3).

### B.5 The life of a sign-in request

Following `POST /api/v1/auth/login` through the system shows how the pieces fit. Middleware runs outermost first:

1. **`RequestIdMiddleware`** gives the request an id (or keeps a safe one from the proxy), binds it to every log line, and echoes it in the `x-request-id` response header.
2. **`SecurityHeadersMiddleware`** wraps the response so that whatever happens below, it leaves with CSP, HSTS, `nosniff`, `no-store` and the rest.
3. **`BodySizeLimitMiddleware`** refuses bodies over 1 MiB with `413` before reading them.
4. **`CrossSiteRequestMiddleware`** refuses the write with `403` if `Origin` or `Sec-Fetch-Site` show it came from another site, and with `415` if the body is not JSON.
5. **FastAPI routing and Pydantic** validate the body into `LoginIn`: a valid email address and a password of at most 4096 characters. Failures become `422` problem documents that never echo the input.
6. **The route** (`routes/auth.py`) calls `AccountService.login` and, on success, sets the two cookies.
7. **`AccountService.login`** (`application/accounts.py`):
   1. counts the attempt atomically for the client IP and for the account, *before* any slow work, and refuses with `ThrottledError` (`429`) over the limit;
   2. opens a **short transaction** to read the user, then closes it;
   3. verifies the password with Argon2 **in a worker thread, with no database connection held** — against a dummy hash if the account does not exist, so both paths cost the same;
   4. on failure, writes an `auth.login_failed` audit event in its own short transaction and raises `InvalidCredentialsError` (`401`);
   5. on success, opens a second short transaction, re-reads the user to make sure nothing changed meanwhile, creates the session, writes the `auth.login` audit event **last**, and commits;
   6. resets the account's attempt counter.
8. **Error handlers** (`api/errors.py`) turn any domain error into the right status and an RFC 9457 problem document.

### B.6 The life of a signed-in write

For `POST /api/v1/account/password`:

1. The same middleware chain runs.
2. The **`get_current` dependency** (`api/session.py`) reads the `__Host-session` cookie, resolves it in a short transaction (`authenticate`), extends the idle timer, and returns the caller. No cookie or a dead session gives `401`.
3. Because the method changes state, `get_current` also checks that the `X-CSRF-Token` header matches the session's CSRF token. A mismatch gives `403`.
4. The service checks the sign-in is recent (15 minutes), verifies the current password and hashes the new one **outside any transaction**, then in one short transaction re-checks the stored hash, saves the user with a version check, revokes all sessions, starts a fresh one and writes the audit event last. The notification email goes out **after** the commit.

---

## Part C — Architectural decisions

Each entry gives the decision, why, what was rejected, what it costs, where it lives in the code, and what proves it.

### C.1 A self-hosted web application only

- **Decision:** one deployment shape, run by each organisation on infrastructure it controls, via Docker Compose. The planned desktop app (V2) is dropped. Recorded in [ADR-0001](adr/0001-web-only-self-hosted.md).
- **Why:** keeping a desktop version possible shaped every decision (a `BackupProvider` nobody used, a "will it run on a laptop" rule) and doubled what had to be built and secured. Self-hosting still keeps data inside the organisation.
- **Rejected:** a hosted SaaS (the organisation would have to trust us with sensitive data); a desktop app (double the surface); Render as the default host (vendor-specific).
- **Costs:** the data always lives on a server, so encryption, access control and audit carry the privacy claim.

### C.2 One repository for everything

- **Decision:** a monorepo: `services/api` (Python), `apps/web` (frontend, planned), `infrastructure/`, `docs/`.
- **Why:** an API change and the frontend change that uses it land in one commit; one place to review. The generated TypeScript types (M13) will make a backend contract change break the frontend build.
- **Rejected:** separate repositories (contract drift between them); the earlier shared `packages/` folders (nothing to share with one frontend).
- **Costs:** two toolchains (uv for Python, pnpm for TypeScript) in one place.

### C.3 Layered architecture, enforced by a tool

- **Decision:** API → application → domain, with providers beside them, dependencies pointing downward only. Four import-linter contracts in `pyproject.toml` enforce it.
- **Why:** the domain rules (what a valid vocabulary is, when a session expires) are the part most worth testing and least worth coupling to frameworks. A rule written in prose is broken eventually; a contract checked on every commit is not.
- **Rejected:** "routes call the database directly" (untestable, and nothing stops a route skipping a check); relying on code review alone.
- **Costs:** more files and some indirection; a new package must be added to the contracts.
- **Proven by:** `uv run lint-imports` (4 contracts kept).

### C.4 Provider seams as Protocols, with fakes

- **Decision:** each outside dependency (storage, encryption keys, password hashing, email, rate limiting, paper search) sits behind a `typing.Protocol` in `<seam>/base.py`, with a real implementation and a fake.
- **Why:** use cases are tested without a network or database, and an organisation can swap a provider (another key store, mail server or model) by changing one file. Protocols (PEP 544) need no inheritance, so implementations stay plain classes.
- **Fakes over mocks:** a fake really behaves (the in-memory storage really has transactions; the fake hasher really distinguishes passwords). A mock that returns canned values makes tests pass without proving anything.
- **Rejected:** abstract base classes (force inheritance); mocking libraries everywhere.
- **Costs:** every seam needs two implementations, and the fake must not drift from the real one — hence the contract suites (C.12).

### C.5 One composition root and an app factory

- **Decision:** `app/api/dependencies.py` (`build_providers`) is the **only** place concrete implementations are chosen. `app/main.py` (`create_app(settings, providers=…)`) builds the app from them.
- **Why:** swapping an implementation is a one-file change, and tests build an app with whatever settings or fakes they need, without patching globals.
- **Rejected:** module-level singletons; choosing implementations inside routes.
- **Costs:** tests pass providers explicitly; `build_providers` grows as seams are added.

### C.6 Pydantic only at the boundaries

- **Decision:** Pydantic appears in exactly two places: `app/api/schemas/` (HTTP input and output) and `app/settings.py` (environment). The domain uses frozen dataclasses that validate themselves.
- **Why:** Pydantic checks **shape** ("is this an email address?"); the domain checks **meaning** ("is this password acceptable for this user?"). Keeping Pydantic out of the domain means the wire format and configuration never leak into the rules.
- **Rejected:** Pydantic models as domain entities (couples rules to the HTTP format).
- **Costs:** a conversion step (`to_domain`, `from_domain`) at the boundary.

### C.7 Configuration and secrets

- **Decision:** `Settings` reads `HUMANI_T_*` environment variables and, in production, Docker secret files under `/run/secrets`. Unknown settings are refused. Production refuses to start without a database, encryption keys, Valkey, a mail server and an `https://` origin. A database without encryption keys is refused in every environment. Settings never read `.env` themselves; development loads it explicitly with `uv run --env-file .env`.
- **Why:** fail closed — a misconfigured production must not start "mostly working". Docker secrets keep passwords out of environment variables (which leak into process listings and crash reports). Explicit `.env` loading means the test suite can never pick up a developer's database by accident.
- **Rejected:** `.env` auto-loading; secrets in Compose environment blocks; defaults that silently work in production.
- **Costs:** one more flag in development commands.
- **Proven by:** `test_settings.py`.

### C.8 Domain objects are immutable and valid by construction

- **Decision:** entities are `@dataclass(frozen=True, slots=True)` that check themselves in `__post_init__` (for example, timestamps must be timezone-aware; an email must be normalised). Changes produce new objects (`user.disabled()`, `session.revoked(at)`).
- **Why:** an invalid object can never exist, so code receiving one need not re-check it. Immutability makes concurrent code easier to reason about.
- **Costs:** "updates" are copies; mapping to and from database rows is explicit.

### C.9 Unit of Work, repositories, and services that own transactions

- **Decision:** persistence is exposed as repositories (`users`, `sessions`, `audit`…) on a unit of work (one transaction). Each account use case opens its **own short transactions**, following the shape: short read → slow work with no connection held → short race-safe write with the audit event last → side effects after commit.
- **Why:** this is the Unit of Work pattern from *Architecture Patterns with Python* (ch. 6). Owning transactions inside the service is what makes it possible to release the database connection while Argon2 runs, which keeps a burst of sign-ins from exhausting the pool.
- **Rejected:** a transaction per HTTP request (holds a connection for the whole request, including hashing and email); an implicit session object passed everywhere.
- **Costs:** a use case has more steps, and the second transaction must re-check anything that could have changed since the first (it does).

### C.10 SQLAlchemy Core, not the ORM

- **Decision:** tables are declared with SQLAlchemy Core (`storage/postgres/tables.py`); repositories map rows to domain dataclasses by hand.
- **Why:** the domain types are frozen dataclasses; the ORM wants mutable, instrumented classes and would pull SQLAlchemy into the domain. Core keeps SQL explicit, which matters for the conditional updates in C.29.
- **Rejected:** the ORM; raw SQL strings (no composition, easy to make injection mistakes).
- **Costs:** more mapping code than the ORM would need.

### C.11 Two database roles, migrations, and a separate test database

- **Decision:** `humani_t_migrator` owns the schema and is used only by Alembic. `humani_t_app` can read and write rows but cannot create, alter or drop anything, and cannot update or delete audit events. Migrations live in `services/api/migrations/` and are the source of truth for the schema. Contract tests use a separate database, `humani_t_test`.
- **Why:** if the API process is compromised, the attacker cannot change the schema or rewrite history. Tests delete what they write, so they must never point at development data (they once did, during this checkpoint's work, and wiped the dev accounts).
- **Rejected:** one superuser for everything; creating tables at app start-up.
- **Costs:** two connection strings and a migration step.
- **Proven by:** `TestPostgresAuditGuarantees` and the manual checks in J.19.

### C.12 In-memory storage plus contract tests

- **Decision:** an in-memory implementation of every repository for fast tests, and contract suites (`test_storage_contract.py`, `test_ratelimit_contract.py`) that run the **same** tests against memory and against real Postgres/Valkey.
- **Why:** fast tests most of the time, and proof that the fake behaves like the real thing.
- **Limit to know:** the in-memory storage is not truly concurrent (last commit wins). Concurrency guarantees are proven by the **Postgres** runs, not the memory ones.

### C.13 Envelope encryption

- **Decision:** each scope (a project, from M10) has its own random 256-bit **data key**. Data is encrypted with it using AES-256-GCM. The data key is stored only **wrapped** (encrypted) by a **key-encryption key (KEK)** that the operator holds as a secret. Each ciphertext is bound by associated data to its scope, purpose and record, so it cannot be copied to another row and still decrypt.
- **Why:** this is how cloud KMS systems work. It gives per-project deletion (C.14), cheap key rotation (only the small data keys are re-wrapped, not the data), and the database alone is useless to a thief.
- **Format:** version byte (1) + 96-bit random nonce (12 bytes) + ciphertext and tag. The version byte lets the algorithm change later without guessing.
- **Rejected:** one key for everything (no per-project deletion; rotation means re-encrypting everything); database-level transparent encryption only (protects disks, not a stolen database dump).
- **Costs:** the KEK becomes the most important secret; losing it loses all data.
- **Proven by:** `test_encryption.py`.

### C.14 Crypto-shredding, and its limits

- **Decision:** deleting a project destroys its data key (`wrapped` set to null, `destroyed_at` recorded). Every ciphertext for that project, wherever copies exist, becomes permanently unreadable. A destroyed key can never be written again, even by a racing key rotation.
- **Why:** brief rule 17 requires complete deletion, including from places that are hard to find and from the append-only audit log.
- **Limit:** database **backups** still contain the wrapped key until they expire. Deletion is complete only after the backup retention window; the deployment guide (M14) must say so.
- **Proven by:** `TestScopeEncryption::test_shredded_scope_is_permanently_unreadable`, `TestShreddingIsFinal`.

### C.15 A hash-chained, append-only audit log

- **Decision:** each event stores the SHA-256 of the previous event's hash plus its own canonical content (sorted-key JSON). Appends lock a single chain-head row so concurrent writers cannot fork the chain. The app role cannot update or delete events, and a trigger refuses those operations for every role. Event rows hold only identifiers; descriptive details are encrypted with the project's key. `python -m app.cli verify-audit` re-checks the whole chain.
- **Why:** brief rule 16. Editing, deleting or reordering any event breaks every hash after it, and the verifier reports where. Encrypting details per project lets a shredded project's history stay verifiable without staying readable.
- **Limit:** a superuser with full database access could rewrite the entire chain consistently. The planned fix is periodically anchoring the chain head outside the database (an HMAC-signed head written to logs by the worker).
- **Proven by:** `test_audit.py`, `TestPostgresAuditGuarantees`.

### C.16 Password rules from NIST SP 800-63B rev 4

- **Decision:** at least 15 characters, at most 1024; no composition rules ("must include a symbol"); no forced rotation; refused if common, a single repeated character, a keyboard or counting run, or built from the user's own email or name. Passwords are normalised (Unicode NFKC) before hashing.
- **Why:** NIST's current guidance: length and screening work; composition rules and rotation make passwords more predictable. 15 is the minimum when a password may be the only factor (it is, until MFA in M9).
- **Costs:** the built-in screening list is small; a deployment can pass a larger one. Users used to "8 characters with a symbol" may be surprised.
- **Proven by:** `test_passwords.py`.

### C.17 Argon2id, off the event loop, with a concurrency cap

- **Decision:** Argon2id with RFC 9106's second recommended profile (64 MiB memory, 3 passes, 4 lanes, 16-byte salt, 32-byte hash). Hashing runs in a worker thread (`asyncio.to_thread`), at most 4 at a time. A hash made with weaker parameters is upgraded at the next successful sign-in.
- **Why:** Argon2id won the Password Hashing Competition and is OWASP's first recommendation; memory-hardness defeats GPU cracking. Running it in a thread keeps one slow hash from freezing every other request. The cap stops a burst of sign-ins using `64 MiB × n` of memory.
- **Rejected:** bcrypt (not memory-hard, 72-byte input limit); PBKDF2 (fast on GPUs); scrypt (fine, but Argon2id is the current recommendation).
- **Costs:** each sign-in costs tens of milliseconds and 64 MiB briefly — that is the point.

### C.18 Accounts cannot be enumerated

- **Decision:** a wrong password and an unknown account get the same `401` and the same body, and both perform one password verification (unknown accounts verify against a dummy hash). Reset requests always return the same `202`. Audit rows for unknown accounts record `unknown`, never the typed email.
- **Why:** an attacker must not learn which emails have accounts, from responses or from timing.
- **Proven by:** `TestLogin::test_unknown_and_known_accounts_are_indistinguishable`, `…_still_costs_a_password_verification`. (That second test caught a regression during the concurrency refactor, where Python's `or` short-circuited past the verification for unknown accounts.)

### C.19 Throttling, not lockout, counted before the slow check

- **Decision:** sign-in attempts are limited to 10 per account and 100 per client IP per 15 minutes; reset requests to 5 per email per 15 minutes. Each attempt is counted atomically **before** the password is verified; a successful sign-in clears the account's count. While throttled, even the correct password gets `429`, with `Retry-After`. Refused attempts are not counted, so a flood cannot extend the block forever.
- **Why:** lockout lets an attacker lock real users out on purpose; throttling slows guessing without that. Counting before verifying closes the race where 30 parallel guesses all pass a check-then-count gap. Refusing the correct password while throttled stops the throttle itself from confirming a guess.
- **Proven by:** `TestThrottling`, `TestThrottleUnderConcurrency`, `test_ratelimit_contract.py`.

### C.20 Invite-only registration and an operator-created first admin

- **Decision:** there is no public sign-up. An administrator invites by email; the invitation link (7 days, single use) is the proof of email ownership. The first administrator is created with `python -m app.cli create-admin`, reading the password from standard input. The CLI validates emails exactly as the API does.
- **Why:** a humanitarian deployment should not accept strangers. A public "create first admin" endpoint would be a race: whoever finds a fresh install first becomes admin.
- **Proven by:** `TestInvitations`, `test_cli.py::TestCreateAdmin`.

### C.21 Server-side sessions, not JWTs

- **Decision:** a session is a row in Postgres. The browser holds an opaque 256-bit random token; the database holds only its SHA-256.
- **Why:** a session can be listed, revoked instantly and audited. A JWT cannot be revoked before it expires without building a server-side list anyway. Hashing the token means a copy of the database cannot be used to sign in as anyone. A plain SHA-256 is enough because the token already has 256 bits of randomness.
- **Rejected:** JWTs in local storage (readable by any injected script); JWTs in cookies (still not revocable).
- **Costs:** one database read and one small write per authenticated request.

### C.22 Cookie design

- **Decision:** `__Host-session` (`Secure; HttpOnly; SameSite=Lax; Path=/`, no `Domain`) carries the session token. `__Host-csrf` (`Secure; SameSite=Strict; Path=/`, readable by scripts) carries the CSRF token. Neither has `Max-Age`, so both end with the browser session.
- **Why:** the `__Host-` prefix makes browsers refuse the cookie unless it is Secure, path `/` and host-only, so no subdomain can set or overwrite it. `HttpOnly` keeps the session token away from scripts. `Lax` (not `Strict`) on the session cookie keeps future single sign-on redirects working.
- **Proven by:** `TestSessionCookie`.

### C.23 Session lifetimes

- **Decision:** 1 hour idle, 24 hours absolute, a new token at every sign-in (no session fixation), and sensitive actions require a sign-in within 15 minutes (`POST /auth/reauthenticate` refreshes it). Password change or reset ends every other session.
- **Why:** NIST 800-63B rev 4 at assurance level 2.
- **Proven by:** `test_sessions.py`, `TestCurrentSession`, `TestChangePassword`.

### C.24 Cross-site request forgery: three independent layers

- **Decision:**
  1. state-changing requests whose `Sec-Fetch-Site` is not `same-origin`, or whose `Origin` is not exactly the public origin, are refused;
  2. every signed-in write must send `X-CSRF-Token` matching the session's token;
  3. request bodies must be `application/json`.
- **Why:** any one layer stops a forged request on its own. An HTML form on another site cannot send JSON or custom headers, and cannot read the CSRF cookie.
- **Non-browser clients:** a request with neither `Origin` nor `Sec-Fetch-Site` (curl, Postman) passes layer 1, because forgery needs a victim's browser. Layer 2 still applies after sign-in.
- **Proven by:** `test_csrf.py`.

### C.25 Security headers

- **Decision:** every response carries `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`, a restrictive `Permissions-Policy`, and `Cache-Control: no-store`. The development-only Swagger page is exempt from the CSP alone, because it loads scripts from a CDN.
- **Why:** API responses are data, never documents: nothing in them should load, frame or run. `no-store` keeps project data out of browser and proxy caches.
- **Proven by:** `TestSecurityHeaders`.

### C.26 Errors as problem documents

- **Decision:** every error is an RFC 9457 `application/problem+json` document. Validation errors list where the problem is, but never echo the submitted value. Unexpected errors say nothing about the cause; the request id ties them to the logs.

| Domain error | Status |
| --- | --- |
| `InvalidCredentialsError`, `ReauthenticationRequiredError` (adds `"reauthentication_required": true`) | 401 |
| `CurrentPasswordIncorrectError`, `ForbiddenError`, missing CSRF token, cross-site request | 403 |
| `NotFoundError` | 404 |
| `InvalidTokenError` | 400 |
| `EmailTakenError`, `ConcurrentUpdateError` | 409 |
| Body too large / not JSON | 413 / 415 |
| `PasswordRejectedError` (adds `violations`) | 422 |
| `ThrottledError` (adds `Retry-After`) | 429 |
| `EmailDeliveryError` (invitations) | 502 |
| `StorageUnavailableError` (adds `Retry-After: 5`) | 503 |

- **Why:** one shape for clients to handle, and one place deciding what an error may reveal. Echoing input could reflect the very personal data the user was trying to send.

### C.27 Request ids and structured, redacted logs

- **Decision:** structlog writes JSON in production and readable lines in development, with a timestamp, level and request id on every line. A processor redacts any field whose name looks like a secret or personal data (password, token, cookie, email, session…).
- **Why:** logs are often shipped to third parties and kept long; redaction in the pipeline catches careless logging at call sites.
- **Gap:** redaction has no automated test yet (see Part L).

### C.28 Bounded request bodies

- **Decision:** bodies over 1 MiB are refused with `413` — immediately if `Content-Length` says so, or as soon as a streamed body passes the limit.
- **Why:** one huge request must not be read entirely into memory. Caddy will enforce a limit too (M4); this one holds if the app is ever reached directly.

### C.29 Rules for concurrency and resources

Written in [`architecture.md` §5.7](architecture.md) and enforced by `test_concurrency_and_resources.py` and the contract suites:

1. Never hold a database connection across slow work (hashing, email, outbound HTTP).
2. Side effects (email) happen after commit.
3. The audit event is the last write in a transaction, keeping the chain lock brief and the lock order consistent (no deadlocks).
4. No blind overwrites of shared records: writes are conditional ("only if still unused/unrevoked") or version-checked (`users.version`, optimistic locking). A lost update raises `ConcurrentUpdateError` (`409`).
5. Single-use tokens are spent with one conditional `UPDATE`; of two simultaneous uses, exactly one wins.
6. Count rate-limited attempts before the slow check.
7. Bounded pools that fail fast with `503`; server-side statement and idle-transaction timeouts.
8. Bounded memory: expiring in-process structures, the body limit, `purge-expired`.
9. Everything opened is closed: `Providers.close()`.

**Why:** every one of these was a real defect found in review: a sign-in burst could exhaust the pool; a late "last seen" update could un-revoke a session; a racing login could re-enable a disabled account; a single-use link could be used twice; a key rotation could revive a shredded project.

### C.30 Connection pool and timeouts

- **Decision:** 10 connections per API process, **no overflow**, a 5-second wait, then `StorageUnavailableError` (`503`). Connections are checked before use (`pool_pre_ping`) and recycled every 30 minutes. Postgres cancels statements over 15 seconds and ends transactions left idle over 30 seconds.
- **Why:** the database sees a predictable number of connections; overload is reported, not queued forever; a bug that leaves a transaction open cannot hold locks indefinitely.
- **Proven by:** `TestPostgresConcurrency`.

### C.31 Rate limiter design

- **Decision:** a sliding-window log: each key is a sorted set of event timestamps in Valkey; the check-and-add runs as one Lua script, so two API workers cannot both admit the last allowed attempt. In development and tests, an in-memory version with the same behaviour is used, and it sweeps expired keys so its memory stays bounded.
- **Why:** sliding windows avoid the burst-at-the-boundary problem of fixed windows. Lua scripts run atomically inside Valkey.
- **Rejected:** fixed windows (bursts); in-process limits in production (each worker would count separately); slowapi (decorator-based, hard to key by account).
- **Proven by:** `test_ratelimit_contract.py`, including 50 concurrent hits against real Valkey never exceeding the limit.

### C.32 Email

- **Decision:** SMTP through `aiosmtplib`; Mailpit catches all mail in development. Links carry their token after `#` (`/reset-password#token=…`). Emails are sent after the commit. Reset and password-change notices are sent "quietly" (a mail failure is logged, not shown); invitations are sent "loudly" (a failure returns `502` so the admin knows).
- **Why:** browsers never send the part after `#` to a server, so tokens cannot leak into server or proxy logs or `Referer` headers. A quiet reset email keeps a mail outage from revealing whether an account exists.

### C.33 An operator command line

- **Decision:** `python -m app.cli` with `create-admin`, `generate-kek`, `rewrap-keys`, `verify-audit`, `purge-expired`.
- **Why:** these actions must not be reachable over the internet. Needing shell access to the server is a stronger guard than any endpoint protection. The worker (M4) will run `verify-audit` and `purge-expired` on a schedule.

### C.34 Tests are the specification

- **Decision:** for every feature, tests are written first as plain-language requirements traced to the brief, NIST or OWASP; they are reviewed while failing; then the code is written until they pass. Tests are never weakened to pass. The spec and its implementation are committed together, so every commit passes.
- **Test architecture:** sentence-style test names; plain classes for grouping; fakes rather than mocks; a controllable `FakeClock` for expiry; instrumented storage, hasher and email that record whether a transaction is open when slow work happens; contract suites against real Postgres and Valkey, enabled by `TEST_DATABASE_URL` and `TEST_VALKEY_URL`.
- **Why:** the tests are the most reviewable statement of what the system must do. They catch regressions (several were caught this way).

### C.35 Documentation lives with the code

- **Decision:** decisions that are hard to reverse get an ADR in `docs/adr/`. Security controls are mapped to their tests in [`docs/security/controls.md`](security/controls.md). Markdown is not hard-wrapped.
- **Why:** a reviewer can trace any claim to a test, and history explains itself.

### C.36 Git workflow

- **Decision:** `main` stays releasable; work happens on short-lived `<type>/<topic>` branches and lands by pull request. Commits follow Conventional Commits (`feat(auth): …`). A pre-commit hook runs the same checks as CI before every commit. You review and commit by hand.
- **Why:** small, readable history; checks run before broken code reaches anyone.

### C.37 Windows specifics

- `.gitattributes` stores text with LF line endings, so Windows checkouts do not produce noisy diffs.
- Ports avoid common clashes: the API on 8731 (not 8000, which `mkdocs serve` uses and Windows lets both bind silently), Postgres on 55432, Valkey on 56379, Mailpit on 51025/58025, all bound to `127.0.0.1` only.
- Commands in guides are PowerShell 5.1-compatible (no `&&`).

---

## Part D — Libraries and tools

Versions are those locked in `services/api/uv.lock` at this checkpoint.

### D.1 Runtime libraries (Python)

#### FastAPI 0.141 (with Starlette 1.6)

- **What it does:** the web framework. Declares routes as Python functions with type hints, validates requests with Pydantic, injects dependencies, and generates the OpenAPI description behind `/docs`. Starlette underneath provides the ASGI request/response machinery, middleware and the test client.
- **Why:** async-native, type-hint driven, and the OpenAPI it generates becomes the contract for the TypeScript frontend (M13). Dependency injection is how the composition root reaches routes.
- **Alternatives:** Django (batteries included, but synchronous-first and ORM-centric); Flask (synchronous, no built-in validation or OpenAPI); Litestar (similar, smaller community).
- **Trade-offs:** fast-moving; dependency injection can hide control flow if overused; we keep business logic out of routes to compensate.

#### Uvicorn 0.52 (`uvicorn[standard]`)

- **What it does:** the ASGI server that runs the app. The `standard` extra adds uvloop (a faster event loop, on Linux) and httptools (a faster HTTP parser).
- **Why:** the standard server for FastAPI; small and well understood.
- **Alternatives:** Hypercorn (HTTP/2 and HTTP/3); Granian (Rust-based).
- **Trade-offs:** speaks HTTP/1.1 only, which is fine behind Caddy, which handles HTTPS and HTTP/2 to the browser.

#### Pydantic 2.13

- **What it does:** validates data against type hints and serialises it. Used for request/response schemas.
- **Why:** FastAPI is built on it; the Rust core makes it fast; strict modes and `extra="forbid"` reject unexpected fields.
- **Alternatives:** msgspec (faster, less ecosystem); attrs + cattrs.
- **Trade-offs:** powerful enough to tempt use everywhere; confined to the boundaries by rule (C.6).

#### pydantic-settings 2.16

- **What it does:** builds a settings object from environment variables and secret files, with the same validation as Pydantic.
- **Why:** typed, validated configuration with Docker secret support (`secrets_dir`) and cross-field rules (production requirements).
- **Alternatives:** python-decouple, dynaconf, plain `os.environ`.
- **Trade-offs:** another Pydantic dependency; behaviour around `.env` files needs care (we load `.env` explicitly instead).

#### email-validator 2.3

- **What it does:** checks email address syntax and refuses special-use domains (`.local`, `.test`…); returns a normalised form. Pydantic's `EmailStr` uses it.
- **Why:** email syntax is genuinely hard; a regex gets it wrong. The CLI uses it directly with the same settings, so an address the CLI accepts is one the API accepts.
- **Trade-offs:** it can optionally check DNS (deliverability); that is turned off, because it would mean a network call per validation.

#### SQLAlchemy 2.1 (`sqlalchemy[asyncio]`)

- **What it does:** the database toolkit. We use its **Core** layer to declare tables and build SQL with Python expressions, its async engine, and its connection pool.
- **Why:** mature, explicit, database-independent SQL construction that cannot be injected; a well-tested pool with pre-ping, recycling and timeouts.
- **Alternatives:** the SQLAlchemy ORM (see C.10); SQLModel (ORM-centric); raw asyncpg (no composition, more error-prone).
- **Trade-offs:** large and with a learning curve; the asyncio extra needs greenlet under the hood.

#### asyncpg 0.32

- **What it does:** the PostgreSQL driver: speaks the Postgres wire protocol directly, asynchronously.
- **Why:** the fastest Python Postgres driver; lets us set server-side settings (statement and idle timeouts) per connection.
- **Alternatives:** psycopg 3 (also async, closer to the DB-API standard).
- **Trade-offs:** Postgres-only (fine: Postgres is the only target); not DB-API compliant, so SQLAlchemy adapts it.

#### Alembic 1.20

- **What it does:** database migrations: versioned scripts that move the schema forward (and back), recorded in the `alembic_version` table.
- **Why:** the standard companion to SQLAlchemy; migrations run as the schema-owner role, separately from the app.
- **Trade-offs:** autogenerated migrations need review; we write them by hand and keep `tables.py` in step.

#### cryptography 50

- **What it does:** cryptographic primitives backed by OpenSSL. We use AES-GCM (authenticated encryption) and secure key generation.
- **Why:** the de facto standard Python cryptography library, maintained by the Python Cryptographic Authority, with a deliberately hard-to-misuse API.
- **Alternatives:** PyNaCl/libsodium (excellent, different primitives); PyCryptodome (more footguns).
- **Trade-offs:** binary wheels; regular security releases must be kept up to date (Dependabot, M4).

#### argon2-cffi 25.1

- **What it does:** Argon2 password hashing via the reference C implementation; produces self-describing hash strings that record their parameters, and reports when a hash needs upgrading.
- **Why:** the reference binding, recommended by OWASP; `check_needs_rehash` enables silent parameter upgrades.
- **Alternatives:** passlib (wraps many algorithms, but is unmaintained); bcrypt (see C.17).
- **Trade-offs:** CPU and memory cost is deliberate; it must run off the event loop (it does).

#### redis-py 8.1 (`redis`)

- **What it does:** client for Redis-protocol servers. We use it against **Valkey** for the shared rate limiter, including registering and running the Lua script.
- **Why:** the standard, maintained client, with asyncio support and script helpers; Valkey speaks the same protocol.
- **Trade-offs:** the server itself is Valkey, not Redis (see D.3), which is a deliberate licence choice.

#### aiosmtplib 5.1

- **What it does:** sends email over SMTP from async code, with STARTTLS and authentication.
- **Why:** the standard library's `smtplib` blocks the event loop; this does not.
- **Alternatives:** an HTTP mail API (SendGrid, SES): a vendor dependency and data leaving the deployment, which the one rule discourages.
- **Trade-offs:** one connection per message; fine at our volume. TLS paths are not yet tested (Part L).

#### structlog 26.1

- **What it does:** structured logging: log lines are key–value events passed through a pipeline of processors (timestamp, level, request id, redaction) and rendered as JSON or text.
- **Why:** machine-readable logs and a single place (the pipeline) to redact secrets.
- **Alternatives:** the standard `logging` module with a JSON formatter; loguru.
- **Trade-offs:** another logging layer to learn alongside the standard library's.

### D.2 Development libraries and tools (Python)

| Tool | What it does | Why | Trade-offs |
| --- | --- | --- | --- |
| **pytest 9.1** | Test runner with fixtures, parametrisation and plain `assert` | The Python standard; readable tests | Fixture magic can obscure where values come from |
| **pytest-asyncio 1.4** | Runs `async def` tests; `asyncio_mode = "auto"` means no markers | Most of the code is async | Event-loop scoping needs care |
| **pytest-cov 7.1** | Measures which lines and branches tests execute; fails under 85% | Shows untested code | Coverage measures execution, not correctness — mutation testing (planned) checks that |
| **hypothesis 6** | Property-based testing: generates many inputs to find a failing one | Proves encryption round-trips for any bytes, not three examples | Slower; failures need shrinking to understand |
| **httpx2 2.12** | HTTP client used by Starlette's `TestClient` | Required by the test client; lets tests call the app in-process | A fork of httpx; follow upstream changes |
| **ruff 0.16** | Linter and formatter in one, written in Rust; rules `E F I UP B SIM RUF S ASYNC PT DTZ TID C4 PTH N` | Replaces flake8, isort, black, bandit-lite; very fast | Rule set is opinionated; some rules need justified exceptions (e.g. `S105` in tests) |
| **pyright 1.1** | Static type checker; strict on `domain/` and `application/` | Catches whole classes of bugs before running; it found a real Protocol contract gap | Strict mode needs precise annotations |
| **import-linter 2.15** | Checks import rules between packages | Enforces the layering (C.3) automatically | Contracts must be kept in step with new packages |
| **pre-commit 4.7** | Runs checks before each commit (whitespace, YAML/TOML, private-key detection, gitleaks, ruff, pyright, import-linter, lockfile, Biome) | Broken code never reaches a commit | It stashes unstaged files, so each commit must pass on its own (which taught us to commit specs with their implementation) |
| **gitleaks** (hook) | Scans commits for secrets | A leaked key in git history is permanent | Occasional false positives |

### D.3 Infrastructure and platform

#### Python 3.13

The language version. Modern typing (`list[str]`, `X | None`, generic syntax `def f[T]()`), and every module starts with `from __future__ import annotations`.

#### uv

- **What it does:** Python package and project manager: creates the virtual environment, resolves and locks dependencies (`uv.lock`), runs commands (`uv run`), and can install Python itself.
- **Why:** very fast, reproducible (the lockfile pins every transitive version), one tool instead of pip + venv + pip-tools.
- **Trade-offs:** newer than pip; contributors must install it.
- **Note:** your environment currently sits on Anaconda's Python 3.13. `uv python install 3.13` followed by recreating `.venv` would make it independent of Anaconda.

#### Docker and Docker Compose

Containers for every service, so everyone runs the same Postgres, Valkey and Mailpit versions. Compose describes the stack in one file (`compose.yaml`) and passes passwords as **secrets** (files mounted at `/run/secrets`), not environment variables.

#### PostgreSQL 18 (`postgres:18-alpine`)

- **What it does:** the relational database for users, sessions, tokens, wrapped keys and the audit log.
- **Why:** transactional, mature, row-level locking (which the atomic claims rely on), roles and grants, triggers, and server-side timeouts.
- **Alternatives:** MySQL (weaker on some of these features); SQLite (no concurrent writers, no roles — fine for a desktop app, wrong for a server).
- **Trade-offs:** an operational service to back up and patch.

#### Valkey 8 (`valkey/valkey:8-alpine`)

- **What it does:** an in-memory key–value store; here, the shared rate-limit counters.
- **Why:** it is the open-source (BSD) continuation of Redis after Redis changed its licence. It speaks the same protocol, so redis-py works unchanged.
- **Configuration:** password from a Docker secret; persistence off (rate-limit counts are disposable).
- **Trade-offs:** another service. Its outage is not yet handled cleanly (Part L, gap G1).

#### Mailpit v1.27 (`axllent/mailpit`, development only)

A fake mail server that accepts every message and shows it in a web inbox at http://127.0.0.1:58025. Nothing is delivered to real addresses. Production uses the organisation's own SMTP server.

#### pnpm and Biome (present, not yet used)

pnpm manages the JavaScript workspace (`apps/*`); Biome formats and lints TypeScript. They come into use with the web UI (M13).

### D.4 Things deliberately not used

| Not used | Why not |
| --- | --- |
| Redis 8 | Licence is no longer open source in the usual sense; Valkey is the drop-in, BSD-licensed fork |
| JWT libraries | Server-side sessions instead (C.21) |
| passlib, bcrypt | passlib is unmaintained; bcrypt is not memory-hard (C.17) |
| An ORM | Frozen domain dataclasses and explicit SQL (C.10) |
| slowapi | Hard to key limits by account; we needed attempt counting before verification |
| MinIO, ClamAV | Deferred until uploads exist; earlier, they only add attack surface |
| Celery | Not needed until background jobs arrive (the worker, M4) |
| mmore | Arrives with masking and indexing, inside an isolated worker |
| Hosted email, LLM or search APIs by default | The one rule: nothing leaves the deployment unless the operator chooses it |

---

## Part E — Repository tour

```text
humani-t/
├── CLAUDE.md                     working agreement and code standards
├── README.md                     what the project is, how to start
├── compose.yaml                  dev stack: postgres, valkey, mailpit
├── .pre-commit-config.yaml       checks run before every commit
├── .gitattributes                LF line endings in the repository
├── .gitignore                    secrets/, .env, caches, build output
├── secrets/                      (git-ignored) dev passwords and KEK for Compose
├── scripts/
│   ├── dev_secrets.py            creates secrets/ and services/api/.env (never overwrites)
│   └── excalidraw_build.py       builds diagrams from their JSON sources
├── infrastructure/postgres/init/
│   └── 01-roles.sh               first-run: roles, grants, humani_t and humani_t_test databases
├── docs/
│   ├── WEB_APP_BRIEF.md          the product brief and its 18 security rules
│   ├── architecture.md           layers, seams, domain model, security model, concurrency rules
│   ├── api.md                    HTTP API reference
│   ├── security/controls.md      every control mapped to the test that proves it
│   ├── adr/0001-…                web-only, self-hosted decision
│   ├── checkpoint-01-…           this document
│   └── standards.md, dependencies.md, repo_structure.md, diagrams.md, …
└── services/api/
    ├── pyproject.toml            dependencies and tool config (pytest, coverage, ruff, pyright, import-linter)
    ├── uv.lock                   exact versions of every dependency
    ├── alembic.ini               migration tool config (no credentials in it)
    ├── .env.example              every setting, documented
    ├── migrations/
    │   ├── env.py                runs migrations as the schema owner
    │   └── versions/             0001 data_keys · 0002 audit log · 0003 accounts · 0004 users.version
    ├── app/
    │   ├── main.py               composition root: create_app(), middleware order, routers
    │   ├── settings.py           configuration (Pydantic boundary #2)
    │   ├── cli.py                operator commands
    │   ├── observability.py      structured, redacted logging
    │   ├── api/
    │   │   ├── dependencies.py   THE place implementations are chosen (build_providers, Providers.close)
    │   │   ├── session.py        cookies and the get_current dependency (auth + CSRF token)
    │   │   ├── middleware.py     request id, security headers, body limit, cross-site check
    │   │   ├── errors.py         domain errors → problem documents
    │   │   ├── schemas/          Pydantic request/response models (boundary #1)
    │   │   └── routes/           auth, account, admin, system, vocabulary, search
    │   ├── application/
    │   │   ├── accounts.py       sign-in, sessions, passwords, invitations, purge
    │   │   ├── audit.py          record and verify audit events
    │   │   ├── encryption.py     per-scope envelope encryption, shredding, rewrap
    │   │   └── search_service.py compile and search
    │   ├── domain/               accounts, sessions, passwords, audit, encryption, errors,
    │   │                         notifications, vocabulary, query, paper
    │   ├── storage/              base.py (Protocols), memory.py, postgres/ (tables, storage)
    │   ├── crypto/               base.py, aesgcm.py, local_key.py
    │   ├── auth/                 base.py, argon2.py, fake.py
    │   ├── email/                base.py, smtp.py, recording.py
    │   ├── ratelimit/            base.py, memory.py, valkey.py
    │   └── search/               base.py, fake.py
    └── tests/                    291 tests; support.py holds the harness and instruments
```

---

## Part F — Data model

Tables as they exist in Postgres at migration `0004`.

| Table | Columns | Purpose and rules |
| --- | --- | --- |
| `users` | `id, email, display_name, password_hash, is_admin, is_disabled, created_at, version` | One row per account. `email` is unique and must be lower-case (check constraint). `version` increases on every save; a save with a stale version is refused. |
| `sessions` | `id, user_id, token_hash, csrf_token_hash, created_at, last_seen_at, authenticated_at, revoked_at, user_agent` | One row per signed-in browser. Only hashes of tokens. Updates are conditional on `revoked_at IS NULL`. |
| `invitations` | `id, email, token_hash, invited_by, created_at, expires_at, accepted_at` | Single-use, 7 days. Claimed with a conditional update. |
| `password_resets` | `id, user_id, token_hash, created_at, expires_at, used_at` | Single-use, 30 minutes; a newer link cancels older ones. |
| `data_keys` | `scope_id, kek_version, wrapped, created_at, destroyed_at` | One wrapped data key per scope. Check constraint: destroyed exactly when `wrapped` is null. A destroyed key is never written again. |
| `audit_events` | `sequence, id, occurred_at, action, actor_id, target_type, target_id, scope_id, request_id, details_sealed, previous_hash, hash` | Append-only (grants and trigger). Identifiers only; details encrypted per scope. |
| `audit_chain_head` | `id, sequence, hash` | Exactly one row (check `id = 1`), locked on each append. |
| `alembic_version` | `version_num` | Which migration the database is at. |

**Who can do what:** `humani_t_app` can `SELECT, INSERT, UPDATE, DELETE` rows, except `UPDATE, DELETE, TRUNCATE` on `audit_events` and `DELETE, TRUNCATE, INSERT` on `audit_chain_head`. It cannot create or alter tables. `humani_t_migrator` owns the schema.

---

## Part G — HTTP API reference

All paths are under `/api/v1`. Full detail is in [`api.md`](api.md); the live, browsable version is at http://127.0.0.1:8731/docs in development.

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | anyone | Is the process up |
| GET | `/ready` | anyone | Can it serve (database reachable) |
| GET | `/info` | anyone, dev only | Which implementations are wired in |
| GET | `/sources` | anyone | Search sources available |
| POST | `/vocabulary/compile` | anyone | Vocabulary → Boolean query |
| POST | `/search` | anyone | Compile and search the corpus |
| POST | `/auth/login` | anyone | Sign in; sets cookies |
| POST | `/auth/logout` | signed in | End this session |
| GET | `/auth/session` | signed in | Who is signed in |
| POST | `/auth/reauthenticate` | signed in | Confirm password for sensitive actions |
| POST | `/auth/register` | invitee | Accept an invitation; signs in |
| POST | `/auth/password-reset` | anyone | Email a reset link (always 202) |
| POST | `/auth/password-reset/confirm` | link holder | Set a new password |
| GET | `/account/sessions` | signed in | List own sessions |
| DELETE | `/account/sessions/{id}` | signed in | Sign out one device |
| POST | `/account/sessions/revoke-others` | signed in | Sign out every other device |
| POST | `/account/password` | signed in, recent | Change password |
| POST | `/admin/invitations` | admin | Invite by email |
| POST | `/admin/users/{id}/disable` | admin | Disable an account |

`/vocabulary/compile`, `/search` and `/sources` are still open to anyone; they become project-scoped and signed-in-only in M10.

---

## Part H — Configuration reference

Environment variables carry the `HUMANI_T_` prefix. In production, secrets come from files in `/run/secrets/<lower-case name without prefix>`.

| Setting | Default | Meaning | Production |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `development` | `development`, `test` or `production` | `production` |
| `PUBLIC_ORIGIN` | `https://localhost` | The one origin the browser uses; must match the `Origin` header exactly. Dev `.env` sets `http://127.0.0.1:8731` | must be `https://` |
| `DATABASE_URL` | unset (memory) | Runtime role connection string | required |
| `MIGRATION_DATABASE_URL` | unset | Schema-owner connection string, Alembic only | for migrations |
| `DB_POOL_SIZE` | `10` | Connections per API process; hard limit | |
| `DB_POOL_TIMEOUT_SECONDS` | `5` | Wait for a connection before `503` | |
| `DB_STATEMENT_TIMEOUT_MS` | `15000` | Postgres cancels longer statements | |
| `KEY_ENCRYPTION_KEYS` | unset (per-process key) | `version:base64,…`; highest wraps, all unwrap. Required whenever `DATABASE_URL` is set | required |
| `VALKEY_URL` | unset (memory) | Shared rate limits | required |
| `SMTP_HOST` | unset (memory outbox) | Mail server | required |
| `SMTP_PORT` | `587` | Dev Mailpit: `51025` | |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | unset | SMTP authentication | as needed |
| `SMTP_SENDER` | `humani-t <no-reply@localhost>` | From address | set |
| `SMTP_STARTTLS` | `true` | Dev Mailpit: `false` | `true` |
| `LOG_LEVEL` | `INFO` | Logging threshold | |

Development values live in `services/api/.env`, generated by `scripts/dev_secrets.py`. Secret files for Compose live in `secrets/`. Both are git-ignored.

**Fixed values in code** (deliberately not configurable yet): session idle 1 h, absolute 24 h, re-authentication 15 min; invitation 7 days; reset link 30 min; 10 attempts per account, 100 per client, 5 resets per email, per 15 min; 4 concurrent hashes; 1 MiB body limit; idle-transaction timeout 30 s.

---

## Part I — Security model

The brief's 18 rules are the requirements; [`security/controls.md`](security/controls.md) maps each built control to its test. A compact threat view:

| Threat | Mitigation in place |
| --- | --- |
| Password guessing | Long-password policy, screening, Argon2id, throttling counted before verification |
| Learning which accounts exist | Identical responses and work for unknown accounts; identical reset responses |
| Stolen database copy | Only token hashes; Argon2 password hashes; data keys wrapped by a KEK not in the database |
| Stolen session cookie | `HttpOnly`, short idle timeout, server-side revocation, sign-out-everywhere |
| Session fixation | New token at every sign-in |
| Cross-site request forgery | Origin/Fetch-Metadata check, per-session CSRF token, JSON-only bodies |
| Cross-site scripting reading tokens | Session token is `HttpOnly`; API responses carry a deny-all CSP |
| Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` |
| Compromised API process | App role cannot change schema or rewrite audit history |
| Tampering with history | Hash chain, append-only grants and trigger, `verify-audit` |
| Data that must be deleted | Crypto-shredding of per-project keys |
| Leaking data in errors or logs | Problem documents never echo input; log redaction |
| Overload and denial of service | Bounded pool with 503, body limit, throttling, no connection held across slow work |
| Race conditions | Conditional updates, optimistic locking, atomic claims, atomic rate limiting |
| Secrets in the repository | Git-ignored `.env` and `secrets/`, gitleaks hook, Docker secrets in production |

Not yet mitigated: see Part L.

---

## Part J — Manual end-to-end test guide

Work through the sections in order the first time. Each test says what to do, what to expect, and why it matters. Tick the checklist in J.22 as you go.

### J.1 Prerequisites

| Tool | Check | Notes |
| --- | --- | --- |
| Docker Desktop | `docker version` | must be running |
| uv | `uv --version` | |
| Git | `git --version` | |
| Postman | open it | for the HTTP tests |
| pgAdmin 4 | open it | to look inside the database (set up in an earlier step of this project) |
| A browser | | for Mailpit and Swagger |

### J.2 Start from a known state

From the repository root:

```powershell
git status
git log --oneline -3
python scripts\dev_secrets.py
docker compose up -d
docker compose ps
```

Expect `git status` clean, and `postgres`, `valkey` and `mailpit` all `healthy`. `dev_secrets.py` creates nothing new if the files already exist.

Then, in `services\api`:

```powershell
cd services\api
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env alembic current
```

Expect `0004 (head)`.

If the admin account does not exist yet (or a test run removed it):

```powershell
"violet-harbour-quietly-41-lanterns" | uv run --env-file .env python -m app.cli create-admin --email admin@example.org --password-stdin
```

Expect `created admin admin@example.org`, or `an account with this email already exists` (also fine).

### J.3 Run the automated checks first

```powershell
uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

Expect all tests to pass (the Postgres and Valkey ones are skipped), coverage over 85%, and no lint, type or layering errors.

To include the Postgres and Valkey tests (they use `humani_t_test`, never your dev data):

```powershell
$env:TEST_DATABASE_URL = (Select-String -Path .env -Pattern '^HUMANI_T_DATABASE_URL=(.*)').Matches[0].Groups[1].Value -replace '/humani_t$', '/humani_t_test'
$env:TEST_VALKEY_URL = (Select-String -Path .env -Pattern '^HUMANI_T_VALKEY_URL=(.*)').Matches[0].Groups[1].Value
uv run pytest
```

Expect **291 passed**, none skipped.

### J.4 Start the API

In a terminal you keep open, in `services\api`:

```powershell
uv run --env-file .env uvicorn app.main:app --reload --port 8731
```

Expect `Uvicorn running on http://127.0.0.1:8731`. Each request you send appears here as a log line with a request id.

### J.5 Set up your tools

**Mailpit:** open http://127.0.0.1:58025. Every email the API sends appears here.

**Postman:**

1. Create a collection named `humani-t`, or import `http://127.0.0.1:8731/openapi.json` (Import → Link) to get every endpoint.
2. Collection → Variables: add `base` = `http://127.0.0.1:8731/api/v1`, plus empty `session` and `csrf`.
3. Collection → Headers (or on each request after login):
   - `Cookie` = `__Host-session={{session}}; __Host-csrf={{csrf}}`
   - `X-CSRF-Token` = `{{csrf}}`
4. On the **login** request and the **register** request, Scripts → Post-response (both start a session, so both must capture its cookies). The variables always hold the most recent session: after registering as someone else, sign in as the admin again to switch back.

```js
for (const h of pm.response.headers.all()) {
  if (h.key.toLowerCase() !== "set-cookie") continue;
  const [name, value] = h.value.split(";")[0].split("=");
  if (name === "__Host-session") pm.collectionVariables.set("session", value);
  if (name === "__Host-csrf") pm.collectionVariables.set("csrf", value);
}
```

Why the manual cookie header: the cookies are `Secure`, and over plain `http://` some clients will not send Secure cookies back automatically. Copying them into variables makes the tests independent of that behaviour. In production, everything is HTTPS.

**pgAdmin:** connect to `127.0.0.1:55432`, database `humani_t`, as `postgres` (password in `secrets\db_superuser_password`). Optionally add a second connection as `humani_t_app` (`secrets\db_app_password`) to see what the app itself can do.

**Swagger** (http://127.0.0.1:8731/docs): useful for reading the API and for `GET` requests and sign-in. It cannot send `X-CSRF-Token`, so signed-in writes from Swagger get `403`. That is the protection working, not a bug.

### J.6 System endpoints and headers

| # | Request | Expect | Why |
| --- | --- | --- | --- |
| 6.1 | `GET {{base}}/health` | `200 {"status":"ok"}` | Liveness touches nothing else |
| 6.2 | `GET {{base}}/ready` | `200 {"status":"ready"}` | Readiness checks the database |
| 6.3 | `GET {{base}}/info` | `200`, `search_provider: "fake"` | Dev-only introspection |
| 6.4 | Look at the response headers of 6.1 | `content-security-policy`, `strict-transport-security`, `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, `cache-control: no-store`, `x-request-id` | C.25 |
| 6.5 | `GET {{base}}/does-not-exist` | `404`, `content-type: application/problem+json` | C.26 |
| 6.6 | Send 6.1 with header `x-request-id: my-test-id-123` | the response echoes `my-test-id-123` | Correlation with a proxy's id |

### J.7 The search skeleton (pre-existing)

| # | Request | Expect |
| --- | --- | --- |
| 7.1 | `POST {{base}}/vocabulary/compile` with the body below | `200`, `rendered` = `("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")` |
| 7.2 | `POST {{base}}/search` with the same body plus `"limit": 10` | `200`, results with the compiled query alongside |
| 7.3 | `POST {{base}}/vocabulary/compile` with `"concepts": "Jane Doe, +44 7700 900123"` | `422`; the text you sent does **not** appear in the response |

```json
{
  "origin": "llm_assisted",
  "concepts": [
    {"term": "chatbot", "synonyms": ["conversational agent"], "theme": "technology"},
    {"term": "humanitarian", "synonyms": ["crisis response"], "theme": "setting"}
  ]
}
```

### J.8 Signing in

| # | Request (`POST {{base}}/auth/login`) | Expect | Why |
| --- | --- | --- | --- |
| 8.1 | `{"email":"admin@example.org","password":"violet-harbour-quietly-41-lanterns"}` | `200`, body with `user`, two `set-cookie` headers; variables `session` and `csrf` filled | Happy path |
| 8.2 | Same, email in capitals `ADMIN@EXAMPLE.ORG` | `200` | Emails are case-insensitive |
| 8.3 | Correct email, wrong password | `401`, `"Email or password is incorrect."`, no `set-cookie` | |
| 8.4 | `nobody@example.org`, any password | `401`, **byte-for-byte the same body as 8.3** | No account enumeration (C.18) |
| 8.5 | `{"email":"x"}` | `422` | Shape validation |
| 8.6 | `{"email":"not-an-email","password":"x"}` | `422` | |

After 8.3 and 8.4, wait or sign in successfully (8.1) before J.15, because failures count towards the throttle.

### J.9 Cookies

Inspect the `set-cookie` headers from 8.1:

- `__Host-session=…; HttpOnly; Path=/; SameSite=lax; Secure` — no `Domain`, no `Max-Age`.
- `__Host-csrf=…; Path=/; SameSite=strict; Secure` — no `HttpOnly` (the web app must read it).

Then in pgAdmin:

```sql
SELECT id, encode(token_hash, 'hex') AS token_hash, created_at, last_seen_at, revoked_at
FROM sessions ORDER BY created_at DESC LIMIT 5;
```

Expect the stored `token_hash` to be 64 hex characters that do **not** match your cookie value. Why: only a one-way hash is stored (C.21).

### J.10 The current session, logout and replay

| # | Do this | Expect |
| --- | --- | --- |
| 10.1 | `GET {{base}}/auth/session` | `200`, your user and session |
| 10.2 | Copy the current `{{session}}` value somewhere | |
| 10.3 | `POST {{base}}/auth/logout` (with the `X-CSRF-Token` header) | `204`; two `set-cookie` headers with `Max-Age=0` |
| 10.4 | `GET {{base}}/auth/session` with the old cookie value from 10.2 | `401` — the session is dead **on the server**, not just in the client |

Sign in again (8.1) before continuing.

### J.11 Cross-site request forgery

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 11.1 | `POST {{base}}/auth/logout` with the `X-CSRF-Token` header **disabled** | `403`; then `GET /auth/session` is still `200` | Layer 2 |
| 11.2 | Same, with `X-CSRF-Token: guess` | `403` | |
| 11.3 | `POST {{base}}/auth/login` with header `Origin: https://evil.example` | `403`, even with correct credentials | Layer 1 |
| 11.4 | `POST {{base}}/auth/login` with header `Sec-Fetch-Site: cross-site` | `403` | Layer 1 |
| 11.5 | `POST {{base}}/auth/login` with body type *x-www-form-urlencoded* | `415` | Layer 3 |
| 11.6 | `POST {{base}}/auth/login` with body type *raw → Text* containing JSON | `415` | Layer 3 |
| 11.7 | In the browser, at http://127.0.0.1:8731/docs, sign in through *Try it out* | `200` | A same-origin browser request passes |

### J.12 Invitations and registration

Signed in as the admin:

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 12.1 | `POST {{base}}/admin/invitations` `{"email":"kofi@example.org"}` | `201` with `id`, `email`, `expires_at`; **no token** in the body | The link goes only to the inbox |
| 12.2 | Open Mailpit | "You have been invited to humani-t", link `http://127.0.0.1:8731/accept-invitation#token=…` | Token after `#` (C.32) |
| 12.3 | Repeat 12.1 with `admin@example.org` | `409` | Existing account |
| 12.4 | Open a **new** Postman tab without the cookie headers. `POST {{base}}/auth/register` `{"token":"<token from email>","password":"short","display_name":"Kofi"}` | `422`, `violations` includes `too_short` | Policy |
| 12.5 | Same, with password `kofi.mensah.2026!` and display name `Kofi Mensah` | `422`, `contains_personal_details` | Personal-detail screening |
| 12.6 | Same, with password `the-river-bends-twice-near-tamale` | `201`, `user.email` = `kofi@example.org`, `set-cookie` present. **Note `user.id`** | Invitation = verified email |
| 12.7 | Send 12.6 again | `400` | Single use |
| 12.8 | As a non-admin (Kofi's cookies), `POST /admin/invitations` | `403` | Admins only |
| 12.9 | Without any cookies, `POST /admin/invitations` | `401` | |

### J.13 Disabling an account

| # | Do this | Expect |
| --- | --- | --- |
| 13.1 | As admin: `POST {{base}}/admin/users/<Kofi's id>/disable` | `204` |
| 13.2 | With Kofi's old cookies: `GET /auth/session` | `401` — his sessions ended |
| 13.3 | Sign in as Kofi | `401`, same body as a wrong password |
| 13.4 | pgAdmin: `SELECT email, is_disabled, version FROM users;` | Kofi `is_disabled = true`, `version` increased |

### J.14 Sessions on several devices

| # | Do this | Expect |
| --- | --- | --- |
| 14.1 | Sign in as admin in Postman tab A, then in tab B (each tab saving to its own variables, or sign in twice and keep the first values aside) | two sessions |
| 14.2 | `GET {{base}}/account/sessions` | two entries, exactly one `current: true`, no token fields |
| 14.3 | From B: `DELETE {{base}}/account/sessions/<A's id>` | `204`; A's `GET /auth/session` → `401`; B → `200` |
| 14.4 | Sign in three times, then from the last: `POST {{base}}/account/sessions/revoke-others` | `204`; only the last session still works |
| 14.5 | As admin, try to `DELETE` a session id that belongs to someone else | `404` (not `403`: its existence is not your business) |

### J.15 Changing the password

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 15.1 | Signed in within the last 15 minutes: `POST {{base}}/account/password` `{"current_password":"violet-harbour-quietly-41-lanterns","new_password":"password"}` | `422`, `violations: ["too_short","too_common"]` | Policy |
| 15.2 | Same with a wrong `current_password` | `403` | |
| 15.3 | With the correct current password and `new_password` = `quiet-lanterns-over-the-harbour-41` | `204` with new `set-cookie` headers; Mailpit: "Your humani-t password was changed"; every other session `401` | C.23 |
| 15.4 | Sign in with the old password | `401` |
| 15.5 | Sign in with the new password | `200` |
| 15.6 | Optional, slow: stay signed in, wait 16 minutes (send a `GET /auth/session` every few minutes so the session stays alive), then try 15.3 again | `401` with `"reauthentication_required": true`; then `POST /auth/reauthenticate` `{"password":"<current>"}` → `204`, and the change works | 15-minute window |

From here on, the admin password is the one you set in 15.3.

### J.16 Password reset

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 16.1 | `POST {{base}}/auth/password-reset` `{"email":"admin@example.org"}` | `202`, `"If an account exists for that email, a reset link has been sent."` | |
| 16.2 | Same for `nobody@example.org` | **identical** `202` body; no email in Mailpit | No enumeration |
| 16.3 | Mailpit | one "Reset your humani-t password", link `…/reset-password#token=…` | |
| 16.4 | Request a second reset for the admin, then try the **first** link's token in 16.5 | `400` | Newest link wins |
| 16.5 | `POST {{base}}/auth/password-reset/confirm` `{"token":"<newest token>","new_password":"lanterns-and-harbours-at-dawn-41"}` | `204`; every session `401`; a "password was changed" email | Fictional example password <!-- gitleaks:allow --> |
| 16.6 | Send 16.5 again | `400` | Single use |
| 16.7 | Optional, slow: request a reset, wait 31 minutes, confirm | `400` | 30-minute expiry |

### J.17 Throttling

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 17.1 | Postman Collection Runner: run a login request with a **wrong** password for 12 iterations | iterations 1–10 → `401`; 11 and 12 → `429` with a `Retry-After` header (seconds) | C.19 |
| 17.2 | Immediately sign in with the **correct** password | `429` | The throttle must not confirm a correct guess |
| 17.3 | Wait 15 minutes, sign in correctly | `200` | Throttling, not lockout |
| 17.4 | Runner: 6 iterations of `POST /auth/password-reset` for one email | 1–5 → `202`, 6 → `429`; Mailpit shows 5 emails | Per-email limit |

### J.18 The audit trail

1. In pgAdmin:

```sql
SELECT sequence, occurred_at, action, target_type, target_id, actor_id
FROM audit_events ORDER BY sequence DESC LIMIT 30;
```

Expect a row for each thing you did: `auth.login_failed`, `auth.login`, `auth.logout`, `account.invited`, `account.registered`, `account.disabled`, `auth.session_revoked`, `account.password_changed`, `auth.password_reset_requested`, `auth.password_reset_completed`… Failed sign-ins for unknown accounts show `target_id = unknown`. **No email addresses anywhere.**

2. Verify the chain:

```powershell
uv run --env-file .env python -m app.cli verify-audit
```

Expect `audit chain intact: N event(s)`.

3. Try to rewrite history:

| Connected as | Run | Expect |
| --- | --- | --- |
| `humani_t_app` | `UPDATE audit_events SET action = 'x.y';` | `permission denied for table audit_events` |
| `humani_t_app` | `DELETE FROM audit_events;` | `permission denied` |
| `postgres` (superuser) | `DELETE FROM audit_events;` | `audit_events is append-only (DELETE refused)` |

4. **Optional — watch tampering being detected**, in a throwaway database so your dev chain stays intact. In pgAdmin as `postgres`, on database `humani_t`:

```sql
CREATE DATABASE humani_t_tamper OWNER humani_t_migrator;
```

Then in PowerShell (`services\api`), migrate it, add a few events, and verify:

```powershell
$dev = (Select-String -Path .env -Pattern '^HUMANI_T_DATABASE_URL=(.*)').Matches[0].Groups[1].Value
$mig = (Select-String -Path .env -Pattern '^HUMANI_T_MIGRATION_DATABASE_URL=(.*)').Matches[0].Groups[1].Value
$env:HUMANI_T_MIGRATION_DATABASE_URL = $mig -replace '/humani_t$', '/humani_t_tamper'
uv run alembic upgrade head
Remove-Item Env:HUMANI_T_MIGRATION_DATABASE_URL
$env:HUMANI_T_DATABASE_URL = $dev -replace '/humani_t$', '/humani_t_tamper'
uv run --env-file .env python -m app.cli rewrap-keys
uv run --env-file .env python -m app.cli rewrap-keys
uv run --env-file .env python -m app.cli verify-audit
```

Expect `audit chain intact: 2 event(s)`. A variable already set in the shell takes precedence over `--env-file` (verified for this checkpoint), which is why `$env:HUMANI_T_DATABASE_URL` points these commands at the tamper database.

Now tamper, in pgAdmin as `postgres` on `humani_t_tamper`:

```sql
ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only;
UPDATE audit_events SET action = 'keys.innocent' WHERE sequence = 1;
ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only;
```

Run `uv run --env-file .env python -m app.cli verify-audit` again. Expect `AUDIT CHAIN BROKEN at sequence 1: event content was altered` and exit code 1. Even a superuser who disables the trigger is caught.

Clean up:

```powershell
Remove-Item Env:HUMANI_T_DATABASE_URL
```

and in pgAdmin (connected to `humani_t`, not the tamper database): `DROP DATABASE humani_t_tamper;`

Safety check before tampering: the first `verify-audit` must report **2** events. If it reports your dev chain's (much larger) count, the variable did not take effect — stop, and do not tamper.

### J.19 Database-level protections

In pgAdmin, connected as `humani_t_app` to `humani_t`:

| Run | Expect | Why |
| --- | --- | --- |
| `CREATE TABLE intruder (id int);` | `permission denied for schema public` | The app cannot change the schema |
| `INSERT INTO data_keys VALUES (gen_random_uuid(), 1, 'x', now(), now());` | `violates check constraint "destroyed_iff_unwrapped"` | Rules hold even outside the app |
| `INSERT INTO users (id,email,display_name,password_hash,is_admin,is_disabled,created_at) VALUES (gen_random_uuid(),'MiXeD@Example.org','x','x',false,false,now());` | `violates check constraint "email_is_normalised"` | |

### J.20 Failure modes

| # | Do this | Expect | Why |
| --- | --- | --- | --- |
| 20.1 | `docker compose -f ..\..\compose.yaml stop postgres`, then `POST /auth/login` and `GET /ready` | login: `503` with `Retry-After: 5`; ready: `503`. The API process keeps running | C.30 |
| 20.2 | `docker compose -f ..\..\compose.yaml start postgres`, wait a few seconds, retry | `200` | Pool pre-ping recovers |
| 20.3 | `docker compose -f ..\..\compose.yaml stop mailpit`, then request a password reset | still `202` (quiet send); a warning in the API log | No enumeration through mail errors |
| 20.4 | With Mailpit still stopped, as admin `POST /admin/invitations` for a new email | `502`, "The email could not be sent. The action was saved…" | The admin must know |
| 20.5 | `docker compose -f ..\..\compose.yaml start mailpit` | | |
| 20.6 | `docker compose -f ..\..\compose.yaml stop valkey`, then `POST /auth/login` | **Currently `500`** — known gap G1 (should be `503`) | Tracked in Part L |
| 20.7 | `docker compose -f ..\..\compose.yaml start valkey` | sign-in works again | |
| 20.8 | `POST /auth/login` with a body over 1 MB (paste a very long string as the email) | `413` | C.28 |

### J.21 Operator commands and production mode

| # | Run (in `services\api`) | Expect |
| --- | --- | --- |
| 21.1 | `uv run --env-file .env python -m app.cli generate-kek` | a 44-character base64 key (32 bytes) |
| 21.2 | `"password" \| uv run --env-file .env python -m app.cli create-admin --email second@example.org --password-stdin` | exit 1, `password refused: too_short, too_common` |
| 21.3 | `"violet-harbour-quietly-41-lanterns" \| uv run --env-file .env python -m app.cli create-admin --email admin@humani-t.local --password-stdin` | exit 1, `email refused: … special-use or reserved name …` |
| 21.4 | `uv run --env-file .env python -m app.cli create-admin --email a@example.org --password x` | usage error: passwords are never accepted as arguments |
| 21.5 | `uv run --env-file .env python -m app.cli purge-expired` | `purged N session(s), N reset link(s), N invitation(s)` |
| 21.6 | `uv run --env-file .env python -m app.cli verify-audit` | `audit chain intact` |
| 21.7 | Key rotation: run 21.1, then in `.env` change `HUMANI_T_KEY_ENCRYPTION_KEYS=1:<old>` to `1:<old>,2:<new>`; run `uv run --env-file .env python -m app.cli rewrap-keys` | `rewrapped N data key(s)` (N is 0 until projects exist in M10; the command and its audit event still run). Keep both keys in `.env` |
| 21.8 | Production refuses to start half-configured: `$env:HUMANI_T_ENVIRONMENT = "production"; uv run uvicorn app.main:app --port 8732` (note: no `--env-file`) | start-up fails with `production requires: database_url, key_encryption_keys, valkey_url, smtp_host` |
| 21.9 | `Remove-Item Env:HUMANI_T_ENVIRONMENT` | back to development |

On 21.2 and 21.3, PowerShell pipes the string into the command's standard input, which is how the password is supplied safely.

### J.22 Results checklist

| Section | Passed | Notes |
| --- | --- | --- |
| J.3 Automated checks (291 with Postgres/Valkey) | ☐ | |
| J.6 System endpoints and headers | ☐ | |
| J.7 Search skeleton | ☐ | |
| J.8 Signing in | ☐ | |
| J.9 Cookies and stored hashes | ☐ | |
| J.10 Session, logout, replay | ☐ | |
| J.11 CSRF | ☐ | |
| J.12 Invitations and registration | ☐ | |
| J.13 Disabling accounts | ☐ | |
| J.14 Multiple sessions | ☐ | |
| J.15 Changing the password | ☐ | |
| J.16 Password reset | ☐ | |
| J.17 Throttling | ☐ | |
| J.18 Audit trail and tamper detection | ☐ | |
| J.19 Database-level protections | ☐ | |
| J.20 Failure modes | ☐ | |
| J.21 Operator commands and production mode | ☐ | |

Anything that does not match an "Expect" column is either a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

---

## Part K — Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `403 Cross-site requests are not allowed` from Swagger | `HUMANI_T_PUBLIC_ORIGIN` does not match the browser's address | Use exactly `http://127.0.0.1:8731` (not `localhost`) or change the setting to match |
| `403 Missing or invalid CSRF token` | Header missing, or `{{csrf}}` is from an older session | Sign in again so the script refreshes the variables |
| `401` right after signing in | Postman sent stale cookies | Check the `Cookie` header uses `{{session}}`; disable Postman's automatic cookie jar for the domain if it conflicts |
| `429` unexpectedly | Earlier failed attempts within 15 minutes | Wait 15 minutes, or sign in successfully once (it resets the account count) |
| `503` on every request | Postgres stopped, or the pool is exhausted | `docker compose ps`; start Postgres |
| `500` on login | Valkey stopped (gap G1) | `docker compose start valkey` |
| `password authentication failed` (pgAdmin or the API) | Secret files regenerated after the volume was created | Dev only: `docker compose down -v`, `docker compose up -d`, re-run migrations, recreate the admin. **Deletes all dev data** |
| `alembic current` shows an older revision | Migrations not run | `uv run --env-file .env alembic upgrade head` |
| Postgres tests fail after a migration | `humani_t_test` not migrated | Run the test-database migration commands in J.3's notes (set `HUMANI_T_MIGRATION_DATABASE_URL` to `…/humani_t_test`, then `alembic upgrade head`) |
| A commit is rejected by a hook | The hook found an issue or reformatted a file | Read the message; if it reformatted, `git add` the files again and re-run `git commit` |
| Emails don't arrive | Mailpit stopped, or `.env` lacks the SMTP lines | `docker compose ps`; regenerate `.env` (move the old one aside, run `dev_secrets.py`) |
| Tracebacks mention `anaconda3` | uv's environment is built on Anaconda's Python | Works; optionally `uv python install 3.13`, delete `.venv`, `uv sync --python-preference only-managed` |

---

## Part L — Known gaps and what comes next

### L.1 Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G1 | Valkey outage is not translated to `StorageUnavailableError`-style handling | Sign-in returns `500` instead of `503` while Valkey is down | Next: spec (failing test) + fix, failing closed with `503` |
| G2 | No multi-factor authentication | A password alone signs in | M9 |
| G3 | No projects or roles; search endpoints open to anyone | No per-project access control yet | M10 |
| G4 | No SSRF guard for outbound requests | Not exploitable yet (no outbound requests) | M11, before real paper sources |
| G5 | Log redaction untested | A regression would go unnoticed | Add a test with the next logging change |
| G6 | SMTP over TLS untested | The production mail path is unproven | M4, TLS mail server in CI |
| G7 | No real-browser test of cookies and CSRF | Browser behaviour assumed from specifications | M13, Playwright |
| G8 | No load test across several API workers | Throughput and pool sizing unmeasured | M4 |
| G9 | No mutation testing, API fuzzing or SAST in CI | Weak tests and some vulnerability classes could slip through | M4: mutmut, Schemathesis, Semgrep/CodeQL |
| G10 | Audit chain head not anchored outside the database | A superuser could rewrite the whole chain consistently | Worker job signing the head (M4) |
| G11 | Crypto-shredding does not reach backups until they expire | Deletion is complete only after backup retention | Document in the deployment guide (M14) |
| G12 | Email addresses stored in plain text in `users` | Readable to anyone with database access | Consider encryption with a blind index |
| G13 | No independent security review | Everything here is self-checked | Before any real data: independent review, then professional assessment |
| G14 | Open-source files and CI missing | Contributors lack guidance; no automated checks on pull requests | M3, M4 |

### L.2 Recommended order from here

1. Your end-to-end pass with this guide.
2. Fix G1 (test first).
3. An independent security review of everything built so far.
4. M9 — multi-factor authentication.
5. M10 — projects and roles; protect the search endpoints.
6. M3 and M4 — open-source files, containers, CI with mutation testing, fuzzing and SAST.

---

## Part M — Glossary

| Term | Meaning |
| --- | --- |
| **AAD / associated data** | Data bound into an encryption without being encrypted; decryption fails if it differs. Used to tie a ciphertext to its row. |
| **AAL2** | NIST "authenticator assurance level 2": the session rules followed here. |
| **ADR** | Architecture Decision Record: a short document recording a hard-to-reverse decision and why. |
| **Argon2id** | A memory-hard password hashing algorithm. |
| **ASGI** | The Python interface between async web servers (Uvicorn) and frameworks (FastAPI). |
| **Audit chain head** | The latest event's sequence and hash, kept in one locked row. |
| **Composition root** | The single place where implementations are chosen and wired together (`api/dependencies.py`). |
| **Contract test** | The same test suite run against a fake and the real implementation, proving they behave alike. |
| **Crypto-shredding** | Deleting data by destroying the key that encrypts it. |
| **CSP** | Content Security Policy: a header telling browsers what a response may load or run. |
| **CSRF** | Cross-site request forgery: another site making your browser send a request you did not intend. |
| **Data key / KEK** | The key that encrypts data / the key-encryption key that encrypts data keys. |
| **Envelope encryption** | Encrypting data with a data key, and the data key with a KEK. |
| **Fake** | A working, simplified implementation used in tests (unlike a mock, which only returns canned answers). |
| **Fetch Metadata** | Browser headers such as `Sec-Fetch-Site` that say where a request came from. |
| **Idempotent / single-use claim** | A conditional update that succeeds for exactly one caller. |
| **Optimistic locking** | Saving only if the record's version is unchanged since it was read; otherwise refusing. |
| **Problem document** | The RFC 9457 JSON format for HTTP errors. |
| **Protocol** | A Python type describing the methods a class must have, without inheritance (PEP 544). |
| **Race condition** | A bug that appears only when two operations interleave in an unlucky order. |
| **Seam** | A boundary where an implementation can be swapped (a Protocol and its implementations). |
| **Session fixation** | An attack where the victim is made to use a session id the attacker knows; prevented by issuing a new token at sign-in. |
| **SSRF** | Server-side request forgery: tricking the server into fetching internal addresses. |
| **Unit of Work** | One transaction, exposing repositories; nothing is visible to others until commit. |
| **Valkey** | The open-source fork of Redis used for rate limiting. |
