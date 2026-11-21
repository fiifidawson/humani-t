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
| `GET` | `/api/v1/projects` | projects | The caller's projects, filtered and sorted, with what grouping needs |
| `GET` | `/api/v1/projects/{project_id}` | projects | One project (any member) |
| `PATCH` | `/api/v1/projects/{project_id}` | projects | Rename (owner) |
| `PUT` | `/api/v1/projects/{project_id}/masking` | projects | Switch masking on or off (owner; recent sign-in to turn it off) |
| `PUT` | `/api/v1/projects/{project_id}/view-first` | projects | Switch "Your view first" on or off (owner) |
| `POST` | `/api/v1/projects/{project_id}/archive` | projects | Hide from the default list (owner) |
| `POST` | `/api/v1/projects/{project_id}/unarchive` | projects | Bring back (owner) |
| `DELETE` | `/api/v1/projects/{project_id}` | projects | Delete by destroying the project's key (owner, recent sign-in) |
| `GET` | `/api/v1/projects/{project_id}/members` | projects | Who belongs to the project (any member) |
| `POST` | `/api/v1/projects/{project_id}/members` | projects | Give an existing account a role (owner, recent sign-in) |
| `PATCH` | `/api/v1/projects/{project_id}/members/{user_id}` | projects | Change a member's role (owner, recent sign-in) |
| `DELETE` | `/api/v1/projects/{project_id}/members/{user_id}` | projects | Remove a member (owner, recent sign-in), or leave (anyone) |
| `GET` | `/api/v1/collections` | collections | Every collection, counting the caller's own projects only |
| `POST` | `/api/v1/collections` | collections | Create a collection (any signed-in user) |
| `PATCH` | `/api/v1/collections/{collection_id}` | collections | Rename it (its creator or an administrator) |
| `DELETE` | `/api/v1/collections/{collection_id}` | collections | Delete it; its projects stay, in no collection (its creator or an administrator) |
| `PUT` | `/api/v1/projects/{project_id}/collection` | collections | File the project in a collection, or in none (owner) |
| `PUT` | `/api/v1/projects/{project_id}/tags` | collections | Replace the project's tags (owner) |
| `PUT` | `/api/v1/projects/{project_id}/pin` | collections | Pin it for yourself (any member) |
| `DELETE` | `/api/v1/projects/{project_id}/pin` | collections | Unpin it (any member) |
| `POST` | `/api/v1/project-actions` | collections | Move, tag or archive several projects; reports what it skipped |
| `GET` | `/api/v1/projects/{project_id}/model-instructions` | model instructions | The project's instructions to the model and every version (any member) |
| `PUT` | `/api/v1/projects/{project_id}/model-instructions` | model instructions | Save them as the next version (owner) |
| `GET` | `/api/v1/search?q=&scope=` | search | Search the caller's projects; results grouped by project |
| `POST` | `/api/v1/projects/{project_id}/reviews` | reviews | Ask a question in the project (owner, reviewer) |
| `GET` | `/api/v1/projects/{project_id}/reviews` | reviews | The project's reviews, newest first (any member) |
| `GET` | `/api/v1/projects/{project_id}/reviews/{review_id}` | reviews | One review (any member) |
| `POST` | `/api/v1/reviews/{review_id}/runs` | runs | Start a run of some stages; `202` at once (owner, reviewer) |
| `GET` | `/api/v1/reviews/{review_id}/runs` | runs | The review's runs, newest first (any member) |
| `GET` | `/api/v1/runs/{run_id}/events` | runs | Follow a run as Server-Sent Events (any member) |
| `POST` | `/api/v1/runs/{run_id}/cancel` | runs | Cancel a run; finished stages are kept (owner, reviewer) |
| `POST` | `/api/v1/gates/{gate_id}` | runs | Decide a gate so the run continues (owner; reviewer for a vocabulary) |
| `GET` | `/api/v1/runs/{run_id}/leaning` | briefs | Your own answer to "Your view first" for this run's brief, or `null` (any member) |
| `PUT` | `/api/v1/runs/{run_id}/leaning` | briefs | Say which option you lean towards before reading, once (any member) |
| `GET` | `/api/v1/needs-you` | history | What waits for the caller across their projects |
| `POST` | `/api/v1/needs-you/dismissals` | history | Hide one Needs you item for the caller until it changes |
| `GET` | `/api/v1/activity` | history | Recent activity in the caller's projects |
| `GET` | `/api/v1/projects/{project_id}/history` | history | The project's timeline, grouped by review (any member) |
| `GET` | `/api/v1/projects/{project_id}/paper-marks` | marks | The caller's seen and saved marks on the project's papers (any member) |
| `PUT` | `/api/v1/projects/{project_id}/papers/{paper_id}/seen` | marks | Mark a paper opened (any member) |
| `PUT`, `DELETE` | `/api/v1/projects/{project_id}/papers/{paper_id}/saved` | marks | Save a paper, or take it off the saved list (any member) |
| `GET` | `/api/v1/saved-papers` | marks | The caller's saved papers |
| `PUT` | `/api/v1/briefs/{brief_id}/seen` | marks | Mark a brief opened (any member) |

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
  "paper_sources": ["openalex", "europepmc"],
  "makes_network_calls": true
}
```

`paper_sources` are the sources a run searches; `demo` makes no requests.

The whole design rests on implementations being swappable. This endpoint makes the current choice visible instead of buried in a config file — and `makes_network_calls` is the fastest way to confirm a deployment is not talking to anyone it should not.

---

## Projects

A project is the unit of access, encryption and deletion. Everything below `/api/v1/projects/{project_id}` answers in this order:

1. `401` if not signed in; `403` if a write lacks the CSRF token;
2. `404` if the project does not exist **or** the caller is not a member, with the same body either way: a project's existence is never confirmed to someone outside it, not even to an instance administrator;
3. `403` if the caller's role does not allow the action;
4. `401` with `"reauthentication_required": true` if the action needs a sign-in within the last 15 minutes (delete, adding a member, changing a role, removing someone else, turning masking off);
5. then the request itself: `422` for bad input, `409` for a conflict.

Steps 1 to 4 happen before the request body is read, so a non-member learns nothing, not even whether their body was valid.

### Roles

| Action | Owner | Reviewer | Viewer |
| --- | --- | --- | --- |
| Read the project and its members | yes | yes | yes |
| Search, start or cancel runs, confirm gates, edit labels, ask questions | yes | yes | no |
| Upload documents, see raw text | yes | no | no |
| Rename, archive, switch masking, manage members, delete | yes | no | no |
| Leave | yes, unless the last owner | yes | yes |

The table is the domain function `can(role, action)` in `app/domain/projects.py`; anything it does not list is refused. Being an instance administrator grants nothing inside a project.

### `ProjectOut`

```json
{
  "id": "6f1c…",
  "name": "Cholera response, Sana'a",
  "role": "owner",
  "created_at": "2026-11-03T09:00:00Z",
  "archived_at": null,
  "status": "brief_ready",
  "is_masking_on": true,
  "is_masking_effective": true,
  "can_turn_masking_off": false,
  "is_view_first_on": false,
  "collection_id": null,
  "tags": ["Mali", "cash transfers"],
  "is_pinned": false
}
```

`role` is the caller's own role. `collection_id`, `tags` and `is_pinned` are described under [Collections, tags and pins](#collections-tags-and-pins); `is_pinned` is the caller's own pin. The name is stored encrypted under the project's own key; only members ever see it in the clear.

`status` is derived, never set (UI_UX_SPEC §11.1), by `project_status` in `app/domain/projects.py`. It comes from the newest review and that review's latest run:

| `status` | When |
| --- | --- |
| `planning` | No review yet, or the newest review is `new`, `clarifying`, `drafting`, `changes_requested` or `accepted` |
| `running` | A run is going |
| `needs_you` | The run waits at a gate |
| `brief_ready` | The latest run finished and wrote a brief |
| `done_without_brief` | The latest run finished without the brief stage |
| `failed` | The latest run failed |
| `cancelled` | The latest run was cancelled |
| `archived` | An owner archived the project, whatever its review |

The masking fields come from the API, so the web app shows them without deciding anything ([ADR-0014](adr/0014-masking-is-a-project-choice.md)):

| Field | Meaning |
| --- | --- |
| `is_masking_on` | The owner's switch, as stored. A new project starts on when the model is hosted, off when the deployment runs it itself. |
| `is_masking_effective` | Whether text is masked before it reaches the model now: the switch, unless the model is hosted and the operator has not set `allow_unmasked_hosted_inference`, when it is `true` whatever the switch says. |
| `can_turn_masking_off` | `false` while the deployment requires masking for every project. |

`is_view_first_on` says whether the brief asks each member which option they lean towards before showing it (UI_UX_SPEC §6.9). Off for every new project (§16 Q3).

### `POST /api/v1/projects`

**Request** `{"name": "Cholera response, Sana'a"}`. The name is trimmed and must be 1 to 200 characters with no control or bidirectional-override characters (`422` otherwise). Unknown fields are refused (`422`), so the owner, id and dates cannot be set by the client.

**Response** `201` with a `ProjectOut` and `Location: /api/v1/projects/{id}`. The project gets its own data key in the same transaction, and the caller becomes its owner.

### `GET /api/v1/projects`

The caller's projects, each a `ProjectOut` with what the All projects page groups, filters and sorts by (UI_UX_SPEC §7.2). Only projects the caller is a member of are ever read, so no filter can bring in anyone else's.

```json
{
  "…": "every ProjectOut field",
  "last_activity_at": "2026-11-18T10:58:00Z",
  "pinned_at": null,
  "members": [{ "user_id": "2b9e…", "display_name": "Kofi Mensah", "role": "reviewer" }],
  "relevant_papers": 52,
  "papers_found": 340,
  "brief_version": 2
}
```

`last_activity_at` is the latest of: created, archived, the newest review's creation, its newest run's creation, start or finish, its latest brief, and its latest follow-up question. `relevant_papers` counts included papers of the newest review's newest run with papers, under the review's latest label rules ([ADR-0013](adr/0013-label-rules-applied-on-read.md)); `papers_found` is that run's papers after duplicates. Both are `null` before there are papers; `brief_version` is `null` before a brief.

Query parameters, all optional, all of which must hold:

| Parameter | Meaning |
| --- | --- |
| `sort` | `last_activity` (default, newest first), `name` (A to Z, ignoring case), `created` (newest first), `relevant_papers` (most first, projects with none last). Ties go to the most recent activity. |
| `status` | Repeatable; any of these `ProjectOut.status` values. `archived` brings archived projects in. |
| `tag` | Repeatable; every one of these tags, ignoring case. |
| `owner`, `member` | A user id: projects that person owns, or belongs to. |
| `created_from`, `created_to` | Dates, inclusive, in UTC. `422` if the range ends before it starts. |
| `collection` | A collection id, or `none` for projects in no collection. |
| `include_archived` | `true` to include archived projects. Left out otherwise, unless `status` asks for them. |

### `GET`, `PATCH /api/v1/projects/{project_id}`, `POST …/archive`, `POST …/unarchive`

- `GET` returns the `ProjectOut` to any member.
- `PATCH` with `{"name": "…"}` renames (owner), with the same rules as on create.
- `POST …/archive` hides the project from the default list and keeps everything; archiving an archived project keeps the first date. `POST …/unarchive` brings it back. Owner only. An archived project is not read-only.

### `PUT /api/v1/projects/{project_id}/masking`

Owner only. **Request** `{"is_masking_on": false}`. **Response** `200` with the `ProjectOut`. Turning masking off needs a sign-in in the last 15 minutes; turning it on never does. `409` with `"masking_required": true` and the detail "This deployment sends text to a hosted model, so masking stays on." when masking cannot be turned off; the request is valid, but the deployment's configuration refuses it, as with the last owner. Setting the value it already has changes nothing and records nothing. Turning masking off also approves the project's open `privacy_review` gates, as the caller, so a waiting run carries on; each shows as `gate.resolved` in its run's events.

With masking effectively off, typed text (the question, clarifying answers, change requests, follow-up questions) is sent without the name, place and pattern detectors; the `mask` stage keeps paper text as written; uploaded documents are used once scanned and read, with no privacy review and no `privacy_review` gate; and the prompt guard no longer refuses identifiers. The project's masking rules still apply to every text, and the guard still refuses a rule term. Receipts, follow-up questions, stage manifests and the brief's trace record which way each text was sent.

### `PUT /api/v1/projects/{project_id}/view-first`

Owner only, no recent sign-in. **Request** `{"is_view_first_on": true}`. **Response** `200` with the `ProjectOut`. Setting the value it already has changes nothing and records nothing. Answers are kept when it is turned off.

### `DELETE /api/v1/projects/{project_id}`

Owner only, with a sign-in in the last 15 minutes. Destroys the project's data key (crypto-shredding), removes every membership and keeps the project row as a tombstone. Answers `204`; from then on the project answers `404` to everyone, exactly as if it never existed. Every ciphertext of the project (its name, encrypted audit details, later its documents) becomes permanently unreadable, including copies in backups once the backup retention window has passed. The audit chain still verifies. Irreversible; asking the user to type the name first is the web app's job.

### Members

`MemberOut`:

```json
{ "user_id": "2b9e…", "display_name": "Kofi Mensah", "role": "reviewer", "added_at": "2026-11-03T09:05:00Z" }
```

Owners also get each member's `email`, so they can tell two people with the same display name apart before changing anyone's access. For reviewers and viewers the field is left out of the body entirely, not sent as `null`. Adding a member and changing a role need the owner role, so those responses always carry it.

- `GET …/members`: every member, earliest added first. Any member.
- `POST …/members` with `{"email": "kofi@example.org", "role": "reviewer"}`: owner, recent sign-in. `201` with a `MemberOut`. The email is matched case-insensitively against **existing, enabled** accounts; anything else gets `422` with the same body, which never repeats the email. `409` if the person is already a member. `role` is exactly `owner`, `reviewer` or `viewer`.
- `PATCH …/members/{user_id}` with `{"role": "viewer"}`: owner, recent sign-in. `200` with the `MemberOut`; `404` if that user is not a member.
- `DELETE …/members/{user_id}`: removing someone else is for owners, with a recent sign-in; removing yourself (leaving) is for anyone and needs no recent sign-in. `204`.

The last owner can neither be demoted nor removed, nor leave: `409` with `"last_owner": true`. Membership changes on one project are made one at a time ([ADR-0002](adr/0002-project-row-lock-for-membership-changes.md)), so two owners stepping down at once still leave one owner.

### Audit

Every project action is recorded with ids only: `project.created`, `project.renamed`, `project.archived`, `project.unarchived`, `project.masking_changed` (sealed details: `{"is_masking_on": …}`), `project.view_first_changed` (sealed details: `{"is_view_first_on": …}`), `project.deleted` (target: the project), and `membership.added`, `membership.role_changed`, `membership.removed` (target: the member's user id). Each carries the project's id as `scope_id`, the actor and the request id. Refused attempts are not recorded.

---

## Collections, tags and pins

How people group and find projects (UI_UX_SPEC §7.1 to §7.3; [ADR-0016](adr/0016-collections-are-shared-labels.md)). A **collection** is a label shared across the deployment, one level deep, with no access rules of its own: it grants nothing and reveals no project to anyone outside it. A project is in at most one. **Tags** are a project's own free labels, as many as 20, each up to 40 characters. A **pin** is one person's bookmark.

### `CollectionOut`

```json
{ "id": "9a0d…", "name": "Sahel 2026", "project_count": 4, "can_manage": true, "created_at": "2026-11-19T09:00:00Z" }
```

`project_count` counts the caller's own projects in it, archived ones aside; never anyone else's. `can_manage` says whether the caller may rename or delete it: its creator, or an instance administrator.

### `GET /api/v1/collections`

Every collection, by name, to any signed-in user. Collection names are visible to everyone in the deployment; a label meant only for a team belongs in a tag.

### `POST /api/v1/collections`

**Request** `{"name": "Sahel 2026"}`. Any signed-in user. The name is trimmed with its spaces collapsed, 1 to 80 characters, with no control or bidirectional-override characters (`422` otherwise). `409` with `"collection_name_taken": true` if another collection uses the name in any spelling ("sahel  2026" and "Sahel 2026" are one name). **Response** `201` with the `CollectionOut` and `Location`. The collection gets its own data key, and its name is stored only sealed under it.

### `PATCH`, `DELETE /api/v1/collections/{collection_id}`

Its creator or an administrator; anyone else gets `403`, a missing collection `404`. `PATCH` with `{"name": "…"}` renames it for everyone, with the same rules as on create; a collection may take its own name in another case. `DELETE` answers `204`: every project it held, whoever owns it, is then in no collection, and its key is destroyed so its name is unreadable in any copy.

### `PUT /api/v1/projects/{project_id}/collection`

Owner only. **Request** `{"collection_id": "9a0d…"}`, or `null` to take the project out. **Response** `200` with the `ProjectOut`. `422` if the collection does not exist; nothing moves. Moving it where it already is changes and records nothing.

### `PUT /api/v1/projects/{project_id}/tags`

Owner only. **Request** `{"tags": ["Mali", "cash transfers"]}` replaces the project's tags; `[]` removes them all. Each tag is trimmed with its spaces collapsed; a repeat, ignoring case, is dropped and the first spelling kept. `422` for a blank tag, one over 40 characters, more than 20, or control characters. **Response** `200` with the `ProjectOut`. Tags are sealed under the project's key and die with it.

### `PUT`, `DELETE /api/v1/projects/{project_id}/pin`

Any member, for themselves only. `204`. Pinning again keeps the first pin's date. A pin goes when the project is deleted or the person leaves it. Home shows up to 8 pinned projects, newest pin first.

### `POST /api/v1/project-actions`

Bulk actions from the All projects list (UI_UX_SPEC §7.2). One of:

```json
{ "action": "move", "project_ids": ["…"], "collection_id": "9a0d…" }
{ "action": "add_tag", "project_ids": ["…"], "tag": "Mali" }
{ "action": "archive", "project_ids": ["…"] }
```

`project_ids` holds 1 to 200 ids; `collection_id` may be `null`. **Response** `200`:

```json
{ "changed": ["…"], "skipped": [{ "project_id": "…", "reason": "not_allowed" }] }
```

A mixed selection is normal: each project the caller owns is changed, each in its own transaction, and every other is listed in `skipped`, in the order given. `not_found` covers a missing project **and** one the caller is not in, with nothing to tell them apart; `not_allowed` is a project where the caller's role may not do this; `too_many_tags` is one already holding 20 tags. Adding a tag keeps the tags already there. An unknown collection, a blank tag or an unknown action refuses the whole request with `422` before anything changes.

### Audit

`collection.created`, `collection.renamed`, `collection.deleted` (target and `scope_id`: the collection); `project.collection_changed` (sealed details: `{"collection_id": …}`) and `project.tags_changed` (sealed details: `{"count": …}`, never the tags), with the project as target and `scope_id`. Bulk actions record one event per project changed. Pins are not audited: a pin is a bookmark, not a decision about the project.

---

## Reviews and runs

A review is a question inside a project. A run executes some of the review's pipeline stages as a background job; the browser follows it on one Server-Sent Events stream and answers its gates with ordinary `POST`s ([architecture §4.4](architecture.md#44-runs-live-events-and-gates), [ADR-0003](adr/0003-run-queue-and-event-fan-out.md)).

The run, event and gate paths name a review, run or gate rather than the project. The API finds the project from that id and then answers exactly as on a project route: `401`, then `404` for a non-member with the same body as for an id that does not exist, then `403` for a role that may not, then the request itself. Routes with a body check access before reading it.

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

**Request** `{"stages": ["discover", "queries"]}`: any of `queries`, `discover`, `ingest`, `mask`, `screen`, `export`, `answer`, in any order; they run in that pipeline order. `{"stages": []}` or `{}` runs the whole pipeline, every stage in that order. Unknown or repeated stages are `422`. Owner or reviewer.

There is no `index` stage: it was a stand-in that did no work, because BM25 builds its index per search ([ADR-0007](adr/0007-bm25-retrieval-for-the-brief.md)). Runs stored with it still read back with it in `stages`, their events and their manifest; a new request naming it is `422`.

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
data: {"at":"2026-11-03T09:11:00+00:00","position":1,"stage":"discover","total":2}

id: 2
event: stage.progress
data: {"at":"2026-11-03T09:11:02+00:00","done":1,"item":"openalex · 25 found","stage":"discover","total":2}

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

**Response** `200` with `{"gate_id", "decision", "by"}`. The first decision wins: `gate.resolved` goes to every open stream and the run is queued again; the stage that asked receives the decision and decides what follows. A second decision, or one on a run that is no longer waiting (it was cancelled), gets `409`. On a `vocabulary` gate, `422` names the first text in `revisions` that holds a term the project always masks (`revisions.criteria[1].text holds a term…`), never the term itself; nothing is stored. Audited as `gate.resolved`.

### Audit

`review.created` (target: the review), `run.started` and `run.cancelled` (target: the run), `gate.resolved` (target: the gate), each with the project as `scope_id`, the actor and the request id. Ids only: never the question, a stage's output or a decision's revisions.

---

## Papers, screening and the manifest

Read-only routes for every member, viewers included (`project.read`). Each resolves the project from the review or run in its path: 401 when signed out, and the same 404 for a non-member as for a missing id. `?run_id=` names a run of this review; left out, the review's newest run that has papers is used. A run started without Discover (a retry from Screen, say) works from the newest earlier discovery, so its papers, tags and counts are that run's. Paper text is served masked only (`masked_text`); a paper's raw kept text has no field anywhere. Titles, years, DOIs and links are the paper's public bibliographic record.

### `PaperOut`

`{"paper_id", "tag", "position", "title", "year", "doi", "url", "source_id", "found_by", "is_open_access", "decision", "screening_status", "skipped_because", "is_cited_in_brief"}`. `url` is the record's own link, else `https://doi.org/<doi>` when it has a DOI (public metadata), else `null`. `screening_status` is `screened` (it has a decision), `skipped` (the screen stage had no masked abstract to send; `skipped_because` is `no_abstract` or `not_masked`), `failed` (the privacy check stopped its text, so nothing was sent; it is tried again next run) or `not_screened` (this run has not screened it yet). `tag` is `P` and the paper's place in the run's merged list (`P1`, `P12`…); it is how the brief cites the paper and does not change when labels do. `is_cited_in_brief` says whether the brief this run wrote cites that tag; `false` while the run has no brief. It is about this run's brief only, not a later run's. `source_id` is the source whose record was kept; `found_by` every source that returned it. `decision` is `null` until screened, else `DecisionOut`: `{"label": "include"|"exclude"|"uncertain", "priority": "high"|"medium"|"low", "tier", "score", "rationale", "criteria_matched", "exclusion_reasons", "is_unreadable", "model_id", "prompt_version", "criteria_version", "evidence_kind", "decided_at", "label_set_by", "rules_version"}`. `label_set_by` is `model`, or `rules` when the review's label rules set `label`, `priority`, `tier` and `exclusion_reasons` (then `rules_version` names the version; see Label rules below). `tier` is the name shown (UI_UX_SPEC §6.8): `highly_relevant`, `relevant`, `possibly_relevant` (an inclusion by priority), `excluded`, `uncertain`. `criteria_matched` names the criteria (`I1`, `E1`…) the paper meets; an exclusion's `exclusion_reasons` are the exclusion criteria it meets, then the inclusion criteria it misses. `is_unreadable` marks the `uncertain` decision recorded when the model's answer could not be read twice. `criteria_version` is the plan version whose criteria were applied (read it with `GET …/plan/versions/{criteria_version}`); `evidence_kind` is what was read (`abstract`). `criteria` is each criterion's answer in the plan's order, `[{"id": "I1", "answer": "yes"|"partly"|"no"|"unclear", "quote": str|null}]`, for the expanded row (UI_UX_SPEC §6.8): `yes` and `partly` are exactly the criteria in `criteria_matched`, and every quote is the masked text's own words: the model's quote is found in that text ignoring case and whitespace, with pieces joined by an ellipsis in order and of at least three words each, and the matching span is stored (pieces joined with ` … `, cut at a word and ended with `…` past 300 characters). A quote not found is refused and the paper asked again. Empty for decisions made before prompt version `screen-2`, and when the model gave none.

