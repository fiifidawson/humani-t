# API Reference — v1

Interactive docs at `/docs` (Swagger) and `/redoc` when the server is running.

```bash
cd services/api
uv run uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

Then <http://127.0.0.1:8731/docs>.

> Port 8731 rather than 8000: if `mkdocs serve` is running for `docs-site/` it already holds 8000, and on Windows both processes can bind it without either reporting an error — uvicorn claims to be running while the browser shows the docs site.

**Status: walking skeleton.** Search is served by an in-memory corpus. No request leaves the machine. Making it live is a change to [`app/api/dependencies.py`](../services/api/app/api/dependencies.py) alone — the endpoints and payloads below do not change.

---

## Endpoints

| Method | Path | Tag | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | system | Liveness |
| `GET` | `/api/v1/ready` | system | Readiness: can this process serve requests |
| `GET` | `/api/v1/info` | system | Which implementations are wired in (not in production) |
| `GET` | `/api/v1/sources` | search | Repositories this process can reach (signed in) |
| `POST` | `/api/v1/vocabulary/compile` | vocabulary | Vocabulary to Boolean query (signed in) |
| `POST` | `/api/v1/projects/{project_id}/search` | search | Compile, then search, inside a project (owner, reviewer) |
| `POST` | `/api/v1/auth/login` | auth | Check the password; sets the session and CSRF cookies; `status` says what comes next |
| `POST` | `/api/v1/auth/logout` | auth | End this session |
| `GET` | `/api/v1/auth/session` | auth | Who is signed in |
| `POST` | `/api/v1/auth/reauthenticate` | auth | Confirm the password before a sensitive action |
| `POST` | `/api/v1/auth/register` | auth | Accept an invitation; signs in |
| `POST` | `/api/v1/auth/password-reset` | auth | Email a reset link (always 202) |
| `POST` | `/api/v1/auth/password-reset/confirm` | auth | Set a new password from a reset link |
| `GET` | `/api/v1/account/sessions` | account | Where this account is signed in |
| `DELETE` | `/api/v1/account/sessions/{id}` | account | Sign out one device |
| `POST` | `/api/v1/account/sessions/revoke-others` | account | Sign out every other device |
| `POST` | `/api/v1/account/password` | account | Change the password |
| `POST` | `/api/v1/auth/mfa/totp` | auth | Complete a pending sign-in with an authenticator-app code |
| `POST` | `/api/v1/auth/mfa/recovery-code` | auth | Complete a pending sign-in with a recovery code |
| `POST` | `/api/v1/auth/mfa/passkey/options` | auth | Start a passkey sign-in (pending session) |
| `POST` | `/api/v1/auth/mfa/passkey` | auth | Complete a pending sign-in with a passkey |
| `GET` | `/api/v1/account/mfa` | account | Which second factors this account has |
| `POST` | `/api/v1/account/mfa/totp` | account | Start adding an authenticator app (recent sign-in) |
| `POST` | `/api/v1/account/mfa/totp/confirm` | account | Activate it with a code |
| `DELETE` | `/api/v1/account/mfa/totp` | account | Remove it (recent sign-in; not the last factor) |
| `POST` | `/api/v1/account/mfa/passkeys/options` | account | Start adding a passkey (recent sign-in) |
| `POST` | `/api/v1/account/mfa/passkeys` | account | Finish adding a passkey (recent sign-in) |
| `DELETE` | `/api/v1/account/mfa/passkeys/{id}` | account | Remove a passkey (recent sign-in; not the last factor) |
| `POST` | `/api/v1/account/mfa/recovery-codes` | account | Replace the recovery codes (recent sign-in) |
| `POST` | `/api/v1/admin/invitations` | admin | Invite someone by email |
| `POST` | `/api/v1/admin/users/{id}/disable` | admin | Disable an account |
| `POST` | `/api/v1/admin/users/{id}/enable` | admin | Let a disabled account sign in again |
| `POST` | `/api/v1/projects` | projects | Create a project; the caller becomes its owner |
| `GET` | `/api/v1/projects` | projects | The caller's projects, newest first |
| `GET` | `/api/v1/projects/{project_id}` | projects | One project (any member) |
| `PATCH` | `/api/v1/projects/{project_id}` | projects | Rename (owner) |
| `POST` | `/api/v1/projects/{project_id}/archive` | projects | Hide from the default list (owner) |
| `POST` | `/api/v1/projects/{project_id}/unarchive` | projects | Bring back (owner) |
| `DELETE` | `/api/v1/projects/{project_id}` | projects | Delete by destroying the project's key (owner, recent sign-in) |
| `GET` | `/api/v1/projects/{project_id}/members` | projects | Who belongs to the project (any member) |
| `POST` | `/api/v1/projects/{project_id}/members` | projects | Give an existing account a role (owner, recent sign-in) |
| `PATCH` | `/api/v1/projects/{project_id}/members/{user_id}` | projects | Change a member's role (owner, recent sign-in) |
| `DELETE` | `/api/v1/projects/{project_id}/members/{user_id}` | projects | Remove a member (owner, recent sign-in), or leave (anyone) |
| `POST` | `/api/v1/projects/{project_id}/reviews` | reviews | Ask a question in the project (owner, reviewer) |
| `GET` | `/api/v1/projects/{project_id}/reviews` | reviews | The project's reviews, newest first (any member) |
| `GET` | `/api/v1/projects/{project_id}/reviews/{review_id}` | reviews | One review (any member) |
| `POST` | `/api/v1/reviews/{review_id}/runs` | runs | Start a run of some stages; `202` at once (owner, reviewer) |
| `GET` | `/api/v1/reviews/{review_id}/runs` | runs | The review's runs, newest first (any member) |
| `GET` | `/api/v1/runs/{run_id}/events` | runs | Follow a run as Server-Sent Events (any member) |
| `POST` | `/api/v1/runs/{run_id}/cancel` | runs | Cancel a run; finished stages are kept (owner, reviewer) |
| `POST` | `/api/v1/gates/{gate_id}` | runs | Decide a gate so the run continues (owner; reviewer for a vocabulary) |

---

### `GET /api/v1/health`

Answers *is this process up*, not *is the whole system healthy*. Touches no provider and no database — conflating the two makes deploys hard to debug, because a failing dependency then looks like a dead process.

```json
{ "status": "ok" }
```

---

### `GET /api/v1/ready`

Answers *can this process serve requests*: the database answers a query. The reverse proxy uses it to hold traffic back while the database is down, without restarting a process that is itself fine.

```json
{ "status": "ready" }
```

`503` with a problem document when a backing service is unreachable.

---

### `GET /api/v1/info`

Reports which implementations are active behind the interfaces. **Not mounted in production**, and neither are `/docs`, `/redoc` or `/openapi.json`: they map the system for anyone who asks.

```json
{
  "name": "humani-t API",
  "version": "0.1.0",
  "api_prefix": "/api/v1",
  "search_provider": "fake",
  "makes_network_calls": false
}
```

The whole design rests on implementations being swappable. This endpoint makes the current choice visible instead of buried in a config file — and `makes_network_calls` is the fastest way to confirm a deployment is not talking to anyone it should not.

---

### `GET /api/v1/sources`

Needs a signed-in user: the list describes the deployment's configuration.

```json
[
  {
    "source_id": "fake",
    "live": false,
    "description": "In-memory corpus of four papers. No network, no key, no rate limit."
  }
]
```

One entry today. It becomes the list a reviewer checks to know what a run covered — an unexpected entry here is the first sign a run is not reproducible.

---

### `POST /api/v1/vocabulary/compile`

Needs a signed-in user (and, like every signed-in write, the `X-CSRF-Token` header), but no project: compiling is pure and reads no project data.

Shows exactly what will be searched for, before anything is searched.

**Request**

```json
{
  "origin": "llm_assisted",
  "version": 1,
  "concepts": [
    {
      "term": "chatbot",
      "synonyms": ["conversational agent"],
      "theme": "technology"
    },
    {
      "term": "humanitarian",
      "synonyms": ["crisis response"],
      "theme": "setting"
    }
  ]
}
```

`origin` is `llm_assisted` or `user_supplied`. The latter marks the vocabulary **strict**: used exactly as given, with no model-driven expansion.

**Response**

```json
{
  "rendered": "(\"chatbot\" OR \"conversational agent\") AND (\"humanitarian\" OR \"crisis response\")",
  "themes": [
    {
      "theme": "technology",
      "groups": [{ "concept": "chatbot", "terms": ["chatbot", "conversational agent"] }]
    },
    {
      "theme": "setting",
      "groups": [{ "concept": "humanitarian", "terms": ["humanitarian", "crisis response"] }]
    }
  ],
  "term_count": 4,
  "provenance": {
    "vocabulary_version": 1,
    "vocabulary_origin": "llm_assisted",
    "is_strict": false
  }
}
```

`rendered` is for humans — logs, review screens, the audit trail. It is **not** what gets sent to a repository; each provider compiles the structured form into its own dialect.

The endpoint is pure and deterministic: the same vocabulary always produces the same query, and no model is consulted for either origin. That is what makes a user-supplied vocabulary strict by construction rather than by discipline.

Invalid input returns `422` — Pydantic checks shape, the domain checks meaning.

---

### `POST /api/v1/projects/{project_id}/search`

Compiles the vocabulary and runs it against the configured source, on behalf of a project. Owners and reviewers may search (`run.manage`); a viewer gets `403` and a non-member `404`. It is scoped to a project because results become the project's evidence and, with real sources, a search is a request leaving the deployment on the project's behalf.

**Request** — the compile body plus `limit` (1–200, default 25).

**Response**

```json
{
  "source_id": "fake",
  "query": { "rendered": "...", "themes": [], "term_count": 4, "provenance": {} },
  "total": 2,
  "results": [
    {
      "source_id": "fake",
      "external_id": "fake-001",
      "title": "Conversational agents for crisis response in humanitarian settings",
      "year": 2024,
      "doi": "10.1000/fake.001",
      "is_open_access": true,
      "abstract": "...",
      "url": "https://example.org/fake-001",
      "identity": "doi:10.1000/fake.001"
    }
  ]
}
```

Two things worth noticing.

**The query travels with the results.** A result set separated from the query that produced it cannot be audited, and auditability is the point of the system.

**Every record carries an `identity`.** DOI when present, otherwise normalised title plus year. It is the key that collapses the same paper arriving from four sources into one record.

---

## Projects

A project is the unit of access, encryption and deletion. Everything below `/api/v1/projects/{project_id}` answers in this order:

1. `401` if not signed in; `403` if a write lacks the CSRF token;
2. `404` if the project does not exist **or** the caller is not a member, with the same body either way: a project's existence is never confirmed to someone outside it, not even to an instance administrator;
3. `403` if the caller's role does not allow the action;
4. `401` with `"reauthentication_required": true` if the action needs a sign-in within the last 15 minutes (delete, adding a member, changing a role, removing someone else);
5. then the request itself: `422` for bad input, `409` for a conflict.

Steps 1 to 4 happen before the request body is read, so a non-member learns nothing, not even whether their body was valid.

### Roles

| Action | Owner | Reviewer | Viewer |
| --- | --- | --- | --- |
| Read the project and its members | yes | yes | yes |
| Search, start or cancel runs, confirm gates, edit labels, ask questions | yes | yes | no |
| Upload documents, see raw text | yes | no | no |
| Rename, archive, manage members, delete | yes | no | no |
| Leave | yes, unless the last owner | yes | yes |

The table is the domain function `can(role, action)` in `app/domain/projects.py`; anything it does not list is refused. Being an instance administrator grants nothing inside a project.

### `ProjectOut`

```json
{
  "id": "6f1c…",
  "name": "Cholera response, Sana'a",
  "role": "owner",
  "created_at": "2026-11-03T09:00:00Z",
  "archived_at": null
}
```

`role` is the caller's own role. The name is stored encrypted under the project's own key; only members ever see it in the clear.

### `POST /api/v1/projects`

**Request** `{"name": "Cholera response, Sana'a"}`. The name is trimmed and must be 1 to 200 characters with no control or bidirectional-override characters (`422` otherwise). Unknown fields are refused (`422`), so the owner, id and dates cannot be set by the client.

**Response** `201` with a `ProjectOut` and `Location: /api/v1/projects/{id}`. The project gets its own data key in the same transaction, and the caller becomes its owner.

### `GET /api/v1/projects`

The caller's projects, newest first, each with the caller's role. Archived projects are left out unless `?include_archived=true`.

### `GET`, `PATCH /api/v1/projects/{project_id}`, `POST …/archive`, `POST …/unarchive`

- `GET` returns the `ProjectOut` to any member.
- `PATCH` with `{"name": "…"}` renames (owner), with the same rules as on create.
- `POST …/archive` hides the project from the default list and keeps everything; archiving an archived project keeps the first date. `POST …/unarchive` brings it back. Owner only. An archived project is not read-only.

### `DELETE /api/v1/projects/{project_id}`

Owner only, with a sign-in in the last 15 minutes. Destroys the project's data key (crypto-shredding), removes every membership and keeps the project row as a tombstone. Answers `204`; from then on the project answers `404` to everyone, exactly as if it never existed. Every ciphertext of the project (its name, encrypted audit details, later its documents) becomes permanently unreadable, including copies in backups once the backup retention window has passed. The audit chain still verifies. Irreversible; asking the user to type the name first is the web app's job.

### Members

`MemberOut`:

```json
{ "user_id": "2b9e…", "display_name": "Kofi Mensah", "role": "reviewer", "added_at": "2026-11-03T09:05:00Z" }
```

No email addresses: members are shown by display name.

- `GET …/members`: every member, earliest added first. Any member.
- `POST …/members` with `{"email": "kofi@example.org", "role": "reviewer"}`: owner, recent sign-in. `201` with a `MemberOut`. The email is matched case-insensitively against **existing, enabled** accounts; anything else gets `422` with the same body, which never repeats the email. `409` if the person is already a member. `role` is exactly `owner`, `reviewer` or `viewer`.
- `PATCH …/members/{user_id}` with `{"role": "viewer"}`: owner, recent sign-in. `200` with the `MemberOut`; `404` if that user is not a member.
- `DELETE …/members/{user_id}`: removing someone else is for owners, with a recent sign-in; removing yourself (leaving) is for anyone and needs no recent sign-in. `204`.

The last owner can neither be demoted nor removed, nor leave: `409` with `"last_owner": true`. Membership changes on one project are made one at a time ([ADR-0002](adr/0002-project-row-lock-for-membership-changes.md)), so two owners stepping down at once still leave one owner.

### Audit

Every project action is recorded with ids only: `project.created`, `project.renamed`, `project.archived`, `project.unarchived`, `project.deleted` (target: the project), and `membership.added`, `membership.role_changed`, `membership.removed` (target: the member's user id). Each carries the project's id as `scope_id`, the actor and the request id. Refused attempts are not recorded.

---

## Reviews and runs

A review is a question inside a project. A run executes some of the review's pipeline stages as a background job; the browser follows it on one Server-Sent Events stream and answers its gates with ordinary `POST`s ([architecture §4.4](architecture.md#44-runs-live-events-and-gates), [ADR-0003](adr/0003-run-queue-and-event-fan-out.md)).

The run, event and gate paths name a review, run or gate rather than the project. The API finds the project from that id and then answers exactly as on a project route: `401`, then `404` for a non-member with the same body as for an id that does not exist, then `403` for a role that may not, then the request itself. Routes with a body check access before reading it.

The stages are fake until the real ones are built: each counts through five items, half a second apart; `mask` first asks for a `privacy_review` gate, and `answer` reports agent steps. The contract below does not change when real stages replace them.

### `ReviewOut`

```json
{
  "id": "1d0e…",
  "project_id": "6f1c…",
  "question": "Do cash transfers reduce acute malnutrition among displaced children?",
  "status": "new",
  "created_at": "2026-11-03T09:10:00Z",
  "created_by": {"id": "2b9e…", "display_name": "Amara Okafor"}
}
```

`created_by` is who asked the question, with the display name the members list shows (never an email address); an account that no longer exists shows as `"Unknown account"`. Like everything about a review, only members of the project see it. `status` follows [UI_UX_SPEC §11.1](UI_UX_SPEC.md#111-review-and-run-states): `new`, `clarifying`, `drafting`, `changes_requested`, `accepted`, then the latest run's `running`, `waiting`, `done`, `failed` or `cancelled`. The question is stored encrypted under the project's key.

### `POST /api/v1/projects/{project_id}/reviews`

**Request** `{"question": "…"}`: trimmed, 1 to 2,000 characters; line breaks are allowed, other control characters and bidirectional overrides are not (`422`). Unknown fields are refused. Owner or reviewer. **Response** `201` with a `ReviewOut` and `Location`. Audited as `review.created`.

`GET …/reviews` lists the project's reviews, newest first; `GET …/reviews/{review_id}` returns one. Any member. A review id from another project answers `404`.

### `POST /api/v1/reviews/{review_id}/runs`

**Request** `{"stages": ["discover", "queries"]}`: any of `queries`, `discover`, `ingest`, `mask`, `screen`, `export`, `index`, `answer`, in any order; they run in that pipeline order. `{"stages": []}` or `{}` runs the whole pipeline, every stage in that order. Unknown or repeated stages are `422`. Owner or reviewer.

**Response** `202` with `{"run_id": "…"}` and `Location: /api/v1/runs/{run_id}/events`. The run is queued; a worker (`python -m app.worker`) picks it up. With in-memory storage the API process works runs itself, since a separate worker cannot see its memory (`HUMANI_T_RUN_WORKER_IN_PROCESS`, refused in production). `409` while another run of the same review is queued, running or waiting. Audited as `run.started`.

`GET /api/v1/reviews/{review_id}/runs` lists the review's runs, newest first, to any member:

```json
{
  "id": "8a2c…", "review_id": "1d0e…", "stages": ["queries", "discover"],
  "state": "done", "outcome": "done", "stages_finished": 2, "cancel_requested": false,
  "created_at": "…", "started_at": "…", "finished_at": "…"
}
```

`state` is `queued`, `running`, `waiting`, `done`, `failed` or `cancelled`; `outcome` is set once the run has finished.

### `GET /api/v1/runs/{run_id}/events`

`text/event-stream`, any member. The stream first sends `retry: 3000`, then every stored event after `Last-Event-ID`, then follows the run live:

```text
retry: 3000

