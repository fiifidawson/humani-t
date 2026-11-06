# 07 — Projects and roles

> **Milestones:** M10 · **Status:** done · **Previous:** `06-mfa` · **Next:** `08-runs-events-and-gates`

Before this phase, every signed-in user could do the same things, and the search endpoints were open to anyone. Now a signed-in user can create a **project**, share it with existing accounts as owner, reviewer or viewer, rename and archive it, and delete it completely. Every request about a project checks membership: a non-member gets the same `404` as for a project that does not exist, and a member whose role does not allow the action gets `403`. A project's name is stored only encrypted under the project's own data key. Deleting a project destroys that key, so every copy of its data, backups included once they expire, becomes unreadable, while the audit chain still verifies. Search now runs inside a project, and compiling a vocabulary or listing sources needs a sign-in. This serves brief rules 7, 8, 15, 16 and 17, [`architecture.md` §4.1](../architecture.md#41-access-and-accountability) and [§5.5](../architecture.md#55-authorisation-and-audit).

## What this phase delivers

- A signed-in user creates a project and becomes its only owner; the project gets its own data key in the same transaction.
- A user lists only the projects they belong to, newest first, each with their own role; archived projects are hidden unless asked for.
- Every member can open a project and see who else belongs to it, by display name (never by email).
- Owners rename, archive and unarchive. An archived project keeps everything and is not read-only.
- Owners add an existing, enabled account by email with a role, change roles and remove members; anyone can leave. Adding, changing and removing someone else need a sign-in within the last 15 minutes; leaving does not.
- The last owner can never be demoted, removed or leave, even when owners act at the same moment.
- Owners delete a project (recent sign-in): its key is destroyed, its memberships removed, its row kept as a tombstone. It then looks exactly like a project that never existed.
- Non-members, including instance administrators, get `404` for everything about a project; the body is identical to a missing project's.
- Every project action is audited with ids only: `project.created`, `project.renamed`, `project.archived`, `project.unarchived`, `project.deleted`, `membership.added`, `membership.role_changed`, `membership.removed`.
- **Endpoints added:**
  - `POST /api/v1/projects` and `GET /api/v1/projects`;
  - `GET`, `PATCH` and `DELETE /api/v1/projects/{project_id}`;
  - `POST /api/v1/projects/{project_id}/archive` and `/unarchive`;
  - `GET` and `POST /api/v1/projects/{project_id}/members`;
  - `PATCH` and `DELETE /api/v1/projects/{project_id}/members/{user_id}`;
  - `POST /api/v1/projects/{project_id}/search`.
- **Endpoints changed:** `POST /api/v1/search` is gone (replaced by the project-scoped one). `POST /api/v1/vocabulary/compile` and `GET /api/v1/sources` need a signed-in user.
- **Migration `0005`:** tables `projects` and `memberships`.
- **No new settings and no new operator commands.**

### Data model

| Table | Columns | Purpose and rules |
| --- | --- | --- |
| `projects` | `id` (primary key), `name_sealed`, `created_at`, `created_by` (→ `users.id`), `archived_at`, `deleted_at`, `version` | One row per project, kept as a tombstone after deletion. `name_sealed` is the name encrypted with AES-256-GCM under the project's data key, bound to `(project id, "projects.name", project id)`. `version` is bumped by every save; a stale copy is refused. |
| `memberships` | `project_id` (→ `projects.id`) and `user_id` (→ `users.id`) together the primary key, `role`, `added_at`, `added_by` (→ `users.id`, null for the creator) | Check constraint `role_is_known`: `role IN ('owner', 'reviewer', 'viewer')`. Index `ix_memberships_user_id` serves "my projects". Every row of a project is deleted when the project is. |

## Architecture choices and why

### D7.1 One pure policy function, deny by default

- **Decision:**
  - `can(role, action)` in `app/domain/projects.py` answers from one table of 12 actions taken from [UI_UX_SPEC §3.3](../UI_UX_SPEC.md#33-roles). `None` (not a member) and any action missing from the table give `False`.
  - Rename and archive are owner-only. The UI spec groups them with tags and collection under project settings.
- **Why:** who may do what is the rule most likely to drift if it is spread across routes. One table makes every change a visible diff, and a test checks every role × action pair.
- **Rejected:**
  - role checks written inline in each route: they drift and cannot be tested as a whole;
  - a role hierarchy (owner > reviewer > viewer): "leave" and future actions do not follow a strict order.
- **Costs:** a new action needs a decision in the table before it can be used. That is intended.
- **Where:** [`app/domain/projects.py`](../../services/api/app/domain/projects.py).
- **Proven by:** `tests/test_projects_domain.py::TestPolicy` (`test_the_table_decides_every_action`, 36 parametrised cases of `test_each_role_may_do_exactly_what_the_table_allows`).

### D7.2 Non-members get 404, before the body is read

- **Decision:**
  - Every route below `/projects/{project_id}` declares the action it needs with `require(action)` (`app/api/access.py`). This is a FastAPI dependency, so it runs before the request body is validated.
  - It answers, in order: `401` (no session) or `403` (no CSRF token on a write); then `404` "No such project." for a missing project, a deleted one, or one the caller does not belong to; then `403` for a role that does not allow the action; then `401` `reauthentication_required` when a recent sign-in is needed.
  - The use case then checks again inside its own transaction.
- **Why:**
  - Answering `403` to a non-member would confirm the project exists. Project ids then become something an attacker can probe.
  - Checking before the body means the answer cannot depend on what was sent.
  - The second check is the one that counts: membership can change between the gate and the write.
- **Rejected:**
  - checking only inside the use case: a non-member would get `422` for a bad body, and `404` only for a good one;
  - checking only in the gate: a time-of-check, time-of-use gap.
- **Costs:** two short reads per request instead of one.
- **Where:** [`app/api/access.py`](../../services/api/app/api/access.py), [`app/application/projects.py`](../../services/api/app/application/projects.py) (`_access`).
- **Proven by:** `tests/test_route_inventory.py::TestMembershipIsRequired`, `tests/test_projects_api.py::TestReadProject`.

### D7.3 A test walks every route

- **Decision:**
  - `tests/test_route_inventory.py` reads the app's own route table through `fastapi.routing.iter_route_contexts`. FastAPI 0.141 keeps each included router as a single entry in `app.routes`; this function flattens them.
  - It asserts that every route outside `PUBLIC_ROUTES` answers an anonymous request with `401`, and every signed-in write without a CSRF token with `403`.
  - It asserts that every route containing `{project_id}` answers a non-member and an instance administrator with `404`, identical to a missing project.
- **Why:** feature tests cover the routes someone remembered. This covers the routes added later, the moment they exist. A route that should be public has to be named in the allowlist, where a reviewer sees it.
- **Rejected:** reading the OpenAPI schema instead. Routes hidden with `include_in_schema=False` would escape the check.
- **Costs:** a new public route must be added to the allowlist deliberately. That is the point.
- **Proven by:** the file itself; `test_the_public_allowlist_names_only_routes_that_exist` keeps the list honest.

### D7.4 The project's name is encrypted under its own key

- **Decision:**
  - The domain `Project` has no plaintext name, only `name_sealed`.
  - `ProjectService` seals the name with `ScopeEncryption`, under the project's data key and the context `(project id, "projects.name", project id)`, and opens it only to build a member's view.
  - The name is trimmed and checked by `normalize_project_name`: 1 to 200 characters, no control characters, no bidirectional overrides.
- **Why:** a name like "Cholera response, Sana'a" reveals an operation, a place and a population. Brief rule 15 asks for project content encrypted at rest. Deletion (D7.6) must make the name unreadable too, including in backups.
- **Rejected:** a plaintext `name` column. It is simpler and sortable in SQL, but a database copy or backup would reveal what every team works on, forever.
- **Costs:** listing decrypts one name per project. Sorting or searching by name happens after decryption, not in SQL.
- **Where:** [`app/application/projects.py`](../../services/api/app/application/projects.py) (`_seal_name`, `_view`).
- **Proven by:** `TestCreateProject::test_the_name_is_never_stored_in_the_clear`, `test_the_stored_name_decrypts_only_with_the_projects_own_key`, `TestRenameProject::test_the_new_name_is_not_stored_in_the_clear_either`.

### D7.5 The last owner, under concurrency: lock the project row

- **Decision:**
  - Every membership change (add, change role, remove, leave) and the delete start by locking the project's row with `SELECT … FOR UPDATE` (`ProjectRepository.lock`).
  - Inside the same transaction they read every membership and apply `require_an_owner_remains`, which raises `LastOwnerError` (`409` with `"last_owner": true`). Then they write, and append the audit event last.
  - See [ADR-0002](../adr/0002-project-row-lock-for-membership-changes.md).
- **Why:** the rule spans rows. Two owners stepping down at once each see "two owners" and both commit. Per-row conditions and versions cannot see the other row.
- **Rejected:**
  - a version bump on the project for every membership change: correct, but the loser gets a generic `409`;
  - `SERIALIZABLE` isolation: correct, but every caller must retry;
  - a counting trigger: possible later as defence in depth.
- **Costs:** membership changes on one project happen one at a time. In a race, the loser gets `409 last_owner`, or `404` if the winner removed it.
- **Where:** [`app/storage/postgres/storage.py`](../../services/api/app/storage/postgres/storage.py) (`PostgresProjectRepository.lock`), [`app/application/projects.py`](../../services/api/app/application/projects.py).
- **Proven by:**
  - `tests/test_projects_domain.py::TestLastOwnerRule`, including a hypothesis property: no sequence of allowed changes leaves no owner;
  - `tests/test_projects_api.py::TestLastOwner`;
  - `tests/test_projects_storage_contract.py::TestLastOwnerUnderConcurrency`, five owners racing on real Postgres;
  - `TestPostgresProjectLock`.

### D7.6 Delete is crypto-shredding plus a tombstone

- **Decision:**
  - `DELETE /projects/{id}` (owner, recent sign-in) runs in one transaction under the project lock:
    - destroy the data key (`ScopeEncryption.destroy_scope`);
    - delete every membership;
    - save the row with `deleted_at`;
    - record `project.deleted`.
  - The tombstone keeps the id from being reused, and keeps the dates the audit log refers to.
  - Confirming by typing the name is the web app's job; the endpoint has no body.
- **Why:** brief rule 17 asks for complete deletion, including from places that are hard to find, such as audit details, later documents, and backups. Destroying the key reaches every copy at once.
  - Memberships go because "who worked on what" is itself sensitive.
  - The audit chain must stay verifiable, so events are never deleted. They hold ids only, and their details are sealed under the now-destroyed key.
- **Rejected:**
  - deleting the project row: loses the record that the project existed, which the audit trail refers to;
  - a soft delete that keeps the key: not deletion.
- **Costs:** backups keep the wrapped key until they expire. Deletion is complete only after the backup retention window ([D3.4](03-envelope-encryption.md#d34-crypto-shredding-and-its-limits)).
- **Proven by:** `TestDeleteProject`, in particular `test_a_copy_taken_before_deletion_can_never_be_decrypted` and `test_the_audit_chain_still_verifies_after_deletion`.

### D7.7 Members are added by the email of an existing account

- **Decision:**
  - `POST …/members` takes `{email, role}`, matched case-insensitively against existing, enabled accounts.
  - An unknown or disabled account gets `422` "No active account uses this email address.", the same body for both, and the email is never echoed.
  - It is `422`, not `404`, because on a project route the web app reads `404` as "no such project".
  - Inviting people who have no account yet is out of scope; an administrator invites them first.
- **Why:** it keeps registration invite-only and reuses verified accounts.
- **Rejected:** creating project invitations for unknown emails. That needs its own token flow and its own rules for who may invite.
- **Costs:** an owner can learn whether an email has an active account. This is accepted inside one organisation's deployment, and the answer does not distinguish disabled from unknown.
- **Proven by:** `TestMembers::test_an_email_with_no_account_is_refused_as_bad_input`, `test_a_disabled_account_looks_exactly_like_no_account`.

### D7.8 Search runs inside a project

- **Decision:**
  - `POST /api/v1/search` became `POST /api/v1/projects/{project_id}/search`, gated by `run.manage` (owner, reviewer).
  - `POST /vocabulary/compile` and `GET /sources` need a signed-in user but no project.
- **Why:** search results become the project's evidence, and with real sources a search is a request leaving the deployment on the project's behalf. Compiling is pure and reads no project data. The source list describes the deployment's configuration and should not be public.
- **Rejected:** scoping compile to a project too. That adds friction and protects nothing.
- **Costs:** existing clients of `/search` must change their path. The web app is not built yet, so there are none.
- **Proven by:** `tests/test_projects_api.py::TestVocabularyAndSearch`, `tests/test_api.py::TestSearch`.

### Deliberately not used

| Not used | Why not |
| --- | --- |
| A permissions library (Casbin, Oso) | One table of 12 actions does not need a policy language. A library would hide the table that reviewers need to see. |
| Row-level security in Postgres | It would duplicate the policy in SQL. Membership is checked in the application, where it is tested once for both storage backends. |

## Libraries

No new libraries. This phase uses FastAPI dependencies, SQLAlchemy Core's `with_for_update()`, and the encryption and audit machinery from phases [03](03-envelope-encryption.md) and [04](04-audit-log.md).

## Files to read, in order

Start from the rules, then follow a request inwards from HTTP.

1. [`services/api/app/domain/projects.py`](../../services/api/app/domain/projects.py): `Role`, `Action`, the permission table and `can`; `normalize_project_name`; `Project` (no plaintext name) and `Membership`; `require_an_owner_remains`.
2. [`services/api/app/storage/base.py`](../../services/api/app/storage/base.py): `ProjectRepository` (note `lock`) and `MembershipRepository` (conditional `change_role` and `remove`).
3. [`services/api/migrations/versions/20261103_0005_projects.py`](../../services/api/migrations/versions/20261103_0005_projects.py): the two tables.
4. [`services/api/app/storage/postgres/storage.py`](../../services/api/app/storage/postgres/storage.py): `PostgresProjectRepository.lock` (`FOR UPDATE`), the version-checked `save`, and `list_for_member`'s join.
5. [`services/api/app/application/projects.py`](../../services/api/app/application/projects.py): `_access` (404, then 403, then recent sign-in), `create` (key, row, membership and audit in one transaction), `delete`, `change_role` and `remove_member` (lock, then rule, then write, then audit).
6. [`services/api/app/api/access.py`](../../services/api/app/api/access.py): the `require(action)` gate.
7. [`services/api/app/api/routes/projects.py`](../../services/api/app/api/routes/projects.py): which action each route declares.
8. [`services/api/app/api/errors.py`](../../services/api/app/api/errors.py): `LastOwnerError` and `AlreadyMemberError` as `409`, `NoSuchAccountError` as `422`.
9. [`services/api/tests/test_route_inventory.py`](../../services/api/tests/test_route_inventory.py): the public allowlist.
10. [ADR-0002](../adr/0002-project-row-lock-for-membership-changes.md).

## Set up

Migrate the development and test databases to `0005` (from [S0.5](00-setup.md#s05-install-dependencies-and-migrate-the-development-database) and [S0.6](00-setup.md#s06-migrate-the-test-database)):

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
```

Then start the API ([S0.9](00-setup.md#s09-start-the-api)). You need three accounts besides the admin: invite and register `kofi@example.org` and `maya@example.org` as in [T5.6](05-accounts-and-sessions.md#t56-invitations-and-registration) (Kofi Mensah, Maya Diallo), and note their user ids. Set up a second device in Postman for Kofi as in [S0.10](00-setup.md#s010-postman) (`session_b`, `csrf_b`). Also add the cookie-capture script to the Post-response tab of `POST {{base}}/account/password` and of `POST {{base}}/auth/reauthenticate`: a password change and a step-up both issue new cookies and revoke the old session.

If MFA is required on your deployment (phase `06-mfa`), finish each sign-in with the second factor before these tests.

## Manual tests

All requests carry the session cookie and `X-CSRF-Token` header from [S0.10](00-setup.md#s010-postman). "As admin" means the main variables after signing in as `admin@example.org`; "as Kofi" means device B.

### T7.1 Create and list

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.1.1 | As admin: `POST {{base}}/projects` `{"name":"  Cholera response, Sana'a  "}` | `201`; body `id`, `name` = `Cholera response, Sana'a` (trimmed), `role` = `owner`, `archived_at` = `null`; header `Location: /api/v1/projects/<id>`. **Note the id** as `{{project}}` | Creator becomes owner |
| T7.1.2 | `GET {{base}}/projects` | `200`, a list holding that project with `role: owner` | Listing |
| T7.1.3 | As Kofi: `GET {{base}}/projects` | `200`, `[]` | Only your own projects |
| T7.1.4 | `POST {{base}}/projects` `{"name":""}`, then `{"name":"Gao\nresponse"}` (a real newline, `\n` in the JSON), then `{"name":"x","role":"owner"}` | `422` each time | Name rules (D7.4), no mass assignment |
| T7.1.5 | pgAdmin (as `postgres`, database `humani_t`): `SELECT id, name_sealed, created_by, version FROM projects;` | `name_sealed` is unreadable bytes; nothing reads "Cholera" | The name is encrypted (D7.4) |
| T7.1.6 | pgAdmin: `SELECT scope_id, destroyed_at FROM data_keys WHERE scope_id = '<project id>';` | One row, `destroyed_at` null | A data key per project |

- **If it fails:** readable text in `name_sealed` means the name was stored without sealing. That is a privacy bug, not a cosmetic one.

### T7.2 Non-members see nothing

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.2.1 | As Kofi: `GET {{base}}/projects/{{project}}` | `404`, `{"type":"about:blank","title":"Not Found","status":404,"detail":"No such project."}` | Non-members get 404 (D7.2) |
| T7.2.2 | As Kofi: `GET {{base}}/projects/00000000-0000-4000-8000-000000000000` | **Byte-for-byte the same body as T7.2.1** | No existence leak |
| T7.2.3 | As Kofi: `POST {{base}}/projects/{{project}}/members` with body `{}` | `404` (not `422`) | Membership is checked before the body |
| T7.2.4 | Without cookies: `GET {{base}}/projects` | `401`, `"Sign in to continue."` | Sign-in required |

- **If it fails:** any difference between T7.2.1 and T7.2.2 tells an outsider which project ids exist.

### T7.3 Members and roles

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.3.1 | As admin: `POST {{base}}/projects/{{project}}/members` `{"email":"KOFI@example.org","role":"reviewer"}` | `201`, `{"user_id":"<Kofi>","display_name":"Kofi Mensah","role":"reviewer","added_at":…}`, no email | Add by email, case-insensitive (D7.7) |
| T7.3.2 | Same with `maya@example.org`, `"role":"viewer"` | `201` | |
| T7.3.3 | Same with `nobody@example.org` | `422`, `"No active account uses this email address."`; the email is not in the response | No echo |
| T7.3.4 | Same with `maya@example.org` again | `409`, `"This person is already a member of the project."` | |
| T7.3.5 | As Kofi: `GET {{base}}/projects/{{project}}` and `…/members` | `200`, `role: reviewer`; three members, each with `user_id`, `display_name`, `role` and `added_at` only | Members see each other, without emails |
| T7.3.6 | As Kofi: `PATCH {{base}}/projects/{{project}}` `{"name":"Renamed"}` | `403`, `"Your role in this project does not allow this."` | Role check (D7.1) |
| T7.3.7 | As Kofi: `POST {{base}}/projects/{{project}}/search` with the vocabulary body from [`api.md`](../api.md) | `200`, results and the compiled query | Reviewers may search (D7.8) |
| T7.3.8 | As admin: `PATCH {{base}}/projects/{{project}}/members/<Maya's id>` `{"role":"reviewer"}` | `200`, `role: reviewer` | Change role |

### T7.4 Recent sign-in and the last owner

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.4.1 | Wait 16 minutes after the admin's last sign-in or reauthentication (keep the session alive with any `GET`). As admin: `DELETE {{base}}/projects/{{project}}/members/<Maya's id>` | `401`, `"reauthentication_required": true`; Maya is still a member | Member changes need a recent sign-in |
| T7.4.2 | `POST {{base}}/auth/reauthenticate` `{"password":…}` (its cookie-capture script stores the new session), then repeat T7.4.1 | `204` with new cookies, then `204`; Maya is gone from `…/members` | |
| T7.4.3 | As admin: `PATCH {{base}}/projects/{{project}}/members/<admin's id>` `{"role":"viewer"}` | `409`, `"last_owner": true`, detail "A project needs at least one owner. …" | Last owner (D7.5) |
| T7.4.4 | As admin: `DELETE {{base}}/projects/{{project}}/members/<admin's id>` | `409`, `"last_owner": true` | The last owner cannot leave |
| T7.4.5 | As Kofi: `DELETE {{base}}/projects/{{project}}/members/<Kofi's id>` | `204`, even with an old sign-in; then `GET …/projects/{{project}}` as Kofi is `404` | Leaving needs no recent sign-in |

### T7.5 Archive

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.5.1 | As admin: `POST {{base}}/projects/{{project}}/archive` twice, a minute apart | `200` both times, the same `archived_at` | Idempotent |
| T7.5.2 | `GET {{base}}/projects`, then `GET {{base}}/projects?include_archived=true` | `[]`, then the project | Hidden by default |
| T7.5.3 | `POST {{base}}/projects/{{project}}/unarchive` | `200`, `archived_at: null` | |

### T7.6 Delete

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.6.1 | Copy the project's `name_sealed` from pgAdmin (T7.1.5) into a note: it stands for a backup | | |
| T7.6.2 | As admin, with a sign-in in the last 15 minutes: `DELETE {{base}}/projects/{{project}}` | `204` | |
| T7.6.3 | `GET {{base}}/projects/{{project}}` | `404`, the same body as T7.2.2 | A deleted project looks like it never existed |
| T7.6.4 | pgAdmin: `SELECT wrapped, destroyed_at FROM data_keys WHERE scope_id = '<project id>';` and `SELECT deleted_at FROM projects WHERE id = '<project id>';` and `SELECT count(*) FROM memberships WHERE project_id = '<project id>';` | `wrapped` null and `destroyed_at` set; `deleted_at` set; `0` | Shredded, tombstone kept, memberships gone (D7.6) |
| T7.6.5 | `uv run --env-file .env python -m app.cli verify-audit` | `audit chain intact: N event(s)` | History still verifies |
| T7.6.6 | pgAdmin: `SELECT sequence, action, actor_id, target_type, target_id, scope_id FROM audit_events WHERE scope_id = '<project id>' ORDER BY sequence;` | The project's history: `project.created`, `membership.added`…, `project.deleted`; only ids, no names or emails | Audit by id only |

- **If it fails:** a non-null `wrapped` after T7.6.2 means the key survived. The data would still be readable to anyone holding the KEK.

### T7.7 Signed-in compile and sources

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T7.7.1 | Without cookies: `POST {{base}}/vocabulary/compile` and `GET {{base}}/sources` | `401` both | Sign-in required (D7.8) |
| T7.7.2 | Signed in: the same two requests | `200` | No project needed |
| T7.7.3 | Signed in: `POST {{base}}/search` | `404` | The unscoped route is gone |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T7.1 Create and list | ☐ | |
| T7.2 Non-members see nothing | ☐ | |
| T7.3 Members and roles | ☐ | |
| T7.4 Recent sign-in and the last owner | ☐ | |
| T7.5 Archive | ☐ | |
| T7.6 Delete | ☐ | |
| T7.7 Compile and sources | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Every role × action pair is decided by one table, deny by default | `tests/test_projects_domain.py::TestPolicy` |
| A project always keeps an owner, whatever the sequence of changes | `tests/test_projects_domain.py::TestLastOwnerRule` |
| Names are trimmed and checked; the domain never holds one in the clear | `tests/test_projects_domain.py::TestProjectName`, `TestProject` |
| Create, list, read, rename, archive over HTTP | `tests/test_projects_api.py::TestCreateProject`, `TestListProjects`, `TestReadProject`, `TestRenameProject`, `TestArchiveProject` |
| Add, change, remove, leave; recent sign-in; no emails shown | `tests/test_projects_api.py::TestMembers` |
| The last owner over HTTP | `tests/test_projects_api.py::TestLastOwner` |
| Members without the role get `403` | `tests/test_projects_api.py::test_owner_only_actions_are_refused_to_other_members_with_403` |
| Delete shreds, a backup copy cannot be decrypted, the chain verifies | `tests/test_projects_api.py::TestDeleteProject` |
| Every action audited with ids only | `tests/test_projects_api.py::TestAuditTrail` |
| Compile and sources signed in, search project-scoped | `tests/test_projects_api.py::TestVocabularyAndSearch`, `tests/test_api.py` |
| Every route needs sign-in (except the allowlist) and CSRF on writes; every project route answers non-members with the missing-project `404` | `tests/test_route_inventory.py` |
| Storage behaves the same in memory and in Postgres; stale project copies refused | `tests/test_projects_storage_contract.py::TestProjects`, `TestMemberships` |
| Five owners racing never leave a project without an owner; the lock really waits | `tests/test_projects_storage_contract.py::TestLastOwnerUnderConcurrency`, `TestPostgresProjectLock` |

How to run only this phase's tests:

```powershell
uv run pytest tests/test_projects_domain.py tests/test_projects_api.py tests/test_route_inventory.py tests/test_projects_storage_contract.py -v
```

With Postgres, after migrating the test database ([S0.6](00-setup.md#s06-migrate-the-test-database)):

```powershell
$env:TEST_DATABASE_URL = '<the app-role URL of humani_t_test>'
uv run pytest tests/test_projects_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G7.1 | No recovery for a project whose every owner is disabled | Nobody can manage, share or delete it; its data stays encrypted but undeletable through the API | An audited operator command later; out of scope for M10 |
| G7.2 | Refused project actions (`403`, `404`) are not audited | Probing for project ids or repeated forbidden attempts leave no trace in the audit log | Revisit with the threat model (M14) |
| G7.3 | Owners can learn whether an email has an active account (`422` vs `201`) | Account enumeration by project owners, inside one organisation | Accepted (D7.7); project invitations would remove it |
| G7.4 | Deletion reaches backups only after their retention window | A backup taken before the delete, with the KEK, can still be read | Documented in the deployment guide (M14), as [D3.4](03-envelope-encryption.md#d34-crypto-shredding-and-its-limits) |
| G7.5 | No retention period or automatic deletion (UI §7.7 "new, needs API") | Projects live until an owner deletes them | Later, with a retention setting |
| G7.6 | Collections and tags (UI §7.3) not built | Projects can only be archived, not grouped | With the web UI work |

## Glossary

| Term | Meaning |
| --- | --- |
| **Project** | The unit of access, encryption and deletion. Everything a team does lives inside one. |
| **Membership** | One user's role in one project: owner, reviewer or viewer. |
| **Owner** | Can do everything in the project, including managing members and deleting it. A project always has at least one. |
| **Reviewer** | Can read, search, run, confirm gates, relabel and ask; cannot manage members or delete. |
| **Viewer** | Can read and leave; nothing else. |
| **Instance administrator** | Manages accounts for the whole deployment. Has no power inside a project they do not belong to. |
| **Gate (`require`)** | The check in front of every project route: sign-in, membership, role, recent sign-in, in that order, before the body is read. |
| **Tombstone** | The row kept for a deleted project: its id and dates, with a name nobody can decrypt any more. |
| **Crypto-shredding** | Deleting data by destroying the key it is encrypted with, so every copy becomes unreadable at once. |
| **Row lock (`FOR UPDATE`)** | A Postgres lock on one row, held until the transaction ends; here, the point where membership changes on a project wait their turn. |