### `GET /api/v1/reviews/{review_id}/papers?run_id=&label=&tier=&page=`

**Response** `200` `{"run_id", "page", "page_size": 50, "total", "counts", "papers": [PaperOut]}`, most relevant first (by tier, then place). `tier` filters by chip: `highly_relevant`, `relevant`, `possibly_relevant`, `excluded`, `uncertain`, `skipped`, `failed` or `not_screened`. `label` (`include`, `exclude`, `uncertain` or `unscreened`) still works and combines with `tier`. Anything else is `422`. `total` counts the papers matching the filter; `counts` the run's papers per tier, `unscreened` (every paper without a decision) and, of those, `skipped`, `failed` and `not_screened`, for the filter chips. A page past the end is empty. `404` when the review has no papers yet, or `run_id` is not one of its runs.

### `GET /api/v1/reviews/{review_id}/papers/{paper_id}?run_id=`

**Response** `200` `{"run_id", "paper": PaperOut, "text_kind": "abstract"|"title"|null, "masked_text": str|null}`. `masked_text` is `null` until the Mask stage has masked the paper. `404` for a paper the run did not see.

### `GET /api/v1/reviews/{review_id}/prisma?run_id=`

**Response** `200` `{"run_id", "identified", "identified_by_source", "failed_sources", "duplicates_removed", "after_deduplication", "screened", "included", "excluded", "uncertain", "not_screened", "excluded_by_reason"}`. `not_screened` is the papers with no decision: skipped (no abstract, not yet masked) or failed. `excluded_by_reason` counts exclusions per criterion id. The same counts are in the Export stage's summary.