id: 1
event: stage.started
data: {"at":"2026-11-03T09:11:00+00:00","position":1,"stage":"queries","total":2}

id: 2
event: stage.progress
data: {"done":1,"item":"Item 1 of 5","stage":"queries","total":5}

: keep-alive

```

| Event | `data` |
| --- | --- |
| `stage.started` | `stage`, `position` and `total` (within this run), `at` |
| `stage.progress` | `stage`, `done`, `total`, `item` (a label; masked text only), `at`; for the screen stage also `screened` |
| `stage.finished` | `stage`, `seconds`, `summary` (the stage's counts) |
| `agent.step` | `round` (0 for the planner), `role`, `status`, then per-role fields |
| `gate.required` | `gate_id`, `kind` (`vocabulary` or `privacy_review`), `payload` (a `VocabularyGatePayload` for a vocabulary gate); the run waits |
| `gate.resolved` | `gate_id`, `by` (the member's user id), `decision`; the run is queued again |
| `job.finished` | `outcome` (`done`, `failed`, `cancelled`), `seconds`, `error` (plain language, or `null`) |

Each event is published in the OpenAPI document as a component (`StageStartedEvent` … `JobFinishedEvent`, each `{id, event, data}`), and `RunStreamEvent` is their union, discriminated by `event`; it is the schema of this route's `text/event-stream` response. Generate client types from it rather than writing them by hand.

Typed payloads, all published as components:

- **`stage.progress.at`**: when the step happened (ISO 8601 with offset). Absent on events stored before November 2026.
- **`stage.progress.screened`** (`ScreenedItemData`), on each screen-stage row: `{"paper_id", "outcome": "screened"|"failed", "label", "priority", "tier", "title", "reason"}`. `paper_id` is the id `GET …/papers` lists. A `failed` row is a paper the privacy check stopped: `label`, `priority`, `tier` and `title` are `null` and `reason` says why. `title` is the first line of the masked text the model read; `reason` is one line of its rationale. Masked text only, like `item`.
- **`gate.required.payload`** for a `vocabulary` gate is a `VocabularyGatePayload`: `{"version", "origin", "is_strict", "themes": [{"name", "concepts": [{"term", "synonyms"}]}], "criteria": [{"id", "kind", "text"}], "rendered"}`, the draft as the draft canvas shows it. A `privacy_review` payload stays an open object.

**Deprecated, kept for now:** on screen-stage rows, `stage.progress.item` (`"HIGH · <masked title> — <one-line reason>"`) says what `screened` says; read `screened` instead. `item` stays the label for every other stage.

- **Ids** count from 1 within the run with no gaps. Every event is stored, encrypted under the project's key, before it is sent.
- **Reconnecting.** `EventSource` resends the last id it saw as `Last-Event-ID`; the server replays everything after it. A new `EventSource`, which cannot set that header, passes the id as `?last_event_id=<id>` instead; when both are present, the header wins. An id the run does not have (malformed, `0`, negative, or beyond the run's latest event) replays from the start, so a client may see an event twice but never misses one: apply events by id.
- **Ending.** The stream ends after `job.finished`, so a finished run replays its history and closes. It also ends, within a minute, when the session is revoked or expires or the member leaves the project; the browser's reconnect then gets `401` or `404`. Watching a stream does not count as activity: the session still idles out after an hour.
- **Quiet runs** (one waiting at a gate) get a `: keep-alive` comment every 15 seconds.
- **A disconnect changes nothing** for the run. Any API instance can serve any stream.

### `POST /api/v1/runs/{run_id}/cancel`

Owner or reviewer. `202` with the run. A queued or waiting run ends at once (`state: "cancelled"`, and `job.finished` with `outcome: "cancelled"` on the stream). A running run answers `state: "running"` with `cancel_requested: true` and ends at its stage's next checkpoint. Finished stages are kept either way. `409` if the run has already finished. Audited as `run.cancelled`.

### `POST /api/v1/gates/{gate_id}`

**Request** `{"decision": "approve" | "reject", "revisions": {…} | null}`. `revisions` is any JSON object the person changed (for example an edited vocabulary); it is stored encrypted and handed to the stage. **Who:** a `vocabulary` gate needs an owner or reviewer; a `privacy_review` gate needs an owner, because approving masking means reading raw text.

**Response** `200` with `{"gate_id", "decision", "by"}`. The first decision wins: `gate.resolved` goes to every open stream and the run is queued again; the stage that asked receives the decision and decides what follows. A second decision, or one on a run that is no longer waiting (it was cancelled), gets `409`. Audited as `gate.resolved`.

### Audit

`review.created` (target: the review), `run.started` and `run.cancelled` (target: the run), `gate.resolved` (target: the gate), each with the project as `scope_id`, the actor and the request id. Ids only: never the question, a stage's output or a decision's revisions.

---

## Papers, screening and the manifest

Read-only routes for every member, viewers included (`project.read`). Each resolves the project from the review or run in its path: 401 when signed out, and the same 404 for a non-member as for a missing id. `?run_id=` names a run of this review; left out, the review's newest run that has papers is used. A run started without Discover (a retry from Screen, say) works from the newest earlier discovery, so its papers, tags and counts are that run's. Paper text is served masked only (`masked_text`); a paper's raw kept text has no field anywhere. Titles, years, DOIs and links are the paper's public bibliographic record.

### `PaperOut`

`{"paper_id", "tag", "position", "title", "year", "doi", "url", "source_id", "found_by", "is_open_access", "decision", "screening_status", "skipped_because"}`. `url` is the record's own link, else `https://doi.org/<doi>` when it has a DOI (public metadata), else `null`. `screening_status` is `screened` (it has a decision), `skipped` (the screen stage had no masked abstract to send; `skipped_because` is `no_abstract` or `not_masked`), `failed` (the privacy check stopped its text, so nothing was sent; it is tried again next run) or `not_screened` (this run has not screened it yet). `tag` is `P` and the paper's place in the run's merged list (`P1`, `P12`…); it is how the brief cites the paper and does not change when labels do. `source_id` is the source whose record was kept; `found_by` every source that returned it. `decision` is `null` until screened, else `DecisionOut`: `{"label": "include"|"exclude"|"uncertain", "priority": "high"|"medium"|"low", "tier", "score", "rationale", "criteria_matched", "exclusion_reasons", "is_unreadable", "model_id", "prompt_version", "criteria_version", "evidence_kind", "decided_at"}`. `tier` is the name shown (UI_UX_SPEC §6.8): `highly_relevant`, `relevant`, `possibly_relevant` (an inclusion by priority), `excluded`, `uncertain`. `criteria_matched` names the criteria (`I1`, `E1`…) the paper meets; an exclusion's `exclusion_reasons` are the exclusion criteria it meets, then the inclusion criteria it misses. `is_unreadable` marks the `uncertain` decision recorded when the model's answer could not be read twice. `criteria_version` is the plan version whose criteria were applied (read it with `GET …/plan/versions/{criteria_version}`); `evidence_kind` is what was read (`abstract`). `criteria` is each criterion's answer in the plan's order, `[{"id": "I1", "answer": "yes"|"partly"|"no"|"unclear", "quote": str|null}]`, for the expanded row (UI_UX_SPEC §6.8): `yes` and `partly` are exactly the criteria in `criteria_matched`, and every quote is checked to be words of the masked text the model read, or the answer is refused and asked again. Empty for decisions made before prompt version `screen-2`, and when the model gave none.

