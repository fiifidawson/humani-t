# 18 — P4 retention: the period, "Deleted in N days unless kept", Keep

> **Milestones:** P4 (UI_UX_SPEC §15) · **Status:** in progress · **Previous:** [17 — P3 find](17-p3-find.md) · **Next:** [18 — P4 coverage](18-p4-coverage.md)

An operator can have projects deleted for age, and nobody is surprised by it: owners are warned in Needs you for the last 14 days, every member sees the date in Project settings, and one click on **Keep** restarts the clock ([UI_UX_SPEC §7.7](../UI_UX_SPEC.md#77-archive-delete-retention), [ADR-0020](../adr/0020-retention-counts-from-last-activity.md)). A deletion for age is exactly an owner's deletion: the project's key is destroyed.

## What this phase delivers

- **A deployment setting**, `HUMANI_T_RETENTION_DAYS` (30 to 3650), empty by default: then nothing is deleted for age.
- **A date per project:** last activity (the project list's `last_activity_at`, follow-up questions included), a member's last read (opening a paper or a brief, recorded at most once a day) or the latest Keep, whichever is latest, plus the period. Worked out on every read.
- **Needs you:** a `retention` item for owners within 14 days of the date, archived projects included, saying "Deleted in N days unless kept", with **Keep** as its action.
- **Project settings → Retention:** the deployment's sentence, the date, "Kept on …", and **Keep project** for owners.
- **Account → Data retention**, for administrators: the deployment's sentence.
- **Deletion when due:** the worker sweeps hourly and deletes through the same `shred` as `DELETE /projects/{id}`.
- Endpoints: `GET /api/v1/retention`, `GET /api/v1/projects/{project_id}/retention`, `POST /api/v1/projects/{project_id}/retention/keep` ([api.md](../api.md#retention)).

### Data model

| Table | Change | Migration |
| --- | --- | --- |
| `projects` | `kept_at timestamptz null`: an owner's latest Keep | `20261121_0021_retention_keep.py` |

## Architecture choices and why

### D18R.1 The clock counts from last activity

- **Decision:** the date is `max(last_activity_at, kept_at) + period`, using the same `last_activity_at` the project list sorts by.
- **Why:** a project in use is not deleted on a fixed date, and the list and the date cannot disagree.
- **Rejected:** counting from creation (deletes projects in daily use); a stored date (a second copy every use case must move).
- **Costs:** opening a paper or a brief writes `projects.read_at`, at most once a day per project.
- **Where:** [`app/domain/retention.py`](../../services/api/app/domain/retention.py), [`app/application/retention.py`](../../services/api/app/application/retention.py).
- **Proven by:** `tests/test_retention_domain.py::TestRetentionPolicy`, `tests/test_retention_api.py::TestAProjectsDate`.

### D18R.2 One deletion path, ordered by the row lock

- **Decision:** the sweep finds due projects without locks, then per project takes the row lock, works the date out again, and calls `shred`. Keep takes the same lock. The audit log gets `project.retention_expired` then `project.deleted`, both with no actor.
- **Why:** a retention deletion leaves nothing an owner's deletion would remove, and a Keep at the same moment is never lost.
- **Rejected:** a separate retention delete (would drift); a reason in audit details (sealed under the key being destroyed).
- **Where:** `shred` in [`app/application/projects.py`](../../services/api/app/application/projects.py), the sweep in [`app/worker.py`](../../services/api/app/worker.py).
- **Proven by:** `tests/test_retention_api.py::TestDeletionWhenDue`.

## Files to read, in order

1. [`app/domain/retention.py`](../../services/api/app/domain/retention.py) — the period, the date, days left, the 14-day window.
2. [`app/application/retention.py`](../../services/api/app/application/retention.py) — Keep, `due_projects`, `delete_if_due` under the lock.
3. [`app/application/history.py`](../../services/api/app/application/history.py) — `_retention_notice`, the new Needs you kind.
4. [`apps/web/src/features/retention/`](../../apps/web/src/features/retention/) — the settings section, the account note, the words.

## Set up

Set the period in `services/api/.env` and restart the API and the worker:

```powershell
Add-Content services\api\.env "HUMANI_T_RETENTION_DAYS=30"
```

## Manual tests

Sign in as an owner of a project with no review yet. The date moves only with time, so tests T18R.3 and T18R.5 move a project's dates back in your database client (the dev database `humani_t`).

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T18R.1 | Open Project settings | **Retention**: "This deployment deletes a project 30 days after its last activity, unless an owner keeps it." and a date 30 days from creation | D18R.1 |
| T18R.2 | As a viewer of the same project, open Project settings | The same text and "Only owners can keep the project.", no button | access |
| T18R.3 | In the DB client: `UPDATE projects SET created_at = now() - interval '20 days' WHERE id = '<id>';` then open Home | "Needs you" lists the project with "Deleted in 10 days unless kept" and **Keep** | D18R.1 |
| T18R.4 | Click **Keep** | The row leaves; Project settings shows "Kept on" today and a date 30 days away; `audit_events` has `project.kept` | D18R.2 |
| T18R.5 | `UPDATE projects SET created_at = now() - interval '40 days', kept_at = NULL WHERE id = '<id>';` then restart the worker (it sweeps on start) | The project is gone for every member; `audit_events` ends with `project.retention_expired`, `project.deleted` | D18R.2 |
| T18R.6 | In Postman, `POST /api/v1/projects/<id>/retention/keep` as a reviewer, then as a non-member | `403`, then `404` | access |
| T18R.7 | Remove the setting, restart, `GET /api/v1/retention` | `{"period_days": null, "warning_days": 14}`; Project settings shows no Retention section | off by default |

```powershell
cd services\api
uv run pytest tests/test_retention_api.py tests/test_retention_domain.py -v
```

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T18R.1–T18R.7 | ☐ | |

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Off by default; under 30 days refused | `tests/test_retention_api.py::TestTheSetting` |
| The date counts from last activity, and Keep restarts it | `tests/test_retention_api.py::TestAProjectsDate`, `TestKeep` |
| Owners see the real number in Needs you, archived too | `tests/test_retention_api.py::TestTheNotice` |
| Deletion is a normal deletion, safe twice, and loses to a Keep | `tests/test_retention_api.py::TestDeletionWhenDue` |
| The screens | `apps/web/src/features/retention/Retention.test.tsx` |

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G18R.1 | Closed: reading and follow-up questions count as activity (owner's decision, 2026-11-21) | – | – |
| G18R.2 | No email warning | An owner who never opens Home sees no notice | With notifications (§16 Q8) |
| G18R.3 | The warning window is fixed at 14 days | Not an operator choice | If asked for |

## Glossary

| Term | Meaning |
| --- | --- |
| **Retention period** | Days after a project's last activity until the deployment deletes it. |
| **Keep** | An owner's action that restarts the clock from now. |
| **Sweep** | The worker's hourly check for projects past their date. |