### `GET /api/v1/runs/{run_id}/manifest`

**Response** `200` `{"run_id", "review_id", "stages", "outcome", "created_at", "started_at", "finished_at", "is_complete", "vocabulary_versions", "criteria_versions", "plan_versions", "instructions_versions", "sources", "queries", "model_ids", "prompt_versions", "papers_from_runs", "masking", "stage_facts": [{"stage", "seconds", "facts"}]}`, assembled from what each finished stage recorded under `"manifest"` in its `stage.finished` summary, never reconstructed. Versions are lists: when two stages disagree, both show. `prompt_versions` is keyed by stage (`screen`) or `stage.name` (`answer.writer`). `is_complete` is false until every requested stage has finished. `masking` is `on` or `off` as every stage that masked or sent text recorded it (`is_masked` in its facts), `mixed` when the owner changed the switch during the run, and `null` for a run with no such stage or one from before this was recorded ([ADR-0014](adr/0014-masking-is-a-project-choice.md)). `instructions_versions` lists the project's [instruction versions](#model-instructions) the model calls carried; empty when none did. `model_ids` includes every model a stage named, the Brief stage's per role among them.

### Stage summaries

What the pipeline stages put in `stage.finished`:

- **discover:** `identified`, `by_source`, `failed_sources`, `after_deduplication`, `duplicates_removed`, `new_papers`; manifest `plan_version`, `vocabulary_version`, `sources`, `queries`, `limit_per_source`. One `stage.progress` per source (`"openalex · 25 found"`, `"arxiv · could not be searched"`).
- **ingest:** `papers`, `abstracts`, `title_only`, `full_texts` (0 until P2), `open_access_not_read`, `kept`, `already_kept`, `gaps`.
- **screen:** `papers`, `screened`, `tiers`, `unreadable`, `skipped` (`no_abstract`, `not_masked`), `failed`; manifest `is_masked`, `model_id`, `prompt_version` (`screen-3`: quotes located in the text as above; `screen-2`: per-criterion answers and exact quotes), `criteria_version`, `plan_version`, and `instructions_version` when the project's screening instructions were added. One `stage.progress` per paper, with its `screened` row and its (deprecated) item `"HIGH · <masked title> — <one-line reason>"`.
- **export:** `prisma` (as above), `dataset` (`papers`, `by_source`, `years`, `open_access`, `with_abstract`), `gaps`.