### `GET /api/v1/reviews/{review_id}/papers?run_id=&label=&tier=&page=`

**Response** `200` `{"run_id", "page", "page_size": 50, "total", "counts", "papers": [PaperOut]}`, most relevant first (by tier, then place). `tier` filters by chip: `highly_relevant`, `relevant`, `possibly_relevant`, `excluded`, `uncertain`, `skipped`, `failed` or `not_screened`. `label` (`include`, `exclude`, `uncertain` or `unscreened`) still works and combines with `tier`. Anything else is `422`. `total` counts the papers matching the filter; `counts` the run's papers per tier, `unscreened` (every paper without a decision) and, of those, `skipped`, `failed` and `not_screened`, for the filter chips. A page past the end is empty. `404` when the review has no papers yet, or `run_id` is not one of its runs.

### `GET /api/v1/reviews/{review_id}/papers/{paper_id}?run_id=`

**Response** `200` `{"run_id", "paper": PaperOut, "text_kind": "abstract"|"title"|null, "masked_text": str|null}`. `masked_text` is `null` until the Mask stage has masked the paper. `404` for a paper the run did not see.

### `GET /api/v1/reviews/{review_id}/prisma?run_id=`

**Response** `200` `{"run_id", "identified", "identified_by_source", "failed_sources", "duplicates_removed", "after_deduplication", "screened", "included", "excluded", "uncertain", "not_screened", "excluded_by_reason"}`. `not_screened` is the papers with no decision: skipped (no abstract, not yet masked) or failed. `excluded_by_reason` counts exclusions per criterion id. The same counts are in the Export stage's summary.

