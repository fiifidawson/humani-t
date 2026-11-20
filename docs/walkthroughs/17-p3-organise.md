# 17 — P3: organise projects (collections, tags, pins)

> **Milestones:** P3 (UI_UX_SPEC §15) · **Status:** in progress · **Previous:** [16 — Documents](16-documents.md) · **Next:** [17 — P3 history](17-p3-history.md)

People can now group and find their projects: file each in one shared **collection**, give it any number of **tags**, **pin** it to Home, and sort, filter and group the All projects page, with bulk actions in its list view ([UI_UX_SPEC](../UI_UX_SPEC.md) §7.1 to §7.3, [ADR-0016](../adr/0016-collections-are-shared-labels.md)).

## What this phase delivers

- Any signed-in user creates a collection; its creator or an administrator renames or deletes it. Every collection name is visible to everyone; counts cover only the caller's own projects.
- A project owner files their project in at most one collection, and sets its tags. Any member pins it for themselves.
- `GET /api/v1/projects` filters (status, tags, owner, member, created dates, collection, archived) and sorts (last activity, name, created, most relevant papers), and returns what grouping needs.
- `POST /api/v1/project-actions` moves, tags or archives several projects and reports what it skipped.
- Web: All projects with cards and list views, Group by, Sort, filter chips in the URL, bulk actions; Home's Pinned and Collections; collections in the rail; the project's ⋯ menu; "Collection and tags" in project settings.
- Endpoints: see [`api.md` — Collections, tags and pins](../api.md#collections-tags-and-pins).

### Data model

| Table | Holds | Sealed |
| --- | --- | --- |
| `collections` | id, name, creator, created, version | name, under the collection's own key |
| `project_collections` | project, collection, who moved it, when | nothing: ids only; cascades with the collection |
| `project_tags` | project, tag list, who, when | the list, under the project's key |
| `project_pins` | user, project, when | nothing: per-user ids |

Migration `20261119_0018_collections.py`.

## Architecture choices and why

### D17.1 Collections are shared labels

- **Decision:** ADR-0016: no access rules of their own, names sealed under each collection's own key, unique ignoring case and spacing, checked under a table lock.
- **Why:** UI_UX_SPEC §16 Q1 assumes a label; a project stays the unit of access.
- **Rejected:** collections with members; names in the clear; per-user collections.
- **Costs:** every signed-in person can read every collection name.
- **Where:** [`app/application/collections.py`](../../services/api/app/application/collections.py), [`app/domain/collections.py`](../../services/api/app/domain/collections.py).
- **Proven by:** `tests/test_collections_api.py::TestCounts`, `::TestRenamingAndDeleting`.

### D17.2 A mixed bulk selection is reported, not refused

- **Decision:** each project is its own transaction; owned ones change, others are listed as `not_found` (also for non-members) or `not_allowed`.
- **Rejected:** all-or-nothing, which would tell the caller nothing useful and still need the per-project check.
- **Proven by:** `tests/test_collections_api.py::TestBulkActions`.

## Libraries

None added.

## Files to read, in order

1. [`app/domain/collections.py`](../../services/api/app/domain/collections.py) and [`app/domain/project_list.py`](../../services/api/app/domain/project_list.py) — the rules.
2. [`app/application/collections.py`](../../services/api/app/application/collections.py) and [`app/application/project_list.py`](../../services/api/app/application/project_list.py) — the use cases.
3. [`apps/web/src/features/projects/ProjectsPage.tsx`](../../apps/web/src/features/projects/ProjectsPage.tsx) — the page.

## Set up

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
```

## Manual tests

Sign in as two people, A and B, in two browsers. A owns project "Cash vs vouchers, Gao"; B owns "Market assessment, Mopti" and has added A as a viewer.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T17.1 | As A, open Gao, ⋯ → Move to collection…, create "Sahel 2026", Move | The header shows "Sahel 2026"; Home shows "Sahel 2026 (1)" | D17.1 | Check `PUT …/collection` in the network tab |
| T17.2 | As B, move Mopti into "Sahel 2026" | B sees "Sahel 2026 (1)": Mopti only, never A's Gao; A, a viewer of Mopti, now sees "(2)" | D17.1 | A count must never include a project the person is not in |
| T17.3 | As B, try to create "sahel  2026" | "A collection with this name already exists." | D17.1 | |
| T17.4 | As A, in Gao's settings, set tags "Mali, cash"; open All projects, Group by Tag | Gao shows under "cash" and "Mali", each marked "Also in …" | §7.2 | |
| T17.5 | As A, All projects → Status "Failed", then copy the URL into a new tab | The same filter chip and list | §7.2 | |
| T17.6 | As A, list view (☰): select Gao and Mopti is not selectable | Only owned projects have a checkbox | §3.3 | |
| T17.7 | As A, pin Gao from its card; open Home | Gao is under "Pinned"; B's Home shows no pin | pins per user | |
| T17.8 | In the rail, unfold "Sahel 2026", reload | It stays unfolded | §7.3 | Check localStorage `humani-t:open-collections` |
| T17.9 | In a database client, `SELECT name_sealed FROM collections;` and `SELECT tags_sealed FROM project_tags;` | Ciphertext only, no "Sahel" or "Mali" | ADR-0016 | |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T17.1 | ☐ | |
| T17.2 | ☐ | |
| T17.3 | ☐ | |
| T17.4 | ☐ | |
| T17.5 | ☐ | |
| T17.6 | ☐ | |
| T17.7 | ☐ | |
| T17.8 | ☐ | |
| T17.9 | ☐ | |

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Names, tags, filters and sorts | `tests/test_collections_domain.py` |
| Routes, access, counts, sealing, audit, bulk | `tests/test_collections_api.py` |
| Memory and Postgres behave alike | `tests/test_collections_storage_contract.py` |
| The pages | `apps/web/src/features/projects/ProjectsPage.test.tsx`, `apps/web/src/features/collections/Organise.test.tsx` |

```powershell
uv run pytest tests/test_collections_domain.py tests/test_collections_api.py tests/test_collections_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G17.1 | No drag-and-drop onto a rail collection | Moving is through ⋯ → Move to collection…, settings, or the list's bulk action | Add if asked; keyboard path already exists |
| G17.2 | No "⌕ Filter…" text box on All projects | Finding a project by name uses Ctrl K search (find lane) | Covered by `GET /search` |
| G17.3 | `last_activity_at` reads the newest review, run and brief only | Questions and documents do not move a project up | The history lane's activity can feed it |

## Glossary

| Term | Meaning |
| --- | --- |
| **Collection** | A shared label for grouping projects, one level deep; a project is in at most one. |
| **Tag** | A project's own free label; many per project, seen only by its members. |
| **Pin** | One person's bookmark that puts a project on their Home. |