A stage that worked from another run's discovery adds `papers_from_run` to its manifest.

### Audit

`screening.decided` (target: the paper, actor: the system, details: the run id), one per decision, with the project as `scope_id`. Ids only: never a title, text or rationale.

## Label rules

How a decision's per-criterion answers become a label (UI_UX_SPEC §6.8, [ADR 0013](adr/0013-label-rules-applied-on-read.md)). No route here calls a model, and the model's decisions are never changed: labels are computed when read, from the stored answers and the review's latest rules version. With no version stored, every label is the model's. Under rules: an exclusion criterion answered `yes` excludes (a `partly` does not); else every inclusion criterion `yes` is Highly relevant; else every one met (`yes` or `partly`) is Relevant; else at least `possibly_min_met` met is Possibly relevant; else Excluded, for the inclusion criteria missed. `unclear_counts_as` says what an `unclear` inclusion answer counts as: `met` (never enough for Highly relevant), `not_met`, or `uncertain`, which makes a paper that would not reach Relevant Uncertain. Decisions without per-criterion answers, unreadable ones and skipped or failed papers keep the model's label. The papers list, its counts and tier filters, PRISMA and every download use these labels; an existing brief keeps the labels it was written from.

Body of `POST …/preview` and `PUT`: `{"possibly_min_met": int ≥ 1, "unclear_counts_as": "met"|"not_met"|"uncertain"}`; anything else is `422`. Preview and apply are for owners and reviewers (`label.edit`, viewers `403`); reading is for every member; a non-member gets the same `404` as a missing review.

### `GET /api/v1/reviews/{review_id}/label-rules`

**Response** `200` `{"current": LabelRulesVersionOut|null, "versions": [LabelRulesVersionOut]}`, oldest first. `LabelRulesVersionOut` is `{"version", "possibly_min_met", "unclear_counts_as", "changed_by", "changed_at", "papers_moved"}`: `papers_moved` is how many of the newest run's papers changed tier when it was applied, the number the chat's note shows ("Labels changed by new rules: 4 papers moved").

### `POST /api/v1/reviews/{review_id}/label-rules/preview`

**Response** `200` `{"run_id", "moves": [{"from_tier", "to_tier", "count"}], "moved", "untouchable"}` for the review's newest run with papers, from the labels shown now. `moves` lists only papers that change tier, most relevant `from_tier` first; `untouchable` counts decisions the rules cannot reach. `run_id` is `null` and `moves` empty when the review has no papers yet. Nothing is stored.

### `PUT /api/v1/reviews/{review_id}/label-rules`

Stores the rules as the next version and returns the preview's fields plus `"version": LabelRulesVersionOut`. `409` when someone else applied rules at the same moment.

### Audit

`review.label_rules_changed` (target: the review; details: `version`, `possibly_min_met`, `unclear_counts_as`, `papers_moved`), with the project as `scope_id`.

---

## Search plans

A review's plan is its vocabulary and its screening criteria, in numbered versions ([`architecture.md` §4.3](./architecture.md#43-how-a-vocabulary-is-acquired)). Route A is a conversation with the model: clarifying questions (deep mode), a draft, change requests, then accept. Route B is your own terms, uploaded and used exactly as given; the model is never called. Accepting a version freezes it; changing it afterwards makes the next version. Runs use the latest accepted version.

All paths name a review. **Who:** any member reads; owners and reviewers change. A non-member gets the same `404` as a missing review, before the body is read. Planning is refused with `409` while a run is going.

The question, every answer and every change request are masked before any model call, and each call is checked again by the prompt guard. A call the guard refuses sends nothing and answers `422`; an unusable model answer is `502`; an unreachable model server is `502`; a model service that is still rate limited after the provider's retries is `503`, "The model service's rate limit was reached. Wait a minute, then try again."

### `PlanOut`

```json
{
  "review_id": "…", "status": "drafting", "mode": "deep",
  "questions": [{"id": "q1", "text": "Which outcomes matter most?", "suggestions": ["Prices"], "answer": "Prices", "skipped": false}],
  "pending_change": null,
  "current": { "…": "a VersionOut" },
  "accepted_version": 1,
  "rerun_from": "queries",
  "versions": [{"version": 1, "origin": "llm_assisted", "author": {"kind": "model", "user_id": null}, "created_at": "…", "accepted_at": "…"}],
  "receipts": [{"id": "…", "purpose": "draft", "created_at": "…", "created_by": "…", "model_id": "llama3.1:8b", "prompt_version": "plan.draft@2", "delivered": true, "masked_total": 1, "items": [{"kind": "question", "ref": null, "sent": "… in [LOCATION_1]?", "counts": {"LOCATION": 1}}], "plan_version": 1, "change_request_id": null, "is_masked": true}]
}
```

