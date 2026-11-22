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
| 10 | [Masking](10-masking.md) | [architecture §3.9](../architecture.md#39-masking) | ✅ done | Masking as derived, reviewable spans over encrypted raw text, several local detectors, project masking rules, human review before use, and a last check on everything that leaves. |
| 11 | [Discover, ingest and screening](11-screening.md) | UI_UX_SPEC §15 P1 | ✅ done | Discover across every enabled source with merged duplicates, kept text for masking, screening of masked text with full provenance on every `ScreeningDecision`, PRISMA counts, the run manifest, and the paper routes. |
| 12 | [Decision brief](12-decision-brief.md) | [architecture §4.2](../architecture.md#42-reviews) | ✅ done | The `answer` stage: a plain-Python agent loop (planner, BM25 retriever, writer, code checks, critic) that writes a `DecisionBrief` from masked passages with citations checked against what was retrieved, stored once per run, sealed, with its trace; and the routes to read the brief, a cited source and how it was made ([ADR-0007](../adr/0007-bm25-retrieval-for-the-brief.md)). |
| 13 | [Web UI](13-web-ui.md) | M13, [UI spec](../UI_UX_SPEC.md#15-delivery-phases) phase P1 | ✅ done | The React web app with strict TypeScript and generated API types: sign-in and MFA, projects, the workspace chat, and reading a brief. |
| 14 | [Deploy and CI](14-deploy-and-ci.md) | M3, M4, M14 (deployment guide) | ✅ done | Open-source files, the self-hosted Compose stack behind Caddy with hardened containers, CI with tests against real services, scanning, CodeQL and a smoke test of the whole stack, Dependabot, and the self-hosting and remote-model guides. Mutation testing, fuzzing and the threat model are still to come. |
| 15 | [Exports and follow-up questions](15-exports-and-questions.md) | UI_UX_SPEC §15 P2 | ✅ done | Downloads of the screening dataset, included papers, PRISMA, the manifest and the brief (Markdown, JSON, print-ready HTML), generated on request, audited, never cached, with CSV formula injection neutralised; follow-up questions in threads, masked before storage and answered in the background from the review's evidence by the brief's own loop; and the brief loop's `no_progress` stop. |
| 16 | [Documents](16-documents.md) | UI_UX_SPEC §15 P2 | ✅ done | A team's own files: upload with CSRF and every check before storage, sealed on a volume (ADR-0008), malware scanning as the operator's explicit choice, text read in an isolated child process within limits (ADR-0009), masking with the broad list, the owner's review of each masked version, approved documents as evidence tagged D1, D2…, and the privacy_review gate resumed by the last decision. |
| 17 | [P3: organise projects](17-p3-organise.md) | UI_UX_SPEC §15 P3 | ✅ done | Shared collections (ADR-0016), tags and per-user pins; the All projects page with cards and list, grouping, sorting, filters in the URL and bulk actions; Home's Pinned and Collections; collections in the rail. |
| 17 | [P3: history](17-p3-history.md) | UI_UX_SPEC §15 P3 | ✅ done | Needs you on Home and in the rail, recent activity, the project timeline, and seen and saved marks on papers, all derived from records already kept (ADR-0017). |
| 17 | [P3: compare](17-p3-compare.md) | UI_UX_SPEC §15 P3 | ✅ done | Brief versions with a version menu and "Compare with", the run manifest, and the Compare runs screen, all computed by the API. |
| 17 | [P3: brief](17-p3-brief.md) | UI_UX_SPEC §15 P3 | ✅ done | Trade-off priorities kept in the URL, humanitarian criteria first, one card per criterion on narrow screens, and the optional "Your view first" question. |
| 17 | [P3: find](17-p3-find.md) | UI_UX_SPEC §15 P3 | ✅ done | Search across a person's projects without an index (ADR-0018), the Ctrl K search and command palette, and the rest of the §11.5 keyboard, with single-key shortcuts that can be turned off. |
| 18 | [P4: retention](18-p4-retention.md) | UI_UX_SPEC §15 P4 | ✅ done | An operator's retention period, off by default; "Deleted in N days unless kept" in Needs you, the date in Project settings, Keep, and deletion when due through the normal delete (ADR-0020). |
| 18 | [P4: coverage](18-p4-coverage.md) | UI_UX_SPEC §15 P4 | ✅ done | The coverage estimate on the screening results: a capture-recapture range across paper sources, always called an estimate, or no estimate and why (ADR-0021); recorded in the run's manifest. |
| 18 | [P4: evaluations](18-p4-evaluations.md) | UI_UX_SPEC §15 P4 | ✅ done | The admin area's evaluations: humani-t's answers against other systems', judged blind by a model the operator configured, inside a project the administrator belongs to (ADR-0022). |
| 18 | [P4: field](18-p4-field.md) | UI_UX_SPEC §15 P4 | ✅ done | Low data mode (system fonts, no motion, run progress every 10 s, offered once on a slow connection) and the brief as a PDF from the browser's print page (ADR-0019). |

Planned phases get their file, from [`TEMPLATE.md`](TEMPLATE.md), when their work starts. M12 (optional OIDC single sign-on) is in the plan but has no phase assigned yet.

## Where the project stands

As of 21 November 2026, everything in the plan is built, tested and merged to `main`: the security core (M0–M14, without the optional M12 single sign-on) and phases P1 to P4 of the [UI spec](../UI_UX_SPEC.md#15-delivery-phases).

- **Checked on every change:** the API suite against Postgres and Valkey (about 3,500 tests), ruff, pyright, the layering rules, the web lint and type check, about 640 vitest tests, the Playwright browser tests, the image and dependency scans, CodeQL, and a smoke test of the whole Compose stack on x86 and Arm.
- **Seen end to end:** a real review with a hosted model (gpt-5-mini) from question to brief, document upload and its masking review, and the P3 and P4 screens in a browser.
- **Off until the operator turns them on:** sending unmasked text to a hosted model (ADR 0014), the retention period (ADR 0020) and evaluation admin (ADR 0022).

Nothing here has had an independent security review. Until it has, use fictional or already-public data only.

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
| A request to an internal address (SSRF) | One guarded fetcher with an allow list, checked again at connect time and on every redirect | [09](09-plan-and-discover.md) |
| Personal data reaching a hosted model | Masking before anything is sent, project rules, the owner's review of each document, a last check on every call | [10](10-masking.md), [16](16-documents.md) |
| A malicious upload | Type and size checks before storage, malware scanning, text read in an isolated child process within limits | [16](16-documents.md) |
| Uploaded text giving the model instructions | Passed only as data in structured requests, with output limited to a schema | [16](16-documents.md), [18 — evaluations](18-p4-evaluations.md) |
| Learning that someone else's project exists | The same `404` as for a missing project, in every list, count, search and activity line | [07](07-projects-and-roles.md), [17 — find](17-p3-find.md) |

## What comes next

1. **An independent security review** of everything built, then a professional assessment, before any real data goes in.
2. **Hosting:** try one of the deployment guides on a real server ([Oracle](../deploy/oracle-cloud.md) or another host); none has been run end to end yet.
3. **Gaps found in P4:** the first screen's JavaScript is about 44 KB over its 170 KB budget, and Lighthouse does not check it in CI ([18 — field](18-p4-field.md)); evaluations cannot be deleted or rerun, and the admin screens for users and invitations are not built ([18 — evaluations](18-p4-evaluations.md)); printing the brief in Safari is not checked.
4. **The spec's open questions** that are still open ([UI_UX_SPEC §16](../UI_UX_SPEC.md#16-open-questions)): phones, languages (French and Arabic), notifications, sharing, and who sees raw-text views in the audit log.
5. **Masking quality:** a larger name model would catch names the small one misses ([10 — masking](10-masking.md)).