### `GET /api/v1/runs/{run_id}/manifest`

**Response** `200` `{"run_id", "review_id", "stages", "outcome", "created_at", "started_at", "finished_at", "is_complete", "vocabulary_versions", "criteria_versions", "plan_versions", "sources", "queries", "model_ids", "prompt_versions", "papers_from_runs", "stage_facts": [{"stage", "seconds", "facts"}]}`, assembled from what each finished stage recorded under `"manifest"` in its `stage.finished` summary, never reconstructed. Versions are lists: when two stages disagree, both show. `prompt_versions` is keyed by stage (`screen`) or `stage.name` (`answer.writer`). `is_complete` is false until every requested stage has finished.

### Stage summaries

What the pipeline stages put in `stage.finished`:

- **discover:** `identified`, `by_source`, `failed_sources`, `after_deduplication`, `duplicates_removed`, `new_papers`; manifest `plan_version`, `vocabulary_version`, `sources`, `queries`, `limit_per_source`. One `stage.progress` per source (`"openalex · 25 found"`, `"arxiv · could not be searched"`).
- **ingest:** `papers`, `abstracts`, `title_only`, `full_texts` (0 until P2), `open_access_not_read`, `kept`, `already_kept`, `gaps`.
- **screen:** `papers`, `screened`, `tiers`, `unreadable`, `skipped` (`no_abstract`, `not_masked`), `failed`; manifest `model_id`, `prompt_version` (`screen-2`: with per-criterion answers and quotes), `criteria_version`, `plan_version`. One `stage.progress` per paper, with its `screened` row and its (deprecated) item `"HIGH · <masked title> — <one-line reason>"`.
- **export:** `prisma` (as above), `dataset` (`papers`, `by_source`, `years`, `open_access`, `with_abstract`), `gaps`.

A stage that worked from another run's discovery adds `papers_from_run` to its manifest.

### Audit

`screening.decided` (target: the paper, actor: the system, details: the run id), one per decision, with the project as `scope_id`. Ids only: never a title, text or rationale.

---

## Search plans

A review's plan is its vocabulary and its screening criteria, in numbered versions ([`architecture.md` §4.3](./architecture.md#43-how-a-vocabulary-is-acquired)). Route A is a conversation with the model: clarifying questions (deep mode), a draft, change requests, then accept. Route B is your own terms, uploaded and used exactly as given; the model is never called. Accepting a version freezes it; changing it afterwards makes the next version. Runs use the latest accepted version.

All paths name a review. **Who:** any member reads; owners and reviewers change. A non-member gets the same `404` as a missing review, before the body is read. Planning is refused with `409` while a run is going.

The question, every answer and every change request are masked before any model call, and each call is checked again by the prompt guard. A call the guard refuses sends nothing and answers `422`; an unusable model answer is `502`; an unreachable model server is `502`.

### `PlanOut`

```json
{
  "review_id": "…", "status": "drafting", "mode": "deep",
  "questions": [{"id": "q1", "text": "Which outcomes matter most?", "suggestions": ["Prices"], "answer": "Prices", "skipped": false}],
  "pending_change": null,
  "current": { "…": "a VersionOut" },
  "accepted_version": 1,
  "versions": [{"version": 1, "origin": "llm_assisted", "author": {"kind": "model", "user_id": null}, "created_at": "…", "accepted_at": "…"}],
  "receipts": [{"id": "…", "purpose": "draft", "created_at": "…", "created_by": "…", "model_id": "llama3.1:8b", "prompt_version": "plan.draft@1", "delivered": true, "masked_total": 1, "items": [{"kind": "question", "ref": null, "sent": "… in [LOCATION_1]?", "counts": {"LOCATION": 1}}], "plan_version": 1, "change_request_id": null}]
}
```

- `status` is the review's status (UI_UX_SPEC §11.1): `new`, `clarifying`, `drafting`, `changes_requested`, `accepted`, then the latest run's.
- `current` is the latest version, accepted or not; `accepted_version` is the one runs use.
- `receipts` are the privacy receipts (UI_UX_SPEC §5.3.2): one per model call, with exactly the masked text the model received and the masked counts by type, never the original values. `delivered: false` means the call failed after the text may have left. `plan_version` is the version the call was drafting (`null` for clarifying questions), and `change_request_id` the change request a revision was drafted from; both are set on failed calls too, so a receipt is placed by them, never by its position. A failed call's `plan_version` may later be written by a retry, whose receipt names it as well. Receipts written before these fields existed have both `null`.

`VersionOut` carries `version`, `origin` (`llm_assisted` or `user_supplied`), `is_strict`, `author` (`model` or a `person` with their id), `created_at`, `accepted_at`, `accepted_by`, `model_id` and `prompt_version` (null for a person's version), `themes` (each concept with its `term`, `synonyms` and `terms`, every term with `added_by`), `criteria` (`I1`, `E1`… with `kind` and `text`), `notes` (changes made automatically to the model's draft, such as a term dropped because it held masked text), the `change_request` that produced it, the `clarifications` it was drafted from, and `query`, the compiled Boolean query (as `POST /vocabulary/compile` returns it).

### `POST /api/v1/reviews/{review_id}/plan`

**Request** `{"mode": "shallow" | "deep"}`. Deep asks the model for 2 to 4 clarifying questions; shallow drafts at once. **Response** `201` with `PlanOut` and a `Location` header. `409` if the review already has a plan. Audited as `plan.started`.

### `GET /api/v1/reviews/{review_id}/plan`

**Response** `200` with `PlanOut`.

### `POST /api/v1/reviews/{review_id}/plan/answers`

**Request** `{"answers": [{"question_id": "q1", "answer": "Prices" | null}], "draft_now": false}`. `null` skips a question; `draft_now: true` skips every one not yet answered. Once every question is answered or skipped, the model drafts version 1. **Response** `200` with `PlanOut`. `409` if the review is not waiting for answers; `422` for an unknown question or a blank answer. Audited as `plan.answered`, then `plan.drafted`.

### `POST /api/v1/reviews/{review_id}/plan/changes`

**Request** `{"request": "add mobile money as a synonym for cash", "based_on_version": 2}`. The request is recorded first (`status` becomes `changes_requested`), then the model drafts the next version from the current one. **Response** `200` with `PlanOut`. `409` when `based_on_version` is not the latest, for your own terms (the model never changes them), or during a run. Audited as `plan.changes_requested` and `plan.revised`.

### `POST /api/v1/reviews/{review_id}/plan/accept`

**Request** `{"version": 2}`. Accepts the latest draft and freezes its vocabulary at that version. A conditional write: the first accept wins, a second one gets `409` with "Someone else already accepted this draft. Showing the latest." `409` also when the version is not the latest or a change is being drafted. **Response** `200` with `PlanOut`. Audited as `plan.accepted`.