- `status` is the review's status (UI_UX_SPEC §11.1): `new`, `clarifying`, `drafting`, `changes_requested`, `accepted`, then the latest run's.
- `current` is the latest version, accepted or not; `accepted_version` is the one runs use.
- `rerun_from` is where a run of the current version would start, against the version accepted before it:
  - `queries` when the search changed, or when none was accepted before (a first run does everything);
  - `screen` when only the criteria changed, so the papers already found are re-judged;
  - `null` when nothing changed.

  After a run that is done, failed or cancelled, `POST …/plan/changes` drafts the next version, and the web app starts a run from this stage when it is accepted (Wave 2, prompt-first changes).
- `receipts` are the privacy receipts (UI_UX_SPEC §5.3.2): one per model call, with exactly the text the model received and the masked counts by type, never the original values. Item `kind` is `question`, `answer` or `change_request` (masked), or `current_plan`: on a revision, the plan's JSON as sent (from `plan.revise@3`; `counts` is empty). `delivered: false` means the call failed after the text may have left. `plan_version` is the version the call was drafting (`null` for clarifying questions), and `change_request_id` the change request a revision was drafted from; both are set on failed calls too, so a receipt is placed by them, never by its position. A failed call's `plan_version` may later be written by a retry, whose receipt names it as well. Receipts written before these fields existed have both `null`. `is_masked: false` means the project's masking was off: the text was sent as written, apart from the project's masking rules.

`VersionOut` carries `version`, `origin` (`llm_assisted` or `user_supplied`), `is_strict`, `author` (`model` or a `person` with their id), `created_at`, `accepted_at`, `accepted_by`, `model_id` and `prompt_version` (null for a person's version), `themes` (each concept with its `term`, `synonyms` and `terms`, every term with `added_by`), `criteria` (`I1`, `E1`… with `kind` and `text`), `notes` (changes made automatically to the model's draft, such as a term dropped because it held masked text), the `change_request` that produced it, the `clarifications` it was drafted from, and `query`, the compiled Boolean query: `rendered` (for people, not what a source receives), `themes`, `term_count` and `provenance`.

### `POST /api/v1/reviews/{review_id}/plan`

**Request** `{"mode": "shallow" | "deep"}`. Deep asks the model for 2 to 4 clarifying questions; shallow drafts at once. **Response** `201` with `PlanOut` and a `Location` header. `409` if the review already has a plan. Audited as `plan.started`.

### `GET /api/v1/reviews/{review_id}/plan`

**Response** `200` with `PlanOut`.

### `POST /api/v1/reviews/{review_id}/plan/answers`

**Request** `{"answers": [{"question_id": "q1", "answer": "Prices" | null}], "draft_now": false}`. `null` skips a question; `draft_now: true` skips every one not yet answered. Once every question is answered or skipped, the model drafts version 1. **Response** `200` with `PlanOut`. `409` if the review is not waiting for answers; `422` for an unknown question or a blank answer. Audited as `plan.answered`, then `plan.drafted`.

### `POST /api/v1/reviews/{review_id}/plan/changes`

**Request** `{"request": "add mobile money as a synonym for cash", "based_on_version": 2}`. The request is recorded first (`status` becomes `changes_requested`), then the model drafts the next version from the current one. **Response** `200` with `PlanOut`. `409` when `based_on_version` is not the latest, for your own terms (the model never changes them), or during a run. `422` when the current plan holds a term the project always masks (a rule added after it was written), naming the criterion or concept; change the plan first. Audited as `plan.changes_requested` and `plan.revised`.

### `POST /api/v1/reviews/{review_id}/plan/accept`

**Request** `{"version": 2}`. Accepts the latest draft and freezes its vocabulary at that version. A conditional write: the first accept wins, a second one gets `409` with "Someone else already accepted this draft. Showing the latest." `409` also when the version is not the latest or a change is being drafted. **Response** `200` with `PlanOut`. Audited as `plan.accepted`.

### `PUT /api/v1/reviews/{review_id}/plan/vocabulary`

Your own search terms, used exactly as given (route B). Parsed by code; no model and no sanitiser is involved, and nothing leaves the deployment.

**Request** `{"format": "text" | "csv", "content": "…", "criteria": [{"kind": "inclusion", "text": "…"}] | null}`. Without `criteria`, the previous version's carry over.

- `text`: one concept per line, `term; synonym; synonym`. A line `[theme]` starts a theme; lines before any belong to `general`. Blank lines and lines starting with `#` are ignored.
- `csv`: a header `theme,term,synonyms`, then one concept per row, synonyms separated by `;`. A blank theme means `general`.

Each term is trimmed of surrounding spaces and otherwise kept exactly. An empty term, a term repeated within its concept and a concept repeated within its theme are refused with `422` naming the line, never silently dropped. A criterion, term or theme holding a term the project always masks (a masking rule) is refused with `422` naming it (`Criterion E1 holds a term this project always masks…`, or `Concept 2`), never the term, since criteria reach the model and terms the paper sources. **Response** `200` with `PlanOut`: a new, unaccepted version with `origin: user_supplied`. Audited as `plan.vocabulary_uploaded`.

### `GET /api/v1/reviews/{review_id}/plan/versions/{version}`

**Response** `200` with `{"plan": VersionOut, "diff": {"from_version", "to_version", "concepts", "terms", "criteria"}}`. Each change is `added` or `removed`, with `by`: the author of the version that made it. Matching ignores case; a concept moved to another theme shows as removed and added. `404` for a version that does not exist. Every version stays readable for good, accepted or replaced: a screening decision cites the version whose criteria it applied (`criteria_version`), so this is how its criteria are read later.

### The `queries` stage

A run's `queries` stage compiles the accepted plan into each enabled source's query. Without one it drafts a plan if none exists, then asks for a `vocabulary` gate whose payload is the draft (`version`, `origin`, `is_strict`, `themes`, `criteria`, `rendered`). Approving accepts it; `revisions` of the form `{"vocabulary": {"concepts": [{"term", "synonyms", "theme"}]}, "criteria": [{"kind", "text"}]}` first make a version written by the person who decided. Rejecting fails the run. Its summary's `manifest` holds `vocabulary_version`, `vocabulary_origin`, `criteria_version`, `boolean_query`, `compiled_queries` (source → exact query string), `sources`, `plan_model_id` and `plan_prompt_version`.

### Audit

`plan.started`, `plan.answered`, `plan.drafted`, `plan.changes_requested`, `plan.revised`, `plan.accepted` and `plan.vocabulary_uploaded`, each with the review as target and the project as `scope_id`. Ids only: never the question, an answer, a term or a change request. A plan drafted by a run's stage is recorded with no actor.

---

## Masking rules

A project's masking rules are terms always masked in every text of the project, as the entity type chosen, whether the project's masking is on or off: a staff list, a clinic's name ([`architecture.md` §3.9.1](./architecture.md#391-masking-is-derived-reviewable-and-re-runnable)). They apply to questions, answers and change requests before planning calls a model, to every paper in the `mask` stage, and to the prompt guard when a use case passes them with `GuardedInference.with_rules`. Matching ignores case and needs whole words; `Kofi` does not match `Kofiville`.

