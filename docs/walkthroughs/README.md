# Walkthroughs

A walkthrough is a guided tour of one phase of humani-t, written for someone who will check the work by hand. Each one explains every architectural choice made in that phase and why the alternatives lost, every library it brought in and what that library costs, which files to read and in what order, and a numbered set of manual tests you can run yourself in PowerShell, Postman and pgAdmin, with the result to expect and what a different result would mean.

The walkthroughs replace the single checkpoint guide, `docs/checkpoints/checkpoint-01-security-foundation.md`, which covered milestones M0 to M8 in one document. It was removed when the walkthroughs arrived and is kept in git history; the walkthroughs carry the same material split by phase, checked again against the code, and extended where the original was thin.

## What humani-t is

A reviewer asks a research question. humani-t helps turn it into a search, finds papers, masks personal data, screens papers against criteria with a language model, and answers follow-up questions with cited sources. It is for humanitarian teams, so the data it holds can be sensitive: names, health details, locations. The whole design follows from that.

Two rules from [`CLAUDE.md`](../../CLAUDE.md) shape every phase:

> humani-t is a self-hosted web application. Data leaves the deployment only to destinations the operator has configured. Text sent to a model the deployment does not run itself is masked first, unless both the operator and the project's owner have chosen otherwise.

> Could a reviewer reconstruct why this paper was included or excluded, six months from now, from what we stored?

The first rule drives the security design. The second drives provenance: decisions are recorded with what produced them, at the moment they are made.

## How to use a walkthrough

1. Do [00 — Setup](00-setup.md) once. Every other walkthrough assumes its tools, services, Postman collection and pgAdmin connections exist.
2. Read a phase top to bottom once: *What this phase delivers*, then *Architecture choices and why*, then *Libraries*. These are reference sections; come back to single entries later.
3. Open the files in *Files to read, in order* beside the walkthrough. The order is chosen so each file only uses ideas from the ones before it.
4. Run the *Manual tests* in order. Each says what to do, what to expect, what it proves and what a failure would mean. Tick the results checklist at the end of the section.
5. Check *Automated tests that prove it*: every claim in the walkthrough names the test that fails if the claim stops being true.
6. Read *Known gaps* before you trust anything for real data.

Conventions used throughout:

- Commands are for **Windows PowerShell 5.1**. They never use `&&` or `export`; they chain with `;` or `if ($?) { … }`, and set variables with `$env:NAME = '...'`.
- Paths are relative to the repository root, `C:\Users\fiifidawson\devdawson\GitHub\humani-t`, unless a step says to `cd services\api` first. Most `uv run` commands run from `services\api`.
- `{{base}}`, `{{session}}` and `{{csrf}}` are Postman collection variables, set up in [00 — Setup](00-setup.md#s010-postman).
- Passwords in the examples are fictional and only ever used against the local development stack.
- Decisions are numbered `D<phase>.<n>` and manual tests `T<phase>.<n>`, so "D5.3" or "T2.14" points at one place in one file.
- The full HTTP reference is [`api.md`](../api.md); in development the live, browsable version is at http://127.0.0.1:8731/docs.
- Every phase follows [`TEMPLATE.md`](TEMPLATE.md), so a future phase reads the same way.

## Phases

| # | Walkthrough | Milestones | Status | What it delivers |
| --- | --- | --- | --- | --- |
| 00 | [Setup](00-setup.md) | cross-cutting | ✅ done | The tools to install, development secrets, the Compose services (Postgres, Valkey, Mailpit), migrations, the first administrator, Postman and pgAdmin, running the API and the tests, and troubleshooting. |
| 01 | [Baseline and quality gates](01-baseline-and-quality-gates.md) | M0–M2 | ✅ done | The web-only decision (ADR-0001), the layered architecture and its seams, the search skeleton, and the gates every commit passes: ruff, pyright, import-linter, pytest-cov, hypothesis, pre-commit, gitleaks and Biome. |
| 02 | [Configuration and persistence](02-configuration-and-persistence.md) | M5 | ✅ done | Settings and secrets that fail closed, SQLAlchemy Core with asyncpg and Alembic, two database roles, the unit of work, memory vs Postgres contract tests, a bounded pool with server-side timeouts, problem responses and security headers. |
| 03 | [Envelope encryption](03-envelope-encryption.md) | M6 | ✅ done | Per-scope data keys wrapped by an operator-held KEK, AES-256-GCM bound to its row, crypto-shredding that cannot be undone, KEK rotation with `rewrap-keys`. |
| 04 | [Audit log](04-audit-log.md) | M7 | ✅ done | A hash-chained, append-only audit log written under a locked chain head, protected by grants and a trigger, and checked by `verify-audit`. |
| 05 | [Accounts and sessions](05-accounts-and-sessions.md) | M8 | ✅ done | Password policy and Argon2id, server-side sessions in `__Host-` cookies, three-layer CSRF defence, throttling, invitations, password resets, email, and the concurrency and resource-safety hardening. |
| 06 | [Multi-factor authentication](06-mfa.md) | M9 | ✅ done | Passkeys (WebAuthn) first and TOTP as fallback, recovery codes, a two-step sign-in through a pending session, MFA required by default, and the rate limiter failing closed (G1). |
| 07 | [Projects and roles](07-projects-and-roles.md) | M10 | ✅ done | Projects, memberships and the owner/reviewer/viewer roles through `can(role, action)`, a data key per project, delete by shredding, 404 for non-members, and the search endpoints made signed-in and project-scoped. |
| 08 | [Runs, events and gates](08-runs-events-and-gates.md) | [architecture §4.4](../architecture.md#44-runs-live-events-and-gates) | ✅ done | Reviews inside a project; runs as background jobs on a Postgres queue with leases and crash recovery; one Server-Sent Events stream per run that replays missed events from `Last-Event-ID`; vocabulary and privacy gates decided once; cancel keeping finished stages; the stage seam the real stages plug into ([ADR-0003](../adr/0003-run-queue-and-event-fan-out.md)). |
| 09 | [Plan and discover](09-plan-and-discover.md) | M11 | ✅ done | The SSRF-guarded `HttpFetcher` every outbound request goes through, OpenAlex, Europe PMC and arXiv behind `PaperSearchProvider`, `InferenceProvider` with Ollama behind a fail-closed prompt guard, and the search-planning conversation: clarifying questions, versioned drafts with their differences, change requests, a first-accept-wins accept, your own terms used exactly as given, privacy receipts, and the `queries` stage with its vocabulary gate ([ADR-0005](../adr/0005-plan-versions-are-written-once.md)). |
| 10 | [Masking](10-masking.md) | [architecture §3.9](../architecture.md#39-masking) | 🟡 in progress | Masking as derived, reviewable spans over encrypted raw text, several local detectors, project masking rules, human review before use, and a last check on everything that leaves. |
| 11 | [Discover, ingest and screening](11-screening.md) | UI_UX_SPEC §15 P1 | 🟡 in progress | Discover across every enabled source with merged duplicates, kept text for masking, screening of masked text with full provenance on every `ScreeningDecision`, PRISMA counts, the run manifest, and the paper routes. The mask store and the brief stage are still to be wired. |
| 12 | [Decision brief](12-decision-brief.md) | [architecture §4.2](../architecture.md#42-reviews) | 🟡 in progress | The `answer` stage: a plain-Python agent loop (planner, BM25 retriever, writer, code checks, critic) that writes a `DecisionBrief` from masked passages with citations checked against what was retrieved, stored once per run, sealed, with its trace; and the routes to read the brief, a cited source and how it was made ([ADR-0007](../adr/0007-bm25-retrieval-for-the-brief.md)). |
| 13 | `13-web-ui` | M13, [UI spec](../UI_UX_SPEC.md#15-delivery-phases) phase P1 | ⬜ planned | The React web app with strict TypeScript and generated API types: sign-in and MFA, projects, the workspace chat, and reading a brief. |
| 14 | [Deploy and CI](14-deploy-and-ci.md) | M3, M4, M14 (deployment guide) | 🟡 in progress | Open-source files, the self-hosted Compose stack behind Caddy with hardened containers, CI with tests against real services, scanning, CodeQL and a smoke test of the whole stack, Dependabot, and the self-hosting and remote-model guides. Mutation testing, fuzzing and the threat model are still to come. |
| 15 | [Exports and follow-up questions](15-exports-and-questions.md) | UI_UX_SPEC §15 P2 | 🟡 in progress | Downloads of the screening dataset, included papers, PRISMA, the manifest and the brief (Markdown, JSON, print-ready HTML), generated on request, audited, never cached, with CSV formula injection neutralised; follow-up questions in threads, masked before storage and answered in the background from the review's evidence by the brief's own loop; and the brief loop's `no_progress` stop. |
| 16 | [Documents](16-documents.md) | UI_UX_SPEC §15 P2 | 🟡 in progress | A team's own files: upload with CSRF and every check before storage, sealed on a volume (ADR-0008), malware scanning as the operator's explicit choice, text read in an isolated child process within limits (ADR-0009), masking with the broad list, the owner's review of each masked version, approved documents as evidence tagged D1, D2…, and the privacy_review gate resumed by the last decision. |

Planned phases get their file, from [`TEMPLATE.md`](TEMPLATE.md), when their work starts. M12 (optional OIDC single sign-on) is in the plan but has no phase assigned yet.

## Where the project stood at checkpoint 1

These numbers are from the checkpoint guide and were re-checked for the walkthroughs where it was possible without a database.

| Milestone | Status | Walkthrough | Notes |
| --- | --- | --- | --- |
| M0 Commit the existing work | ✅ Done | [01](01-baseline-and-quality-gates.md) | Session 1–2 search skeleton committed; `main.py` repaired |
| M1 Strip the desktop (V2) version | ✅ Done | [01](01-baseline-and-quality-gates.md) | [ADR-0001](../adr/0001-web-only-self-hosted.md) |
| M2 Python quality gates | ✅ Done | [01](01-baseline-and-quality-gates.md) | ruff, pyright, import-linter, coverage, hypothesis, pre-commit |
| M3 Open-source files | 🟡 Partial | 14 | README only; CONTRIBUTING, SECURITY.md, code of conduct, templates missing |
| M4 Containers and CI | 🟡 Partial | 14 | Development Compose only; no Dockerfile, Caddy or GitHub Actions |
| M5 Configuration and persistence | ✅ Done | [02](02-configuration-and-persistence.md) | Settings, storage seam, Postgres, migrations, middleware |
| M6 Envelope encryption | ✅ Done | [03](03-envelope-encryption.md) | Per-project keys, crypto-shredding, key rotation |
| M7 Audit log | ✅ Done | [04](04-audit-log.md) | Hash chain, append-only, verifiable |
| M8 Accounts and sessions | ✅ Done | [05](05-accounts-and-sessions.md) | Plus the concurrency and resource-safety hardening |
| M9 Multi-factor authentication | ✅ Done | [06](06-mfa.md) | Passkeys, TOTP, recovery codes; G1 closed |
| M10 Projects and roles | ⬜ | 07 | Owner, reviewer, viewer |
| M11 SSRF-guarded outbound HTTP | ⬜ | 09 | |
| M12 Optional OIDC single sign-on | ⬜ | — | |
| M13 Web UI | ⬜ | 13 | React, strict TypeScript |
| M14 Security documentation | 🟡 Partial | 14 | Control map done; threat model to do |

Rough completion at the checkpoint: **about 44% of the foundation plan**, and **about 15% of the whole product** in the brief (the review workflow itself — search, masking, screening, Q&A — has not started). Treat both as ±10 points.

Test suite at the checkpoint: **291 tests**. Without Postgres and Valkey, `uv run pytest --cov` gives **268 passed, 23 skipped** and about **90%** branch coverage (re-run for these walkthroughs). With `TEST_DATABASE_URL` and `TEST_VALKEY_URL` set, all 291 run; the checkpoint recorded **94%** branch coverage for that run. The coverage gate is 85%. Ruff, pyright and import-linter reported no errors at the checkpoint.

What works end to end:

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

Deliberately not built yet: multi-factor authentication, projects and roles, the web UI, real paper sources, document upload, masking, screening, indexing, Q&A, production containers and CI. Each is planned; none is half-built.

## Threats and where they are handled

The brief's 18 rules are the requirements; [`security/controls.md`](../security/controls.md) maps each built control to its test. A compact view, with the walkthrough that explains each mitigation:

| Threat | Mitigation in place | Walkthrough |
| --- | --- | --- |
| Password guessing | Long-password policy, screening, Argon2id, throttling counted before verification | [05](05-accounts-and-sessions.md) |
| Learning which accounts exist | Identical responses and work for unknown accounts; identical reset responses | [05](05-accounts-and-sessions.md) |
| Stolen database copy | Only token hashes; Argon2 password hashes; data keys wrapped by a KEK not in the database | [03](03-envelope-encryption.md), [05](05-accounts-and-sessions.md) |
| Stolen session cookie | `HttpOnly`, short idle timeout, server-side revocation, sign-out-everywhere | [05](05-accounts-and-sessions.md) |
| Session fixation | New token at every sign-in | [05](05-accounts-and-sessions.md) |
| Cross-site request forgery | Origin/Fetch-Metadata check, per-session CSRF token, JSON-only bodies | [05](05-accounts-and-sessions.md) |
| Cross-site scripting reading tokens | Session token is `HttpOnly`; API responses carry a deny-all CSP | [02](02-configuration-and-persistence.md), [05](05-accounts-and-sessions.md) |
| Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` | [02](02-configuration-and-persistence.md) |
| Compromised API process | App role cannot change schema or rewrite audit history | [02](02-configuration-and-persistence.md), [04](04-audit-log.md) |
| Tampering with history | Hash chain, append-only grants and trigger, `verify-audit` | [04](04-audit-log.md) |
| Data that must be deleted | Crypto-shredding of per-project keys | [03](03-envelope-encryption.md) |
| Leaking data in errors or logs | Problem documents never echo input; log redaction | [02](02-configuration-and-persistence.md) |
| Overload and denial of service | Bounded pool with 503, body limit, throttling, no connection held across slow work | [02](02-configuration-and-persistence.md), [05](05-accounts-and-sessions.md) |
| Race conditions | Conditional updates, optimistic locking, atomic claims, atomic rate limiting | [05](05-accounts-and-sessions.md) |
| Secrets in the repository | Git-ignored `.env` and `secrets/`, gitleaks hook, Docker secrets in production | [00](00-setup.md), [01](01-baseline-and-quality-gates.md) |

## Recommended order from here

1. Your end-to-end pass through walkthroughs 00 to 05.
2. ~~Fix gap G1 — Valkey outage answers `500` instead of `503`~~ — done in [06](06-mfa.md).
3. An independent security review of everything built so far (gap G13: everything here is self-checked; before any real data, an independent review, then a professional assessment).
4. ~~M9 — multi-factor authentication~~ — [06](06-mfa.md).
5. M10 — projects and roles; protect the search endpoints.
6. M3 and M4 — open-source files, containers, CI with mutation testing, fuzzing and SAST.