### `PUT /api/v1/reviews/{review_id}/plan/vocabulary`

Your own search terms, used exactly as given (route B). Parsed by code; no model and no sanitiser is involved, and nothing leaves the deployment.

**Request** `{"format": "text" | "csv", "content": "…", "criteria": [{"kind": "inclusion", "text": "…"}] | null}`. Without `criteria`, the previous version's carry over.

- `text`: one concept per line, `term; synonym; synonym`. A line `[theme]` starts a theme; lines before any belong to `general`. Blank lines and lines starting with `#` are ignored.
- `csv`: a header `theme,term,synonyms`, then one concept per row, synonyms separated by `;`. A blank theme means `general`.

Each term is trimmed of surrounding spaces and otherwise kept exactly. An empty term, a term repeated within its concept and a concept repeated within its theme are refused with `422` naming the line, never silently dropped. **Response** `200` with `PlanOut`: a new, unaccepted version with `origin: user_supplied`. Audited as `plan.vocabulary_uploaded`.

### `GET /api/v1/reviews/{review_id}/plan/versions/{version}`

**Response** `200` with `{"plan": VersionOut, "diff": {"from_version", "to_version", "concepts", "terms", "criteria"}}`. Each change is `added` or `removed`, with `by`: the author of the version that made it. Matching ignores case; a concept moved to another theme shows as removed and added. `404` for a version that does not exist. Every version stays readable for good, accepted or replaced: a screening decision cites the version whose criteria it applied (`criteria_version`), so this is how its criteria are read later.

### The `queries` stage

A run's `queries` stage compiles the accepted plan into each enabled source's query. Without one it drafts a plan if none exists, then asks for a `vocabulary` gate whose payload is the draft (`version`, `origin`, `is_strict`, `themes`, `criteria`, `rendered`). Approving accepts it; `revisions` of the form `{"vocabulary": {"concepts": [{"term", "synonyms", "theme"}]}, "criteria": [{"kind", "text"}]}` first make a version written by the person who decided. Rejecting fails the run. Its summary's `manifest` holds `vocabulary_version`, `vocabulary_origin`, `criteria_version`, `boolean_query`, `compiled_queries` (source → exact query string), `sources`, `plan_model_id` and `plan_prompt_version`.

### Audit

`plan.started`, `plan.answered`, `plan.drafted`, `plan.changes_requested`, `plan.revised`, `plan.accepted` and `plan.vocabulary_uploaded`, each with the review as target and the project as `scope_id`. Ids only: never the question, an answer, a term or a change request. A plan drafted by a run's stage is recorded with no actor.

---

## Masking rules