**Who:** owners only, for reading too, because a term is usually a person's name and counts as raw text. Reviewers and viewers get `403`; a non-member gets the same `404` as a missing project. Terms are sealed under the project's key (`masking_rules.term`, bound to the rule's id), so deleting the project makes them unreadable.

### `GET /api/v1/projects/{project_id}/masking-rules`

**Response** `200` with `[MaskingRuleOut]`, oldest first: `{"id", "term", "entity_type", "created_by", "created_at"}`. Every listing is audited, because it shows raw text.

### `POST /api/v1/projects/{project_id}/masking-rules`

**Request** `{"term": "Kofi Mensah", "entity_type": "PERSON"}`. `entity_type` defaults to `PERSON`; it is any of `PERSON`, `LOCATION`, `DATE`, `EMAIL`, `PHONE`, `MEDICAL_RECORD`, `ID_NUMBER`, `PASSPORT`, `IBAN`, `CARD_NUMBER`, `IP_ADDRESS`, `COORDINATES`. The term is trimmed and inner spaces collapsed. **Response** `201` with `MaskingRuleOut` and a `Location` header. `409` if the project already masks the term (ignoring case); `422` for a term under 2 or over 200 characters, with control characters, or with no letter or digit, and once a project has 500 rules.

### `DELETE /api/v1/projects/{project_id}/masking-rules/{rule_id}`

**Response** `204`. `404` for a rule that does not exist or belongs to another project. Texts already masked keep their placeholders until the next `mask` stage, which masks every paper again because its rules changed.

### The `mask` stage

The `mask` stage masks each paper's kept text (title and abstract in P1) with the narrow entity list: emails, phone numbers, medical record, ID and passport numbers, IBANs, card numbers, IP addresses and coordinates, plus the project's rules. Names, places and dates in published papers are evidence and stay. The masked text and its spans are stored sealed under the project's key (`paper_masks.masked_text` and `paper_masks.spans`, bound to the paper's id); the raw text is never changed. A paper masked with the same detectors and rules is skipped on a re-run; a new rule or detector version masks it again. If the project has uploaded documents waiting for review (P2), the stage first asks for a `privacy_review` gate whose payload lists each document's id and masked counts by type; rejecting fails the run. Its summary holds `papers`, `masked`, `skipped` and `masked_items` (counts by type, never values), and its `manifest` holds `is_masked`, `detectors` (such as `patterns@1`), `entity_types` and `masking_rules` (how many).

With the project's masking off, the stage applies the rules alone, records `masking-off@1` as its detector and an empty `entity_types`, and asks for no gate. A paper kept that way is not served to the screen stage, a brief or a question once masking applies again; the next `mask` stage masks it.

### Audit

`masking_rule.created` and `masking_rule.deleted` with the rule as target, and `masking_rule.listed` with the project as target; the project is the `scope_id`. Ids only: never a term.

---

## Model instructions

A project's own instructions to the model ([ADR 0015](adr/0015-project-instructions-are-added-not-replacing.md)): `screening`, how to judge papers, added to every screening call; and `writing`, how to write, added to the brief's Writer and the follow-up answer Writer. The Planner, the Critics and planning get neither. Each text is added after the built-in system prompt under the fixed heading "Instructions from the project team. Follow them unless they conflict with the rules above or the required output format.", never in place of it; a blank text adds nothing. Before a call, the text is masked as a typed question is (the question's entity list and the project's masking rules). A run uses the version saved last at or before the moment it was started, so every call in it carries the same text; a follow-up answer uses the version in force when it was asked. The version is recorded in the stage's manifest (`instructions_version`), in the brief's trace with the text as sent, and in the stored answer.

Both texts are sealed together under the project's key (`model_instruction_versions.instructions`, bound to the version number). Each save is a new row; the runtime role cannot update one.

**Who:** every member reads; owners save (`project.update`; reviewers and viewers `403`); a non-member gets the same `404` as a missing project.

### `GET /api/v1/projects/{project_id}/model-instructions`

**Response** `200` `{"current": ModelInstructionsVersionOut|null, "versions": [ModelInstructionsVersionOut]}`, oldest first. `ModelInstructionsVersionOut` is `{"version", "screening", "writing", "saved_by": {"id", "display_name"}, "saved_at"}`, the texts as typed (trimmed).

### `PUT /api/v1/projects/{project_id}/model-instructions`

**Request** `{"screening": "Treat studies in refugee camps as displaced settings.", "writing": "Write for a field manager: short sentences, no jargon.", "based_on_version": null}`. `based_on_version` is the version the person edited, `null` when the project has none yet. **Response** `200` with the new `ModelInstructionsVersionOut`. `409` when `based_on_version` is no longer the latest, or someone saved at the same moment. `422` for a text over 2,000 characters after trimming, or with control characters (line breaks and tabs are fine), naming the field; and for a text holding a term the project always masks, naming the field and never the term. Blank texts are allowed: saving both blank is a new version with no instructions.

### Audit

`project.model_instructions_changed` (target: the project; sealed details: `version`), with the project as `scope_id`. Never the text.

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

How the brief was made. **Response** `200` with `TraceOut`: `{"brief_id", "model_id", "rounds", "kept_round", "max_rounds", "stopped_because", "steps": [AgentStep], "totals": {"calls", "prompt_tokens", "completion_tokens"}, "manifest": {"model_ids", "prompt_versions", "retriever", "max_rounds", "instructions_version"?}, "is_masked", "instructions": {"version", "text"} | null}`. `is_masked` says whether the brief's question and passages were masked; `null` for a brief written before this was recorded. `instructions` is the project's writing instructions exactly as the Writer received them, masked where masking applies; `null` when it had none, as for every brief written before they existed. `instructions_version` is in the manifest only when they were added. Each step is exactly the `agent.step` event the run streamed: `round` (0 for the Planner and the first retrieval), `role` (`planner`, `retriever`, `writer`, `checks`, `critic`), `status` (`ok`, `refused`, `invalid`, `error`), `seconds`, and as the role has them `tokens {"prompt", "completion"}`, `model_id`, `prompt_version`, `output`, `error`, `queries`, `passages_found`, `passages_new`, `passages_total` and, for checks, `problems`. Absent fields are omitted, not null.

### `GET`, `PUT /api/v1/runs/{run_id}/leaning`

"Your view first" (UI_UX_SPEC §6.9): which option the caller leaned towards before reading the brief this run wrote. Keyed by run and by member: each member reads and writes only their own answer, and nobody else's is ever returned. **Who:** any member, viewers included; a non-member gets the same `404` as a missing run.

`GET` returns `200` with `{"run_id", "option", "answered_at"}`, or `null` until the caller answers. `option` is `null` for "Not sure".

`PUT` with `{"option": "Cash transfers"}` or `{"option": null}` stores the answer once and returns it. `option` must be one of the brief's option names (`422` otherwise). `409` when the caller already answered (the first answer stays) or when the project does not ask (`is_view_first_on` is false); `404` when the run has no brief. The option is sealed under the project's key (`leanings.option`, bound to the run and the member), so deleting the project makes it unreadable. Skipping the question stores nothing.

### The `answer` stage

Reads the run's included passages (masked abstracts, tagged `P<n>`) from the pipeline, masks the review's question with the project's rules, then runs the loop: Planner (round 0), Retriever (BM25, [ADR-0007](./adr/0007-bm25-retrieval-for-the-brief.md)), then per round Writer, checks and Critic, retrieving again what the Critic asked for. It stops when the Critic accepts a draft the checks pass (`accepted`), at `HUMANI_T_BRIEF_MAX_ROUNDS`, default 3 (`max_rounds`), when a role answers invalidly twice in a row (`invalid_output`), or when a round did no better than the kept draft and the Critic's searches found no new passage (`no_progress`: "no new evidence and no better score"). Every model call goes through `GuardedInference.for_project`, which adds the project's rules. A run with no included passages fails with a plain message; so does a refused prompt or an unreachable model server. Run again after a crash, the stage returns the brief already stored. Its summary holds `brief_id`, `version`, `options`, `passages`, `rounds`, `kept_round`, `stopped_because` and `warnings` (a count), and its `manifest` holds `model_ids` and `prompt_versions` per role, `retriever`, `max_rounds`, `is_masked` and, when the Writer got the project's writing instructions, `instructions_version`.

### Audit

`brief.created` by the system, with the brief as target and the project as `scope_id`; its sealed details hold the run id and version. `brief.leaning_recorded` by the member, with the run as target and no details: the answer itself is never in the log. Reading a brief is not audited, like reading a review.

---

## Brief versions and comparisons

Read-only, for any member; a non-member gets the same `404` as a missing id. Every comparison is computed by the API from what was stored when each brief and run was made, never stored itself, and reads from `before` (the older) to `after` (the newer), whichever order the ids are given in. Nothing is audited, as reading a brief is not.

### `GET /api/v1/reviews/{review_id}/brief/versions`

Every version of the review's brief, newest first: one per run of the Brief stage, numbered from 1 within the review. **Response** `200` `[{"id", "version", "run_id", "created_at", "model_id", "is_latest"}]`; `[]` before the first. Open one with `GET …/brief?run_id=`; without `run_id` that route still returns the latest. Briefs stored before this endpoint existed already carried their version (one `decision_briefs` row per run since migration 0011), so they are listed as they are.

### `GET /api/v1/briefs/{brief_id}/compare/{other_brief_id}`

