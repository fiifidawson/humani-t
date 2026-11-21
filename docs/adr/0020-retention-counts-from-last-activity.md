# ADR-0020: Retention counts from last activity, and deletes through the normal delete

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

P4 adds a retention period (UI_UX_SPEC §7.7): the operator may have projects deleted for age, owners see "Deleted in N days unless kept" in Needs you, and an owner can keep a project. Deleting is irreversible (crypto-shredding), so three choices are hard to undo: what the clock counts from, how a deletion for age relates to an owner's deletion, and how a Keep and a deletion that happen at once are ordered.

## Decision

- **Off by default.** `HUMANI_T_RETENTION_DAYS` is empty unless the operator sets it (30 to 3650 days). Without it nothing is deleted for age and nothing reads the clock. It reaches the application as a frozen `RetentionPolicy`.
- **The clock counts from the project's last activity, a member's last read, or an owner's Keep, whichever is latest.** Last activity is the value the project list already sorts by (`last_activity_at` in `application/project_list.py`): created, archived, its newest review, and that review's latest run, brief and follow-up question. One definition serves both, so the list and the date cannot disagree. Opening a paper or a brief is a read: it is stored as `projects.read_at`, at most once a day, so reading is not a write on every click, and it moves only the retention date, not the list's sort. The date is worked out on every read; only the Keep and the last read are stored (`projects.kept_at`, `projects.read_at`).
- **Owners are warned for the last 14 days**, in Needs you, with the real number of days. Only owners see the notice, because only they can keep; every member sees the date in Project settings. Archived projects are announced too, since archiving does not stop the clock.
- **A deletion for age is an ordinary deletion.** The worker sweeps hourly and calls the same `shred` as `DELETE /projects/{id}`: the key is destroyed, memberships, pins, placements and tags go, the row stays as a tombstone, and `project.deleted` is audited. Just before it, in the same transaction, it records `project.retention_expired`. Both have no actor (the system) and ids only. The reason has its own event because audit details are sealed under the project's key, which the deletion destroys.
- **A Keep and a deletion serialise on the project's row lock.** The sweep finds due projects without locks, then for each one takes the lock and works the date out again before deleting. A Keep takes the same lock. Whichever comes second sees the first: a Keep after a deletion gets 404; a deletion after a Keep finds the date moved and spares the project. The version check on `projects.save` stays as a second guard. Running the sweep twice, or in two workers, deletes once.

## Consequences

- A project people still read or question is never deleted for age: the owner decided that reading and follow-up questions count (2026-11-21). The cost is a small write when someone opens a paper or a brief, at most once a day per project, which `save` never touches, so a stale copy cannot set a read back.
- A deleted project stays in backups until the backup retention window passes (`docs/deploy/self-hosting.md` §6). The period is not a promise that data is gone from every copy on that day.
- Each Needs you read and each sweep reads a few rows per project. That is fine at a team's scale.

## Alternatives rejected

- **Counting from creation:** a project in daily use would be deleted on a fixed date unless someone remembered to keep it.
- **A stored deletion date:** a second record of a fact derived from others, which every use case that adds activity would have to remember to move.
- **A separate deletion path for retention:** two ways to delete would drift, and a retention deletion could leave behind what an owner's deletion removes.
