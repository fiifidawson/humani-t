# ADR-0002: Membership changes serialise on the project's row lock

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

A project must always have at least one owner (UI_UX_SPEC §6.14). The rule spans several rows: it is about how many `memberships` rows of a project have `role = 'owner'`. Checking it and then writing is a classic check-then-act race. Two owners of a two-owner project who step down at the same moment each read "two owners", each decide their own change is safe, and both commit. The project is left with no owner and nobody can manage or delete it.

The usual tools in this codebase do not cover it on their own:

- **Conditional updates** (`claim`, `revoke`) protect one row. Here each request changes a different row.
- **Optimistic locking** (`users.version`) also protects one row. A version on each membership would not notice a change to a different membership.
- **Postgres `SERIALIZABLE` isolation** would catch it, but it makes every transaction that touches memberships retryable, and the rest of the application runs at `READ COMMITTED`.

## Decision

Every use case that changes a project's members begins by locking the project's row with `SELECT … FOR UPDATE` (`ProjectRepository.lock`). Then, inside the same transaction, it reads the memberships, applies the domain rule `require_an_owner_remains`, writes, and appends the audit event last.

This covers adding a member, changing a role, removing a member, leaving and deleting the project.

Under `READ COMMITTED`, a second transaction waits on the lock until the first commits. Its next statement then sees the committed memberships, so it counts owners correctly. It is the same pattern as the audit log's chain head (`AuditRepository.lock_head`). The order of locks is always the same: project row first, then the audit chain head. That rules out a deadlock between the two.

The in-memory storage has nothing to lock. Its transactions never interleave, so its `lock` is `get`.

## Consequences

**Easier**

- One rule, easy to explain: "membership changes on a project happen one at a time".
- No retry loops and no change of isolation level.
- The loser of a race gets a meaningful answer:
  - `409` with `last_owner` when the change would now leave no owner;
  - `404` when the loser was itself removed by the change that won.

**Harder**

- Membership changes on one project are serialised. That is irrelevant at human scale. A bulk import of many members would need batching.
- A long transaction holding the lock would block other owners of that project. Use cases keep it short, and nothing slow (hashing, email, HTTP) happens while it is held (architecture §5.7).
- The guarantee depends on every membership write going through a use case that takes the lock first. A new writer that skips `lock` would reopen the race. The storage contract (`tests/test_projects_storage_contract.py::TestLastOwnerUnderConcurrency`) runs real concurrent requests against Postgres to catch that.

## Alternatives considered

- **Version-check the project on every membership change** (bump `projects.version`). Correct, but the loser gets a generic `409 changed concurrently` even when retrying would succeed. It also turns a membership change into a project write, which audit and caches would then misread as "project changed".
- **A database constraint or trigger counting owners.** It would enforce the rule for every writer. But a deferred trigger that counts rows is subtle, and it is harder to read and test than one domain function. It may be worth adding later as defence in depth.
- **`SERIALIZABLE` isolation for these transactions.** Correct, but callers would have to retry serialisation failures. It is also easy to forget for one transaction.