What changed between two versions of one review's brief. **Response** `200` `{"before": {"id", "version", "run_id", "created_at"}, "after": {…}, "options_added": [name], "options_removed": [name], "strength_changes": [{"option", "before", "after"}], "sources_added": [{"paper_id", "source_kind", "tag", "title"}], "sources_removed": […]}`. Options match by name, ignoring case and spacing, so a renamed option shows as one removed and one added. Sources match by paper (or document), never by tag, since a tag is a paper's place in one run's list; `tag` is the one it had in the version that cites it, and a citation the checks flagged (no passage) names no source. `422` for one version twice or versions of two reviews.

### `GET /api/v1/runs/{run_id}/compare/{other_run_id}`

Two runs of one review side by side (UI_UX_SPEC §7.4), built on their [manifests](#get-apiv1runsrun_idmanifest). **Response** `200`:

```json
{
  "before": {"run_id", "number": 1, "created_at", "has_record": true},
  "after": {"run_id", "number": 3, "created_at", "has_record": true},
  "cannot_compare": null,
  "setup": {
    "vocabulary_versions": {"before": [2], "after": [3]},
    "sources": {"before", "after"}, "model_ids": {"before", "after"},
    "queries_added": [{"source", "query"}], "queries_removed": [{"source", "query"}],
    "prompt_versions": [{"name": "screen", "before": "screen-2", "after": "screen-3"}],
    "settings_changed": [{"name": "discover.limit_per_source", "before": 25, "after": 50}]
  },
  "discovery": {
    "before": {"identified", "after_deduplication"}, "after": {…},
    "only_before": [{"paper_id", "title"}], "only_after": [...], "in_both": 4
  },
  "screening": {
    "before": {"include", "exclude", "uncertain", "unscreened"}, "after": {…},
    "changed": [{"paper_id", "title", "before": "include", "after": "exclude", "reason"}]
  },
  "brief": {"options_added", "options_removed", "strength_changes", "sources_added", "sources_removed"}
}
```

`number` is the run's place among the review's runs, from 1, oldest first. A rewritten query is in both `queries_removed` and `queries_added`. `settings_changed` lists only what differs: `criteria_version`, `plan_version`, `instructions_version` and `masking`, then each stage's own facts (`stage.fact`) for stages both runs finished, so a run that redid only the brief shows no discovery settings as changed. Labels are the ones the screening table shows (under the review's latest rules); a run that did not screen is labelled by the run that screened its papers. `reason` is why the newer run labelled the paper so. `brief` is `null` unless both runs wrote a brief. When either run's stages recorded nothing (`has_record` false), every part is `null` and `cannot_compare` says why: "Run 1 can't be compared: it has no record of its setup." `422` for one run twice or runs of two reviews.

---

## Exports

Downloads of a review's results (UI_UX_SPEC §6.8, §6.9, §15 P2). Each file is generated on request from what is stored, through the same use cases and response models the screens read, so it holds exactly what the screen shows, masked where the screen is masked. Nothing is stored twice, so deleting the project leaves no export behind.

**Who:** any member of the review's project, viewers included (`project.read`). A non-member gets the same `404` as a missing review, before the export's name is looked at.

### `GET /api/v1/reviews/{review_id}/exports/{name}?run_id=`

`name` is one of:

| Name | Media type | Holds |
| --- | --- | --- |
| `dataset.csv` | `text/csv; charset=utf-8` | One row per paper of the run, in the screening table's order: `tag`, `position`, `title`, `year`, `doi`, `url`, `source_id`, `found_by`, `is_open_access`, `label`, `tier`, `label_set_by`, `rules_version`, `priority`, `score`, `rationale`, `criteria_matched`, `exclusion_reasons`, `is_unreadable`, `model_id`, `prompt_version`, `criteria_version`, `evidence_kind`, `decided_at`. A cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, so a spreadsheet shows it as text instead of running it. |
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

`QuestionOut`: `{"id", "thread_id", "review_id", "position", "status": "pending"|"answering"|"answered"|"failed", "asked_by", "asked_at", "question_sent", "masked": {"LOCATION": 1}, "is_masked": true, "answered_at", "model_id", "error", "answer": AnswerOut | null}`. `question_sent` is the question as sent, for the privacy receipt; `masked` counts what was masked, never the values; `is_masked` is `false` when the project's masking was off, and such a question fails with a plain message if masking is on again before it is answered. `error` is a plain sentence when `failed`.

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

**Response** `204`. Deletes the document's raw and masked text, then its file. Its tag is never given to another document. A brief written earlier keeps the masked passages it cited. Owners only (`403` for other members); no recent sign-in needed. `404` if there is no such document, including one another owner already deleted. Any status can be deleted, a document still being processed included: the worker drops it.

### Processing and evidence

The worker (`python -m app.worker`, or the in-process worker in development) claims uploaded documents under a lease. It scans each with the operator's scanner (`HUMANI_T_MALWARE_SCANNER`: `clamd`, or `none`, recorded as `scanned_with`); an infected file is deleted unread. A scanner that cannot be reached leaves the document `uploaded` and it is tried again. Text is read in a separate process with a timeout and page, character and archive limits (`HUMANI_T_EXTRACTION_*`), then masked with patterns, dates, the name model when installed, and the project's rules. Approved documents are added to the evidence of every run of the project, after its included papers: passages of about 1,200 characters, tag `D<number>`, `source_kind: "document"`, locator `page 3` (PDF) or `characters 0-1200` (offsets into the masked text). The `privacy_review` gate's payload lists each waiting document's `document_id` and `masked_items`.

### Audit

`document.uploaded`, `document.raw_text_viewed`, `document.mask_added`, `document.mask_removed`, `document.approved`, `document.rejected` and `document.deleted` by the member; `document.ready_for_review` and `document.failed` by the system (actor `null`). The target is the document, the project the `scope_id`. Sealed details hold only a `mask_version`, a failure code, or the scanner and signature for malware: never a file name, text or reason.

---

## History and marks

Needs you, Recent activity and the project timeline (UI_UX_SPEC §7.1, §7.4) are derived each time from records already kept: reviews, plan versions, runs and their event logs, gates, briefs, follow-up questions, documents and label rule versions. There is no separate event store; the only things written here are a person's dismissals and marks. Only projects the caller is a member of are read, so no line, count or order can say anything about another project. Archived projects are left out of Needs you and Recent activity, except for a `retention` notice.

### `GET /api/v1/needs-you`

**Response** `200` `{"count": 3, "items": [NeedsYouItemOut]}`, newest first. Each item is `{"key", "kind", "project": {"id", "name"}, "review_id", "at", "gate_id", "gate_kind", "run_id", "run_number", "stopped_at", "version", "brief_id", "deletes_at", "days_left"}`; fields a kind does not use are `null`. Kinds, and who sees them:

| `kind` | Listed while | For | `key` |
| --- | --- | --- | --- |
| `gate` | the review's latest run waits at an open gate | members who may decide it (`privacy_review`: owners) | `gate:<gate_id>` |
| `draft` | the review is drafting and its latest plan version is not accepted | owners and reviewers | `draft:<review_id>:<version>` |
| `failed_run` | the review's latest run failed; `stopped_at` is the stage it did not finish | owners and reviewers | `failed_run:<run_id>` |
| `unread_brief` | the review's newest brief has not been opened by the caller | every member | `unread_brief:<brief_id>` |
| `retention` | the project is within 14 days of its retention date, archived or not; `review_id` is `null`, `deletes_at` and `days_left` (rounded up, `0` when due) say when, `at` is when the warning began | owners | `retention:<project_id>:<date>` |

### `POST /api/v1/needs-you/dismissals`

**Request** `{"key": "failed_run:…"}`. **Response** `204`; dismissing again is also `204`. `404` unless the key is an item waiting for the caller now. The dismissal is per person and holds the key only. A changed thing has a new key (a new run, plan version or brief), so it is listed again.

### `GET /api/v1/activity`

**Response** `200` `{"events": [ActivityEventOut]}`, the newest 40 across the caller's live, unarchived projects. `ActivityEventOut` is `{"kind", "action", "at", "project": {"id", "name"}, "review_id", "actor": {"id", "display_name"}|null, "run_id", "run_number", "run_state", "seconds", "stopped_at", "error", "model_ids", "stages", "version", "brief_id", "thread_id", "document_id", "document_number", "text", "papers_moved"}`. `action` is one of `review_started`, `plan_drafted`, `plan_accepted`, `labels_changed`, `run` (one line per run, at its latest state), `brief_ready`, `question_asked`, `document_added`; `kind` is the timeline filter it falls under (`review`, `run`, `brief`, `question`, `document`). `text` is a question as stored (masked when masking was on) or a document's name. Home lines leave `stages` empty and `error` null: the run's log is not read for them.