A project's masking rules are terms always masked in every text of the project, as the entity type chosen: a staff list, a clinic's name ([`architecture.md` §3.9.1](./architecture.md#391-masking-is-derived-reviewable-and-re-runnable)). They apply to questions, answers and change requests before planning calls a model, to every paper in the `mask` stage, and to the prompt guard when a use case passes them with `GuardedInference.with_rules`. Matching ignores case and needs whole words; `Kofi` does not match `Kofiville`.

**Who:** owners only, for reading too, because a term is usually a person's name and counts as raw text. Reviewers and viewers get `403`; a non-member gets the same `404` as a missing project. Terms are sealed under the project's key (`masking_rules.term`, bound to the rule's id), so deleting the project makes them unreadable.

### `GET /api/v1/projects/{project_id}/masking-rules`

**Response** `200` with `[MaskingRuleOut]`, oldest first: `{"id", "term", "entity_type", "created_by", "created_at"}`. Every listing is audited, because it shows raw text.

### `POST /api/v1/projects/{project_id}/masking-rules`

**Request** `{"term": "Kofi Mensah", "entity_type": "PERSON"}`. `entity_type` defaults to `PERSON`; it is any of `PERSON`, `LOCATION`, `DATE`, `EMAIL`, `PHONE`, `MEDICAL_RECORD`, `ID_NUMBER`, `PASSPORT`, `IBAN`, `CARD_NUMBER`, `IP_ADDRESS`, `COORDINATES`. The term is trimmed and inner spaces collapsed. **Response** `201` with `MaskingRuleOut` and a `Location` header. `409` if the project already masks the term (ignoring case); `422` for a term under 2 or over 200 characters, with control characters, or with no letter or digit, and once a project has 500 rules.

### `DELETE /api/v1/projects/{project_id}/masking-rules/{rule_id}`

**Response** `204`. `404` for a rule that does not exist or belongs to another project. Texts already masked keep their placeholders until the next `mask` stage, which masks every paper again because its rules changed.

### The `mask` stage

The `mask` stage masks each paper's kept text (title and abstract in P1) with the narrow entity list: emails, phone numbers, medical record, ID and passport numbers, IBANs, card numbers, IP addresses and coordinates, plus the project's rules. Names, places and dates in published papers are evidence and stay. The masked text and its spans are stored sealed under the project's key (`paper_masks.masked_text` and `paper_masks.spans`, bound to the paper's id); the raw text is never changed. A paper masked with the same detectors and rules is skipped on a re-run; a new rule or detector version masks it again. If the project has uploaded documents waiting for review (P2), the stage first asks for a `privacy_review` gate whose payload lists each document's id and masked counts by type; rejecting fails the run. Its summary holds `papers`, `masked`, `skipped` and `masked_items` (counts by type, never values), and its `manifest` holds `detectors` (such as `patterns@1`), `entity_types` and `masking_rules` (how many).

### Audit

`masking_rule.created` and `masking_rule.deleted` with the rule as target, and `masking_rule.listed` with the project as target; the project is the `scope_id`. Ids only: never a term.

---

## Decision briefs

A run's `answer` stage writes one decision brief ([`architecture.md` §4.2](./architecture.md#42-reviews)); these routes read it (UI_UX_SPEC §6.9 to §6.11). Read-only: there is no route that writes or edits a brief.

**Who:** any member of the brief's project, viewers included. The paths name a review or a brief; a non-member gets the same `404` as a missing id. Everything returned was stored sealed under the project's key (`decision_briefs.content`, `.evidence` and `.trace`, each bound to the brief's id), so deleting the project makes it unreadable.

**Claims.** Every statement is a claim `{"text", "support", "citations"}`. `support` is `evidence` (the cited passages say this; `citations` is never empty) or `hypothesis` (the model's reasoning; `citations` is always empty). A citation is `{"tag", "source_kind"}`: `P12` for a paper (`paper`), `D3` for a team document (`document`). Nothing names a winning option, a rank or a total score.

**The steps behind a claim.** Every claim of a brief comes from the Writer step of round `kept_round`. Each cited passage records `found_by` (the search) and `round` (the Retriever step that found it). The trace lists those steps.

### `GET /api/v1/reviews/{review_id}/brief?run_id=`

The review's latest brief, or the one run `run_id` wrote. **Response** `200` with `BriefOut`:

```json
{
  "id": "…", "review_id": "…", "run_id": "…", "version": 2, "created_at": "…",
  "question": "Should we switch to cash in the camps near [LOCATION_1]?",
  "caveat": "This brief is an aid to a decision, not the decision. …",
  "bottom_line": [Claim, Claim],
  "options": [{"name", "description", "strength": "strong|moderate|weak|insufficient", "benefits": [Claim], "risks": [Claim]}],
  "trade_offs": [{"criterion", "is_humanitarian": true, "assessments": [{"option", "claim": Claim}]}],
  "conflicts": [{"question", "conflict_type": "hierarchy|context|noise", "positions": [Claim], "meaning": Claim | null}],
  "considerations": [{"criterion", "option", "because": Claim}],
  "gaps": ["…"],
  "sources": [{"tag", "source_kind", "paper_id", "has_passage", "title", "year", "doi", "url", "screening_label", "citations"}],
  "quality": {"coverage", "groundedness", "disagreements", "uncertainty", "humanitarian_awareness", "specificity"} | null,
  "warnings": [{"code", "message", "where"}],
  "model_id": "qwen3.5:9b", "rounds": 2, "kept_round": 2, "max_rounds": 3,
  "stopped_because": "accepted|max_rounds|invalid_output|no_progress"
}
```

`options` keep the Writer's order (2 to 4). `assessments` hold one cell per option. `sources` lists every cited tag by first appearance, with how often it is cited; `has_passage` is false only for a citation the checks flagged. `quality` holds the Critic's scores (0 to 2 each), never summed. `warnings` are checks still failing on the kept draft, such as `citation_not_retrieved`, `no_humanitarian_criterion` or `option_without_evidence`. `404` for no such review, a review with no brief yet, or a `run_id` whose brief belongs to another review.

### `GET /api/v1/briefs/{brief_id}/evidence/{tag}`

The source panel. **Response** `200` with `EvidenceOut`: `{"tag", "source_kind", "paper_id", "source": {"title", "year", "doi", "url", "screening_label", "rationale"} | null, "passages": [{"text", "locator", "found_by", "round"}], "cited_for": [{"path", "section", "option", "criterion", "text", "support"}]}`. `passages` are the exact masked texts the Writer saw. `source` is what the pipeline knew of the paper when the brief was written, with the screening rationale ("why it was included"); `null` when nothing was known. `cited_for` lists every claim citing the tag; `section` is `bottom_line`, `benefit`, `risk`, `trade_off`, `conflict` or `consideration`. `404` for no such brief, or a tag with no passage in the brief's evidence (the UI's "source missing").

### `GET /api/v1/briefs/{brief_id}/trace`

How the brief was made. **Response** `200` with `TraceOut`: `{"brief_id", "model_id", "rounds", "kept_round", "max_rounds", "stopped_because", "steps": [AgentStep], "totals": {"calls", "prompt_tokens", "completion_tokens"}, "manifest": {"model_ids", "prompt_versions", "retriever", "max_rounds"}}`. Each step is exactly the `agent.step` event the run streamed: `round` (0 for the Planner and the first retrieval), `role` (`planner`, `retriever`, `writer`, `checks`, `critic`), `status` (`ok`, `refused`, `invalid`, `error`), `seconds`, and as the role has them `tokens {"prompt", "completion"}`, `model_id`, `prompt_version`, `output`, `error`, `queries`, `passages_found`, `passages_new`, `passages_total` and, for checks, `problems`. Absent fields are omitted, not null.

### The `answer` stage

Reads the run's included passages (masked abstracts, tagged `P<n>`) from the pipeline, masks the review's question with the project's rules, then runs the loop: Planner (round 0), Retriever (BM25, [ADR-0007](./adr/0007-bm25-retrieval-for-the-brief.md)), then per round Writer, checks and Critic, retrieving again what the Critic asked for. It stops when the Critic accepts a draft the checks pass (`accepted`), at `HUMANI_T_BRIEF_MAX_ROUNDS`, default 3 (`max_rounds`), when a role answers invalidly twice in a row (`invalid_output`), or when a round did no better than the kept draft and the Critic's searches found no new passage (`no_progress`: "no new evidence and no better score"). Every model call goes through `GuardedInference.with_rules`. A run with no included passages fails with a plain message; so does a refused prompt or an unreachable model server. Run again after a crash, the stage returns the brief already stored. Its summary holds `brief_id`, `version`, `options`, `passages`, `rounds`, `kept_round`, `stopped_because` and `warnings` (a count), and its `manifest` holds `model_ids` and `prompt_versions` per role, `retriever` and `max_rounds`.

### Audit

`brief.created` by the system, with the brief as target and the project as `scope_id`; its sealed details hold the run id and version. Reading a brief is not audited, like reading a review.

---

## Exports

Downloads of a review's results (UI_UX_SPEC §6.8, §6.9, §15 P2). Each file is generated on request from what is stored, through the same use cases and response models the screens read, so it holds exactly what the screen shows, masked where the screen is masked. Nothing is stored twice, so deleting the project leaves no export behind.

**Who:** any member of the review's project, viewers included (`project.read`). A non-member gets the same `404` as a missing review, before the export's name is looked at.

### `GET /api/v1/reviews/{review_id}/exports/{name}?run_id=`

`name` is one of:

| Name | Media type | Holds |
| --- | --- | --- |
| `dataset.csv` | `text/csv; charset=utf-8` | One row per paper of the run, in the screening table's order: `tag`, `position`, `title`, `year`, `doi`, `url`, `source_id`, `found_by`, `is_open_access`, `label`, `tier`, `priority`, `score`, `rationale`, `criteria_matched`, `exclusion_reasons`, `is_unreadable`, `model_id`, `prompt_version`, `criteria_version`, `evidence_kind`, `decided_at`. A cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, so a spreadsheet shows it as text instead of running it. |
| `dataset.jsonl` | `application/x-ndjson` | One `PaperOut` per line, exactly as `GET …/papers` returns it. |
| `included.ris` | `application/x-research-info-systems` | The included papers, for a reference manager: `TY`, `ID` (the tag), `TI`, `PY`, `DO`, `UR`, `ER`. |
| `prisma.md` | `text/markdown; charset=utf-8` | The PRISMA counts of `GET …/prisma` as a Markdown table. |
| `manifest.json` | `application/json` | `GET /runs/{run_id}/manifest`, as a file. |
| `brief.json` | `application/json` | `GET …/brief`, as a file. |
| `brief.md` | `text/markdown; charset=utf-8` | The brief in the canvas's order: header with the caveat, bottom line, options, trade-offs, disagreements, considerations, gaps, sources. Reasoning claims are marked "reasoning, not a source". |
| `brief.html` | `text/html; charset=utf-8` | The same, print-ready: the browser's "Save as PDF" makes the PDF. The caveat sits in a fixed footer that repeats on every printed page. No script; every text is escaped. |

Without `run_id`, each export uses the run its screen shows by default: the newest run with papers for the dataset, RIS, PRISMA and manifest, the newest brief for the brief. **Response** `200` with the file, `Content-Disposition: attachment; filename="humani-t-run-<first 8 hex of the run id>-<name>"`, `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. `404` for an unknown name, a run of another review, a review with no papers yet, or no brief yet.

### Audit

`export.downloaded` by the member, with the review as target and the project as `scope_id`; its sealed details hold the export's name and the run id.

## Follow-up questions

Questions about a review's evidence, in threads (UI_UX_SPEC §6.12, §5.3.9). Asking returns at once; the worker answers in the background, because an answer takes one to four model calls, which is minutes on a local model. The browser polls the question until it is `answered` or `failed`.

**Who:** reviewers and owners ask (`question.ask`); any member reads. The paths name a review, thread or question; a non-member gets the same `404` as a missing id.

**Privacy.** The question is masked with the question sanitiser and the project's masking rules before it is stored; only the masked text and the counts by type are kept, sealed under the project's key (`questions.question`). The raw question is never stored or sent. Every model call goes through `GuardedInference.with_rules`.

**Answers.** From the review's evidence only: the included passages of the run its latest brief came from (or its newest run with any), plus the passages that brief cited, searched with BM25. Each claim is evidence (cites retrieved passages, checked by code) or reasoning (cites nothing), as in the brief; a citation of a passage that was never retrieved is sent back to the Writer. When nothing is retrieved, the answer has no claims and says "Nothing in this project answers that." without asking the model.

**Threads.** A follow-up names its `thread_id`. It carries the thread's last 3 questions and answers (masked) and also searches with the previous question. A thread answers one question at a time: asking while its last question is still `pending` or `answering` is `409`.

### `POST /api/v1/reviews/{review_id}/questions`

**Request** `{"question": "…", "thread_id": "…" | null}` (1 to 2000 characters; whitespace is collapsed). **Response** `202` with `QuestionOut` and a `Location: /api/v1/questions/{id}` header. `409` while the thread's last question is open, or when someone asked in the same thread at the same moment. `404` for a thread of another review. `422` for a blank question. `403` for a viewer.

`QuestionOut`: `{"id", "thread_id", "review_id", "position", "status": "pending"|"answering"|"answered"|"failed", "asked_by", "asked_at", "question_sent", "masked": {"LOCATION": 1}, "answered_at", "model_id", "error", "answer": AnswerOut | null}`. `question_sent` is the masked question, for the privacy receipt; `masked` counts what was masked, never the values. `error` is a plain sentence when `failed`.

`AnswerOut`: `{"claims": [Claim], "nothing_found", "message", "run_id", "sources": [{"tag", "source_kind", "paper_id", "passages": [{"text", "locator", "found_by", "round"}]}], "steps": [AgentStep], "rounds", "kept_round", "stopped_because", "warnings": [{"code", "message", "where"}]}`. Claims and agent steps have the brief's shapes. `sources` lists every cited tag with the masked passages the answer drew on. `stopped_because` is `null` when nothing was retrieved and no model was asked.

### `GET /api/v1/reviews/{review_id}/threads`

**Response** `200` with `[ThreadOut]`, newest first: `{"id", "review_id", "created_at", "created_by", "questions": [QuestionOut]}`, the questions in the order asked.

### `GET /api/v1/threads/{thread_id}`

**Response** `200` with `ThreadOut`.

### `GET /api/v1/questions/{question_id}`

Poll this until `status` is `answered` or `failed`. **Response** `200` with `QuestionOut`.

### The answering worker

`app.worker` answers questions beside its runs. Each is claimed under a 10-minute lease, renewed after every agent step; a question whose worker died is claimed again once, then failed with "The answer was interrupted twice, so it was given up. Ask again." An unreachable model or a refused prompt fails the question in plain words. The answer, its evidence and its agent steps are sealed under the project's key (`questions.answer`).

### Audit

`question.asked` by the member, with the question as target and the project as `scope_id`; its sealed details hold the thread id and position.

## Documents

A team's own files, uploaded to a project, read in the background, masked with the broad list (names, places, dates, contacts and identifiers, plus the project's rules), and reviewed by an owner before anything uses them ([walkthrough 16](./walkthroughs/16-documents.md), [`architecture.md` §3.9.1](./architecture.md#391-masking-is-derived-reviewable-and-re-runnable)). An approved document becomes evidence for the brief and follow-up questions as passages tagged `D<number>`; a model never sees one that is not approved.

**Who:** any member lists documents and reads their masked text; only owners upload, delete, see raw text, correct masks, and approve or reject (UI_UX_SPEC §3.3). A non-member gets the same `404` as a missing project. A document of another project is `404`.

**Status** moves `uploaded` → `scanning` → `extracting` → `masking` → `awaiting_review` → `approved` or `rejected`; any processing step can end in `failed`, with a `failure` code and a plain `failure_message`: `malware_found`, `scan_failed`, `unreadable`, `encrypted`, `no_text`, `too_complex`, `refused_content`, `timed_out`, `interrupted`. A correction sends an `approved` document back to `awaiting_review`.

**`DocumentOut`:** `{"id", "tag": "D2", "name", "file_type": "pdf"|"docx"|"txt"|"md", "size_bytes", "status", "failure", "failure_message", "uploaded_by", "uploaded_at", "scanned_with": "clamd"|"none"|null, "extracted_with", "page_count" (PDF only), "character_count", "is_truncated", "masked_items": {"PERSON": 3, …}, "mask_version", "last_review": {"decision", "mask_version", "decided_by", "decided_at"} | null}`.

### `POST /api/v1/projects/{project_id}/documents`

**Request:** `multipart/form-data` with exactly one part, named `file`, carrying a filename. This is the API's only non-JSON body: it still needs the session's `X-CSRF-Token`, and a browser's `Sec-Fetch-Site` and `Origin` must be same-origin. The type is read from the file's bytes, never from its name or the part's `Content-Type`; the name only tells plain text from Markdown. The name is made safe to show (no path, no hidden or bidirectional characters, at most 200 characters). **Response** `202` with `DocumentOut` (`status: "uploaded"`) and a `Location` header; a worker does the rest. **Refusals**, all before anything is stored: `413` larger than `HUMANI_T_UPLOAD_MAX_BYTES` (20 MiB by default, checked on the declared length before reading); `415` not multipart, or not PDF, DOCX, UTF-8 text or Markdown (a macro-enabled document named `.docx` included); `422` an empty file, or anything but one `file` part; `409` the project's quota (`HUMANI_T_DOCUMENTS_PER_PROJECT`, `HUMANI_T_DOCUMENT_BYTES_PER_PROJECT`) is full; `429` with `Retry-After` past `HUMANI_T_UPLOADS_PER_HOUR` for this person; `503` uploads switched off by the operator (production with no malware scanner chosen, or a database with no document directory), with the reason in `detail`.

### `GET /api/v1/projects/{project_id}/documents`

**Response** `200`: `{"documents": [DocumentOut], "usage": {"documents", "size_bytes", "max_documents", "max_size_bytes", "max_file_bytes"}, "accepted_types": ["pdf", "docx", "txt", "md"], "uploads_enabled", "uploads_disabled_reason"}`, by tag.

### `GET /api/v1/projects/{project_id}/documents/{document_id}`

**Response** `200` with `DocumentOut`.

### `GET /api/v1/projects/{project_id}/documents/{document_id}/masked`

What the model will see. **Response** `200`: `{"document_id", "tag", "status", "mask_version", "masked_text", "items": [{"start", "end", "placeholder": "[PERSON_1]", "entity_type", "sources": ["spacy-en_core_web_sm@3.8.0", "rule:<id>", "user:<id>"]}], "page_starts", "is_truncated"}`. Offsets are into `masked_text`. No raw text. `404` until the document has been masked.

### `GET /api/v1/projects/{project_id}/documents/{document_id}/review`

The owner's masking review. **Response** `200`: `{"document_id", "tag", "status", "mask_version", "raw_text", "masked_text", "spans": [{"start", "end", "entity_type", "source", "can_remove"}], "removed": [{"start", "end", "entity_type", "source", "removed_by", "reason"}], "items", "page_starts", "is_truncated", "notice"}`. `spans` and `page_starts` are offsets into `raw_text`. `can_remove` is false for project rules and for detected structured identifiers. Every request writes `document.raw_text_viewed`.

### `POST /api/v1/projects/{project_id}/documents/{document_id}/masks`

Mask text the detectors missed. **Request** `{"version": 1, "start": 120, "end": 132, "entity_type": "PERSON"}`: the `mask_version` you were looking at, and a range of `raw_text` (at most 500 characters, containing text). The project's current rules are applied again too. **Response** `200` with the review body for the new version (`mask_version` one higher, status `awaiting_review`). `409` if the masking changed since `version`, or the document is not `awaiting_review` or `approved`; `422` for a range outside the text.

### `POST /api/v1/projects/{project_id}/documents/{document_id}/unmask`

Mark a mask "not personal". **Request** `{"version": 1, "start": 40, "end": 46, "reason": "A district's name, not a person"}`: `start` and `end` exactly those of a mask in `spans`; the reason (one line, at most 200 characters) is kept, sealed. **Response** `200` with the review body for the new version. `422` for a project rule's mask (remove the rule instead), a detected structured identifier (email, phone, MRN, ID, passport, IBAN, card, IP, coordinates), a range no mask covers exactly, or an unusable reason; `409` as above.

### `POST /api/v1/projects/{project_id}/documents/{document_id}/approve` and `…/reject`

**Request** `{"version": 1}`, the `mask_version` reviewed. **Response** `200` with `DocumentOut`; `last_review` records who decided which version and when (also kept, append-only, after the document is deleted). `409` if the document is not `awaiting_review` or the version is not current. Deciding the last document waiting for review approves any open `privacy_review` gate of the project's runs in the decider's name, so the run resumes (UI_UX_SPEC §6.6).

### `DELETE /api/v1/projects/{project_id}/documents/{document_id}`

**Response** `204`. Deletes the document's raw and masked text, then its file. Its tag is never given to another document. A brief written earlier keeps the masked passages it cited.

### Processing and evidence

The worker (`python -m app.worker`, or the in-process worker in development) claims uploaded documents under a lease. It scans each with the operator's scanner (`HUMANI_T_MALWARE_SCANNER`: `clamd`, or `none`, recorded as `scanned_with`); an infected file is deleted unread. A scanner that cannot be reached leaves the document `uploaded` and it is tried again. Text is read in a separate process with a timeout and page, character and archive limits (`HUMANI_T_EXTRACTION_*`), then masked with patterns, dates, the name model when installed, and the project's rules. Approved documents are added to the evidence of every run of the project, after its included papers: passages of about 1,200 characters, tag `D<number>`, `source_kind: "document"`, locator `page 3` (PDF) or `characters 0-1200` (offsets into the masked text). The `privacy_review` gate's payload lists each waiting document's `document_id` and `masked_items`.

### Audit

`document.uploaded`, `document.raw_text_viewed`, `document.mask_added`, `document.mask_removed`, `document.approved`, `document.rejected` and `document.deleted` by the member; `document.ready_for_review` and `document.failed` by the system (actor `null`). The target is the document, the project the `scope_id`. Sealed details hold only a `mask_version`, a failure code, or the scanner and signature for malware: never a file name, text or reason.

---

## Semantics

### How a query is built

```text
each Concept        ->  (term OR synonym OR synonym)
concepts in a theme ->  OR'd together
themes              ->  AND'd together
```

So a vocabulary with concepts in two themes requires a match in **both**. With the example above, a paper about chatbots in education does not match, and neither does one about humanitarian logistics.

### Strict mode

`origin: "user_supplied"` means the terms reach the providers exactly as given. This is a correctness property with a test behind it (`test_strict_vocabulary_gains_no_terms`), not a convention — when LLM expansion is added, that test is what stops it reaching user-supplied terms.

---

## Authentication

Sessions are server-side. Signing in sets two cookies:

| Cookie | Holds | Readable by scripts | SameSite |
| --- | --- | --- | --- |
| `__Host-session` | the session token | no (`HttpOnly`) | `Lax` |
| `__Host-csrf` | the session's CSRF token | yes | `Strict` |

Every state-changing request made while signed in must send the CSRF token back in an `X-CSRF-Token` header, or it is refused with `403`. Requests whose `Origin` or `Sec-Fetch-Site` show they come from another site are refused before that, as are request bodies that are not `application/json` (`415`).

Sessions end after 1 hour idle or 24 hours in total. Changing the password needs a sign-in within the last 15 minutes; otherwise the response is `401` with `"reauthentication_required": true`, and `POST /auth/reauthenticate` refreshes it.

`POST /auth/reauthenticate` `{"password"}` answers `204` with **new** `__Host-session` and `__Host-csrf` cookies: the token from before stops working at once. The session keeps its assurance level and its 24-hour limit, counted from the original sign-in; a wrong password is `403` and changes nothing.

Registration is by invitation only. The first administrator is created inside the deployment: `python -m app.cli create-admin --email … --password-stdin`.

### Account administration

Instance administrators only (`403` otherwise). Each change is audited with ids only: `account.disabled` and `account.enabled` (target: the user), with the acting admin and the request id.

- `POST /admin/users/{id}/disable` → `204`. The account can no longer sign in, and every session it has ends. `409` for your own account, and `409` with `"last_admin": true` when it is the last enabled administrator. Admins disabling one another at the same moment are serialised, so one enabled administrator always remains.
- `POST /admin/users/{id}/enable` → `204`. The account can sign in again; the sessions that disabling ended stay ended. Needs a sign-in within the last 15 minutes (`401` with `reauthentication_required`). Enabling an account that is not disabled changes nothing. `404` for an unknown id.

Per-client sign-in limits count the connecting address. `X-Forwarded-For` names the client only when the request comes from one of the operator's `HUMANI_T_TRUSTED_PROXIES` (none by default; Caddy in the compose deployment), and from anyone else it is ignored.

### Multi-factor authentication

MFA is required. Once an account has a second factor, sign-in has two steps, and every account must add a factor before it can do anything beyond its own account basics.

**Step 1 — `POST /auth/login`** answers `200` with a `status` (fields that do not apply are left out):

| `status` | Body | Session | What the browser may reach |
| --- | --- | --- | --- |
| `mfa_required` | `methods` from `["passkey", "totp", "recovery_code"]`, **no** `user` | pending: aal 1, ends 5 minutes after sign-in | `/auth/mfa/*` and `POST /auth/logout` only; anything else is `401` with `"mfa_required": true` |
| `mfa_enrolment_required` | `user` | aal 1, normal lifetime | its account basics (`/auth/session`, `/auth/reauthenticate`, `/auth/logout`, `/account/sessions*`, `/account/password`, `/account/mfa*`); anything else is `403` with `"mfa_enrolment_required": true` |
| `signed_in` | `user` | full | everything its roles allow (only when MFA is not required and the account has no factor) |

`POST /auth/register` answers the same way (`mfa_enrolment_required`). A wrong password is the same `401` whether or not the account has MFA.

```json
{ "status": "mfa_required", "methods": ["passkey", "totp", "recovery_code"] }
```

**Step 2 — complete the pending session** with one factor. Success is `200` `{"status": "signed_in", "user": {…}}` with **new** cookies: the pending session ends and an aal 2 session takes its place.

| Request | Body |
| --- | --- |
| `POST /auth/mfa/totp` | `{"code": "123456"}`: exactly six ASCII digits, else `422` |
| `POST /auth/mfa/recovery-code` | `{"code": "ABCD-EFGH-JKMN-PQ01"}`: case, spaces and dashes are ignored |
| `POST /auth/mfa/passkey/options` | `{}` → `{"options": PublicKeyCredentialRequestOptionsJSON}` |
| `POST /auth/mfa/passkey` | `{"credential": AuthenticationResponseJSON}` |

- A wrong, reused or out-of-window code, an unrecognised passkey, or an ended pending session is `401`. The pending session may try again until it ends.
- A TOTP code is accepted within one 30-second step either side of now, and each step only once: an older code is refused once a newer one has been used.
- Codes and recovery codes share one limit per account: 10 attempts per 15 minutes, then `429` with `Retry-After`, even for the right code.
- A passkey challenge answers one attempt, failed or not, and lasts 5 minutes. User verification (PIN or biometric) is required. A signature counter that does not advance is refused, because that is what a cloned authenticator looks like.
- Using a recovery code spends it and emails the account, saying how many remain.

**Managing factors** (`/account/mfa*`, reachable before enrolment):

- `GET /account/mfa` → `{"is_enrolled", "totp": {"id", "created_at", "confirmed_at"} | null, "passkeys": [{"id", "name", "created_at", "last_used_at"}], "recovery_codes_remaining"}`. No secret, public key or credential id is ever returned.
- `POST /account/mfa/totp` → `201` `{"secret", "otpauth_uri"}`. The secret is shown here once and is inactive until confirmed. `409` if the account already has an authenticator app. Starting again replaces an unconfirmed secret.
- `POST /account/mfa/totp/confirm` `{"code"}` → `200` `{"recovery_codes": [10 codes] | null}`; `400` for a wrong code.
- `POST /account/mfa/passkeys/options` → `{"options": PublicKeyCredentialCreationOptionsJSON}`. The relying party id is the host of the deployment's public origin. Then `POST /account/mfa/passkeys` `{"name", "credential": RegistrationResponseJSON}` → `201` `{"passkey", "recovery_codes"}`; `400` if verification fails or the challenge was used, expired or issued to another session; `409` if that passkey is already registered.
- The **first** factor an account adds returns 10 recovery codes (shown once) and moves this browser to aal 2 under new cookies; every other session of the account ends.
- `DELETE /account/mfa/totp` and `DELETE /account/mfa/passkeys/{id}` → `204`; `409` for the last factor; `404` for another account's passkey.
- `POST /account/mfa/recovery-codes` → `201` `{"recovery_codes"}`; every older code stops working. `409` without a factor.
- Adding, removing and regenerating need a sign-in within 15 minutes (`401` with `reauthentication_required`), and each emails the account.

| Status | Meaning |
| --- | --- |
| `400` | a wrong code while enrolling an authenticator app, or a passkey registration that failed verification |
| `401` | not signed in, wrong credentials, a rejected second factor, or the session has ended; `"mfa_required": true` for a pending session outside `/auth/mfa/*` |
| `403` | missing CSRF token, cross-site request, wrong current password, not an admin, a project role that does not allow the action, or `"mfa_enrolment_required": true` |
| `422` | invalid body, a password that breaks the policy (`violations` lists why), or a new member's email with no active account |
| `404` | no such object, or a project the caller is not a member of (the same answer for both) |
| `429` | too many attempts; `Retry-After` says when to try again |
| `409` | the record changed while you were editing it; reload and retry. Also: already a member, `last_owner`, the last second factor, an authenticator that is already enrolled, a run already going for the review, a gate already decided, a run that has finished or stopped waiting, disabling your own account, or `last_admin` |
| `413` | the request body is over 1 MiB |
| `502` | an email (an invitation) could not be sent, and the action itself was saved; or a paper source (with its `source_id`) or the model server could not be reached. The message never names a host, an address or the upstream's own error |
| `503` | the service is saturated, the database is unreachable, or the rate limiter is (sign-in fails closed); `Retry-After` says when to retry |

---

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem document, served as `application/problem+json`:

```json
{ "type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found" }
```

Validation errors (`422`) add an `errors` list of `{loc, msg, type}`. They never echo the submitted value back, because it may be the sensitive text the user was sending. Unexpected errors (`500`) say nothing about the cause; quote the response's `x-request-id` header when reporting one. The server's `unhandled_error` log line carries the same `request_id`.

Every response carries security headers (CSP, HSTS, `nosniff`, `Cache-Control: no-store`, …) and an `x-request-id`, a `500` included.

---

## Not here yet

Deliberately absent, in build order:

- **Real sources** — arXiv, OpenAlex, Europe PMC, Google Scholar
- **Real pipeline stages** — screening, masking behind the privacy gate (the run machinery, its gates and its fake stages are in place)

See [`dependencies.md`](./dependencies.md) for the sequence.