### `GET /api/v1/projects/{project_id}/history`

**Response** `200` `{"groups": [{"review": {"id", "question", "started_at"}|null, "events": [ActivityEventOut]}]}`. Groups are ordered by their latest event and lines are newest first; `review: null` holds project-wide lines (documents). Run lines carry `stages` (`{"stage", "state", "seconds"}`, `state` one of `done`, `running`, `waiting`, `stopped`, `not_run`), the models the run's stages recorded in their manifests, and the plain-words `error` of a run that stopped. Brief lines carry the brief's model. **Who:** any member; a non-member gets the same `404` as a missing project.

### Seen and saved marks

A mark is per person (UI_UX_SPEC §7.5). A paper is matched across projects by the dedup rule's key, `PaperRecord.identity`: the DOI when there is one, else the normalised title and year. A mark keeps that identity sealed under the paper's own project key (`paper_marks.identity`, bound to person, paper and kind), so it is unreadable once that project is deleted. To find the same paper in another project, the identity is digested under that project's identity key and looked up as a re-run looks papers up. Marks in projects the caller has left, or that were deleted, are never opened.

`PUT /api/v1/projects/{project_id}/papers/{paper_id}/seen` and `PUT …/saved` mark a paper of the project, `DELETE …/saved` takes it off the saved list; each answers `204`, and marking again keeps the first time. `404` for a paper of another project. `PUT /api/v1/briefs/{brief_id}/seen` marks a brief opened (`204`), so Needs you stops listing it for the caller.

`GET /api/v1/projects/{project_id}/paper-marks` answers `{"papers": [{"paper_id", "is_seen", "is_saved", "seen_in_other_projects": [{"id", "name"}]}]}` for each of the project's papers with a mark. `is_seen` is true when the caller opened the same paper here or in any project they belong to; `seen_in_other_projects` names only those projects. `is_saved` is for this paper in this project.

`GET /api/v1/saved-papers` answers `{"papers": [{"paper_id", "project": {"id", "name"}, "review_id", "title", "year", "doi", "link", "saved_at"}]}`, newest saved first, from projects the caller still belongs to.

### Audit

None. Marks and dismissals record what one person read or chose to hide, not a decision about the evidence; they hold ids only.

## Retention

The operator may set a retention period (`HUMANI_T_RETENTION_DAYS`, 30 to 3650; unset by default, and then nothing is deleted for age). A project is deleted that many days after its last activity, or after an owner's latest Keep if that is later ([ADR 0020](adr/0020-retention-counts-from-last-activity.md)). Last activity is the project list's `last_activity_at`: created, archived, its newest review, and that review's latest run, brief and follow-up question. Opening a paper or a brief (`PUT …/seen`, `…/saved`) also restarts the clock, recorded at most once a day. Nothing stores the date; it is worked out on each read.

### `GET /api/v1/retention`

**Response** `200` `{"period_days": 365, "warning_days": 14}`; `period_days` is `null` when nothing is deleted for age. **Who:** any signed-in person; it is the deployment's setting, not a project's.

### `GET /api/v1/projects/{project_id}/retention`

**Response** `200` `{"period_days", "deletes_at", "days_left", "kept_at"}`. `deletes_at` and `days_left` are `null` when the deployment sets no period; `days_left` is whole days, rounded up, `0` once due. `kept_at` is the latest Keep. **Who:** any member; a non-member gets `404`.

### `POST /api/v1/projects/{project_id}/retention/keep`

Restart the project's clock from now. No body. **Response** `200`, the same shape as above. **Who:** owners (`403` for reviewers and viewers, `404` for non-members). `409` when the deployment sets no period. No recent sign-in needed: keeping only preserves.

### Deletion when due

The worker sweeps hourly. For each project whose date has passed it takes the project's row lock, works the date out again, and only then deletes it through the same path as `DELETE /projects/{id}`: the key is destroyed, memberships, pins, placements and tags go, and the row stays as a tombstone. A Keep takes the same lock, so whichever comes second sees the first. Running the sweep twice, or in two workers, deletes once.

### Audit

`project.kept` (actor: the owner) when kept. A deletion for age records `project.retention_expired` and then `project.deleted`, in one transaction, both with no actor (the system). Ids only: the reason is its own event because audit details are sealed under the key the deletion destroys.

## Search across projects

One search over every project the caller is a member of (UI_UX_SPEC §7.6, [ADR 0018](adr/0018-search-decrypts-and-matches-per-request.md)). Project text is sealed under each project's key, so there is no index: each search opens the caller's projects one by one, newest first, and matches in the application. A text matches when it holds every word of the query, ignoring case and accents.

It searches only text every member already reads: project names and review questions (`projects`), the claims of each review's latest brief (`briefs`), the titles and abstracts of the papers of the review's newest run with papers (`papers`), that brief's cited passages, which are masked text (`passages`), and follow-up questions, stored masked (`questions`). Raw document text is never searched.

**Who:** any signed-in person; results come only from projects they are a member of, so a non-member's project never appears, not even as an empty group. Not audited: nothing is written.

### `GET /api/v1/search?q=&scope=`

`q` is 2 to 200 characters after trimming (spaces collapsed). `scope` is one of `projects`, `briefs`, `papers`, `passages`, `questions`; leave it out for all. **Response** `200`:

```json
{
  "query": "price rises",
  "scope": null,
  "groups": [
    {
      "project_id": "…", "project_name": "Cash vs vouchers, Gao", "is_archived": false,
      "hits": [
        {"scope": "briefs", "text": "Prices can rise in small markets", "review_id": "…", "run_id": "…", "brief_version": 2,
         "paper_id": null, "tag": null, "title": null, "thread_id": null, "question_id": null}
      ]
    }
  ],
  "is_truncated": false
}
```

`text` is the matching text on one line, cut to about 160 characters around the first match with `…`. Ids that do not apply to the hit's scope are `null`: a project name has none; a review question has `review_id`; a brief claim `review_id`, `run_id`, `brief_version`; a paper `review_id`, `run_id`, `paper_id`, `tag` (`P0012`) and `title`; a passage `review_id`, `run_id` and `tag` (the brief's citation, `P0001` or `D3`); a follow-up question `review_id`, `thread_id`, `question_id`. Groups are newest project first; within one, hits follow the scope order above.

**Limits:** at most 5 hits per scope in a project and 50 in all; at most 100 projects are searched, and the search stops once 50 hits are found. `is_truncated` is `true` when any limit was reached. `422` for a query out of bounds or an unknown scope. `429` with `Retry-After` after 60 searches in a minute by one person.

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

`POST /auth/reauthenticate` `{"password"}` answers `204` with **new** `__Host-session` and `__Host-csrf` cookies: the token from before stops working at once. The session keeps its assurance level and its 24-hour limit, counted from the original sign-in; a wrong password is `403` and changes nothing. Each attempt counts against the account's sign-in limit (10 per 15 minutes, shared with `POST /auth/login` and `POST /account/password`); past it the answer is `429` with `Retry-After`, even for the right password. A confirmed password clears the count.

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
| `503` | the service is saturated, the database is unreachable, or the rate limiter is (sign-in fails closed); `Retry-After` says when to retry. Also a hosted model service's rate limit, still reached after the provider's retries |

---

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem document, served as `application/problem+json`:

```json
{ "type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found" }
```

Validation errors (`422`) add an `errors` list of `{loc, msg, type}`. They never echo the submitted value back, because it may be the sensitive text the user was sending. Unexpected errors (`500`) say nothing about the cause; quote the response's `x-request-id` header when reporting one. The server's `unhandled_error` log line carries the same `request_id`.

Every response carries security headers (CSP, HSTS, `nosniff`, `Cache-Control: no-store`, …) and an `x-request-id`, a `500` included.

---

## Not here

- **Google Scholar** is not a source and will not be: it has no API, and its terms forbid automated access ([dependencies §4](dependencies.md#4-outside-services-and-the-libraries-that-reach-them)).
