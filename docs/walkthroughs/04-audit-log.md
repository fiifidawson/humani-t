# 04 — Audit log

> **Milestones:** M7 · **Status:** done · **Previous:** [03 — Envelope encryption](03-envelope-encryption.md) · **Next:** [05 — Accounts and sessions](05-accounts-and-sessions.md)

This phase gave humani-t a record of who did what that cannot be quietly changed. Every event stores the hash of the event before it, so editing, deleting or reordering any event breaks every hash after it, and `python -m app.cli verify-audit` reports exactly where. Appends are serialised through a single locked row so concurrent writers cannot fork the chain. The app's database role cannot update or delete events, and a trigger refuses those operations for every role. Event rows hold identifiers only; anything descriptive is encrypted with the project's key, so a shredded project's history stays verifiable without staying readable. It implements brief rule 16 and [`architecture.md` §5.5](../architecture.md#55-authorisation-and-audit).

## What this phase delivers

- **Domain rules** (`app/domain/audit.py`): `AuditEntry` (what happened, identifiers only), `AuditEvent` (an entry placed in the chain), `ChainHead`, `chain_hash`, and `verify_chain`, which returns a `ChainReport` with the first broken sequence and the reason.
- **`AuditLog`** (`app/application/audit.py`): `record` (append one event inside the caller's unit of work, encrypting `details` under the scope's key), `read_details`, `verify`.
- **Storage:** `AuditRepository` (`lock_head`, `append`, `head`, `list_events`) in memory and Postgres.
- **Migration `0002`**: `audit_events`, `audit_chain_head` (seeded with the genesis head), the `audit_events_append_only` trigger, and the grants revoked from `humani_t_app`.
- **Operator command:** `python -m app.cli verify-audit` (exit `0` if intact, `1` if broken).
- **Events recorded** by phases 03 and 05 (D4.8).

### Data model

| Table | Columns | Purpose and rules |
| --- | --- | --- |
| `audit_events` | `sequence` (primary key, starts at 1), `id`, `occurred_at`, `action`, `actor_id`, `target_type`, `target_id`, `scope_id`, `request_id`, `details_sealed`, `previous_hash`, `hash` | Append-only (grants and trigger). Identifiers only; details encrypted per scope. `hash` = SHA-256(`previous_hash` ‖ canonical content). |
| `audit_chain_head` | `id`, `sequence`, `hash` | Exactly one row (check constraint `single_row`: `id = 1`), seeded with sequence `0` and 32 zero bytes; locked on each append. |

**Who can do what:** `humani_t_app` may `SELECT` and `INSERT` events but not `UPDATE`, `DELETE` or `TRUNCATE` them; it may `SELECT` and `UPDATE` the chain head (to move it) but not `INSERT`, `DELETE` or `TRUNCATE` it. The trigger refuses `UPDATE`, `DELETE` and `TRUNCATE` on `audit_events` for **every** role, the schema owner and superusers included, unless a superuser disables the trigger first.

## Architecture choices and why

### D4.1 A hash chain

- **Decision:** each event's `hash` is `SHA-256(previous_hash + canonical(entry, sequence))`. The first event chains onto the genesis hash (32 zero bytes) and has sequence 1; each later one onto its predecessor. `verify_chain` walks the events in order and checks, for each, that the sequence is the next one, that `previous_hash` equals the previous event's `hash`, and that `hash` matches the recomputed content. Given the stored head, it also checks the last event *is* the head, which catches events deleted from the end (those leave every remaining link intact).
- **Why:** brief rule 16. A chain proves tampering happened; it cannot prevent it — that is the grants' and the trigger's job (D4.5). Editing one event breaks that event's hash; recomputing its hash to hide the edit breaks the next event's link; and so on to the end.
- **Rejected:** an unchained log (an edit is invisible); a Merkle tree or an external ledger (more machinery than a single writer needs today); per-event signatures (a key in the app process can sign anything a compromised process wants).
- **Costs:** appends are serialised (D4.3); verification reads the whole log.
- **Proven by:** `tests/test_audit.py::TestVerifyChain` — untouched, edited, edited-with-recomputed-hash, deleted, reordered, and deleted-from-the-end chains.

### D4.2 Canonical content

- **Decision:** the content hashed is JSON with sorted keys and fixed separators: `sequence`, `id`, `occurred_at` (converted to UTC, ISO 8601), `action`, `actor_id`, `target_type`, `target_id`, `scope_id`, `request_id`, and `details_sealed` as hex — every column except the hashes themselves.
- **Why:** the same event must always hash the same, whichever process wrote it and whichever database returned it (a timestamp read back from Postgres in another time zone, keys in another order). Including `sequence` means an event cannot be moved to another position and still verify.
- **Proven by:** `TestAuditEntry::test_the_same_entry_always_hashes_the_same`.

### D4.3 Appends serialised by a locked chain head

- **Decision:** `record` calls `lock_head()` — in Postgres a `SELECT … FROM audit_chain_head WHERE id = 1 FOR UPDATE` — then appends the new event and moves the head with `UPDATE audit_chain_head … WHERE id = 1 AND sequence = <new - 1> AND hash = <previous_hash>`. If that update changes no row, the append is refused (`ValueError: audit event does not extend the current chain head`).
- **Why:** without the lock, two concurrent writers could both chain onto the same event and fork the log. The conditional head update is a second guard: an append made without the lock fails loudly instead of forking silently.
- **Rejected:** a database sequence for `sequence` alone (gives order, not a single chain); table-level locks (block readers too).
- **Costs:** every audited write takes the same row lock, so the audit event is written **last** in its transaction to hold the lock as briefly as possible, and in the same position everywhere so lock order is consistent and deadlocks are impossible (rule 3 of [`architecture.md` §5.7](../architecture.md#57-concurrency-and-resources)).
- **Proven by:** `tests/test_storage_contract.py::TestPostgresAuditGuarantees::test_concurrent_writers_never_fork_the_chain` (50 concurrent appends against real Postgres, then a full verification), `TestAuditLog::test_an_append_that_does_not_extend_the_head_is_refused`, `tests/test_audit.py::TestAuditLog::test_an_append_that_skips_the_head_is_refused`.

### D4.4 Written inside the action's own transaction

- **Decision:** `AuditLog.record(uow, …)` writes into the caller's unit of work. The action and its audit record commit or roll back together.
- **Why:** there is never a deletion without its record, nor a record of a deletion that did not happen.
- **Rejected:** writing audit events in a separate transaction or a background queue (either can disagree with what actually happened).
- **Proven by:** `tests/test_audit.py::TestAuditLog::test_an_event_rolls_back_with_its_action`.

### D4.5 Two layers stop rewriting, and the chain catches the rest

- **Decision:** migration `0002` revokes `UPDATE, DELETE, TRUNCATE` on `audit_events` (and `DELETE, TRUNCATE, INSERT` on `audit_chain_head`) from `humani_t_app`, and installs a statement-level `BEFORE UPDATE OR DELETE OR TRUNCATE` trigger whose function raises `audit_events is append-only (<operation> refused)`. The `REVOKE` is conditional on the role existing, so the migration also runs against a database set up without the role script; the trigger applies regardless.
- **Why:** the `REVOKE` means a compromised app process cannot even ask; the trigger stops every role, including the schema owner, from editing history by accident. A superuser can still disable the trigger — and the hash chain is what makes that detectable afterwards.
- **Limit:** a superuser with full database access could rewrite the **entire** chain consistently (recompute every hash and the head). The planned fix is anchoring the chain head outside the database: the worker periodically writes an HMAC-signed head to the logs (gap G10).
- **Proven by:** `TestPostgresAuditGuarantees::test_the_app_role_cannot_rewrite_history`; [T4.4](#t44-nobody-can-rewrite-history), [T4.6](#t46-watch-tampering-being-detected).

### D4.6 Identifiers in rows; details encrypted per project

- **Decision:** an `AuditEntry` holds identifiers only: `action` (`noun.verb`, matching `^[a-z][a-z_]*\.[a-z][a-z_]*$`), `actor_id` (`None` for the system — the CLI, a worker job), `target_type` and `target_id` (never blank), `scope_id`, `request_id`, a timezone-aware `occurred_at`. Anything descriptive goes in `details`, which `record` encrypts with `ScopeEncryption` under the event's `scope_id` (purpose `audit_events.details`, record id = the event id) into `details_sealed`. Details without a scope are refused.
- **Why:** an audit log is kept for a long time and read by many people; personal data in it would outlive every other deletion. Encrypting details per project means a shredded project's history stays **verifiable** (the chain hashes the sealed bytes) without staying **readable** (`read_details` raises `KeyDestroyedError`).
- **Costs:** reading details needs the project's key; events about accounts carry no details at all today, so who-did-what is fully answered by identifiers and the request id.
- **Proven by:** `tests/test_audit.py::TestAuditEntry`, `TestAuditLog::test_details_are_encrypted_and_readable_by_the_project`, `test_a_shredded_project_keeps_a_verifiable_but_unreadable_history`, `test_details_without_a_scope_are_refused`, `tests/test_auth_api.py::TestAuditTrail::test_audit_records_never_contain_email_addresses_or_passwords`.

### D4.7 Verification is an operator command

- **Decision:** `python -m app.cli verify-audit` reads every event and the head in one unit of work and prints `audit chain intact: N event(s)` (exit `0`), or `AUDIT CHAIN BROKEN at sequence <n>: <reason>` on standard error (exit `1`). The reasons are `sequence gap or reordering`, `link to the previous event is broken`, `event content was altered`, and `events missing from the end`.
- **Why:** a non-zero exit code is what a scheduler or monitoring system can act on. The worker (M4) will run it on a schedule. It runs inside the deployment, by someone with shell access ([D3.6](03-envelope-encryption.md#d36-an-operator-command-line-not-endpoints)).
- **Proven by:** `tests/test_cli.py::test_verify_audit_reports_an_intact_chain`, `test_verify_audit_fails_loudly_on_tampering`.

### D4.8 What is recorded today

| Action | Actor | Target | When |
| --- | --- | --- | --- |
| `account.created` | system | `user`, id | `create-admin` |
| `account.invited` | admin | `invitation`, id | `POST /admin/invitations` |
| `account.registered` | the new user | `user`, id | `POST /auth/register` |
| `account.disabled` | admin | `user`, id | `POST /admin/users/{id}/disable` (no request id is recorded for this one) |
| `account.password_changed` | the user | `user`, id | `POST /account/password` |
| `auth.login` | the user | `session`, id | successful sign-in |
| `auth.login_failed` | none | `account`, user id or `unknown` | failed sign-in (unknown accounts never record the typed email) |
| `auth.logout` | the user | `session`, id | `POST /auth/logout` |
| `auth.reauthenticated` / `auth.reauthentication_failed` | the user | `session`, id | `POST /auth/reauthenticate` |
| `auth.session_revoked` | the user | `session`, id | `DELETE /account/sessions/{id}` |
| `auth.other_sessions_revoked` | the user | `user`, id | `POST /account/sessions/revoke-others` |
| `auth.password_reset_requested` | none | `account`, user id or `unknown` | `POST /auth/password-reset` |
| `auth.password_reset_completed` | the user | `user`, id | `POST /auth/password-reset/confirm` |
| `keys.rewrapped` | system | `kek`, current version | `rewrap-keys` |

Every request-driven event also stores the request id (except `account.disabled`), so an event can be matched to the API's log lines.

## Libraries

No new libraries. The chain uses the standard library's `hashlib` (SHA-256) and `json`; the protections use PostgreSQL grants, a PL/pgSQL trigger function and `SELECT … FOR UPDATE`.

- **Why no ledger product:** an immutable-database product (immudb, a blockchain, a cloud ledger) would add a service to run and secure, or an outbound dependency the operator did not choose, for a guarantee a hash chain plus external anchoring (G10) already gives a single-writer system.
- **What we gave up:** third-party verifiability without trusting the operator; that becomes possible once the head is anchored outside the database.

## Files to read, in order

From the rule, to the writer, to the database, to the operator:

1. [`app/domain/audit.py`](../../services/api/app/domain/audit.py) — `AuditEntry.__post_init__`, `canonical`, `chain_hash`, `GENESIS`, `verify_chain` and its four reasons.
2. [`app/application/audit.py`](../../services/api/app/application/audit.py) — `Actor` and `SYSTEM`, `record` (encrypt details, lock the head, chain, append), `read_details`, `verify`.
3. [`app/storage/base.py`](../../services/api/app/storage/base.py), `AuditRepository` — the contract `lock_head` promises.
4. [`app/storage/postgres/storage.py`](../../services/api/app/storage/postgres/storage.py), `PostgresAuditRepository` — `with_for_update()` and the conditional head update.
5. [`migrations/versions/20261024_0002_audit_log.py`](../../services/api/migrations/versions/20261024_0002_audit_log.py) — the genesis row, the trigger, the conditional `REVOKE`, and the comment explaining the two layers.
6. [`app/cli.py`](../../services/api/app/cli.py), `_verify`.
7. [`app/application/accounts.py`](../../services/api/app/application/accounts.py) — search for `self._audit.record`: each one is the last write before `commit()`.
8. [`tests/test_audit.py`](../../services/api/tests/test_audit.py) — the specification.

## Set up

[00 — Setup](00-setup.md) through S0.12. T4.2 and T4.3 are more interesting after you have worked through [05](05-accounts-and-sessions.md)'s tests, which fill the log; they work at any time. Run commands from `services\api`.

## Manual tests

### T4.1 Watch the chain detect edits and truncation (in memory)

Runs the real `AuditLog` against in-memory storage; touches no database.

- **Do:**

```powershell
@'
import asyncio, dataclasses
from app.application.audit import SYSTEM, AuditLog
from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.storage.memory import MemoryStorage

async def main():
    storage = MemoryStorage()
    audit = AuditLog(ScopeEncryption(LocalKeyProvider.from_config(f"1:{generate_kek()}"), AesGcmCipher()))
    async with storage.unit_of_work() as uow:
        for n in range(3):
            await audit.record(uow, SYSTEM, "demo.recorded", "demo", str(n))
        await uow.commit()
    async with storage.unit_of_work() as uow:
        print("intact:", await audit.verify(uow))
    events = storage.state.audit_events
    for e in events:
        print(e.sequence, e.previous_hash.hex()[:12], "->", e.hash.hex()[:12], e.entry.action)
    second = events[1]
    events[1] = dataclasses.replace(second, entry=dataclasses.replace(second.entry, action="demo.innocent"))
    async with storage.unit_of_work() as uow:
        print("edited:", await audit.verify(uow))
    events[1] = second
    del events[2]
    async with storage.unit_of_work() as uow:
        print("last one deleted:", await audit.verify(uow))

asyncio.run(main())
'@ | uv run python -
```

- **Expect** (hashes differ on every run, because ids and timestamps do):

```text
intact: ChainReport(events_checked=3, first_broken_sequence=None, reason=None)
1 000000000000 -> 88ca3e755ffd demo.recorded
2 88ca3e755ffd -> ba14fc61979e demo.recorded
3 ba14fc61979e -> 8fb6652f4eb6 demo.recorded
edited: ChainReport(events_checked=2, first_broken_sequence=2, reason='event content was altered')
last one deleted: ChainReport(events_checked=2, first_broken_sequence=3, reason='events missing from the end')
```

  Each event's left-hand hash is the previous event's right-hand hash; the first chains onto zeros.
- **Proves:** D4.1 and D4.7: an edit is caught at the edited event, and deleting the newest event is caught by comparing with the stored head.
- **If it fails:** `edited` reported intact means the hash no longer covers the content; `last one deleted` reported intact means verification ignores the head.

### T4.2 The audit trail of your own actions

- **Do:** in pgAdmin (any role):

```sql
SELECT sequence, occurred_at, action, target_type, target_id, actor_id, request_id
FROM audit_events ORDER BY sequence DESC LIMIT 30;
```

- **Expect:** a row for each thing you did, with the actions in D4.8: `auth.login_failed`, `auth.login`, `auth.logout`, `account.invited`, `account.registered`, `account.disabled`, `auth.session_revoked`, `account.password_changed`, `auth.password_reset_requested`, `auth.password_reset_completed`… Failed sign-ins and reset requests for unknown accounts show `target_id = unknown`. `request_id` matches the `x-request-id` of the response you saw. **No email addresses anywhere**, and `details_sealed` is empty for account events.
- **Proves:** D4.6 and D4.8.
- **If it fails:** an email address in any column is a privacy defect; a missing action means a use case skipped `record`.

### T4.3 The links are really there

- **Do:** in pgAdmin:

```sql
SELECT encode(previous_hash, 'hex') FROM audit_events WHERE sequence = 1;

SELECT count(*) AS broken_links
FROM (SELECT previous_hash, lag(hash) OVER (ORDER BY sequence) AS prev FROM audit_events) t
WHERE prev IS NOT NULL AND previous_hash <> prev;

SELECT h.sequence = (SELECT max(sequence) FROM audit_events) AS head_is_last,
       h.hash = (SELECT hash FROM audit_events ORDER BY sequence DESC LIMIT 1) AS head_hash_matches
FROM audit_chain_head h;
```

- **Expect:** 64 zeros; `broken_links` = `0`; `head_is_last` and `head_hash_matches` both `true` (on an empty log the third query returns `null`s).
- **Proves:** D4.1 and D4.3, read straight from the tables rather than through the app.
- **If it fails:** run `verify-audit` (T4.5); it will name the first broken sequence.

### T4.4 Nobody can rewrite history

| # | Connected as | Run | Expect |
| --- | --- | --- | --- |
| T4.4.1 | `humani_t_app` | `UPDATE audit_events SET action = 'x.y';` | `permission denied for table audit_events` |
| T4.4.2 | `humani_t_app` | `DELETE FROM audit_events;` | `permission denied for table audit_events` |
| T4.4.3 | `humani_t_app` | `INSERT INTO audit_chain_head VALUES (2, 0, '\x00');` | `permission denied for table audit_chain_head` |
| T4.4.4 | `postgres` (superuser) | `DELETE FROM audit_events;` | `audit_events is append-only (DELETE refused)` |
| T4.4.5 | `postgres` (superuser) | `TRUNCATE audit_events;` | `audit_events is append-only (TRUNCATE refused)` |

- **Proves:** D4.5 — the grants stop the app role from even asking; the trigger stops everyone else, superusers included, unless they disable it.
- **If it fails:** a successful statement on the development database has destroyed history: stop, and report it. A `DELETE` that succeeds as `humani_t_app` means migration `0002`'s `REVOKE` did not run (the role did not exist when it ran).

### T4.5 Verify the development chain

- **Do:** `uv run --env-file .env python -m app.cli verify-audit; $LASTEXITCODE`
- **Expect:** `audit chain intact: N event(s)` and `0`.
- **Proves:** D4.7 against the real database.
- **If it fails:** a `BROKEN` line names the first bad sequence and why; nothing in the normal app can cause that.

### T4.6 Watch tampering being detected

Done in a throwaway database, so the development chain stays intact.

1. **Create the database.** In pgAdmin as `postgres`, on database `humani_t`: `CREATE DATABASE humani_t_tamper OWNER humani_t_migrator;`
2. **Give it the same grants as the real databases.** Refresh pgAdmin's database list, open a Query Tool on `humani_t_tamper` as `postgres`, and run what `infrastructure/postgres/init/01-roles.sh` runs for `humani_t`:

```sql
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO humani_t_migrator;
GRANT USAGE ON SCHEMA public TO humani_t_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humani_t_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO humani_t_app;
```

   Default privileges are stored per database, so a new database does not inherit the ones the init script set. Without this step the app role has no rights on the new tables and step 3 fails with `permission denied for table data_keys`.
3. **Migrate it, add two events, verify.** In PowerShell, in `services\api`:

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

   Expect `audit chain intact: 2 event(s)`. A variable already set in the shell takes precedence over `--env-file` (verified for the checkpoint), which is why `$env:HUMANI_T_DATABASE_URL` points these commands at the tamper database while the KEK still comes from `.env`. **Safety check before tampering:** `verify-audit` must report **2** events. If it reports your development chain's (larger) count, the variable did not take effect — stop, and do not tamper.
4. **Tamper.** In pgAdmin as `postgres` on `humani_t_tamper`:

```sql
ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only;
UPDATE audit_events SET action = 'keys.innocent' WHERE sequence = 1;
ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only;
```

5. **Verify again:** `uv run --env-file .env python -m app.cli verify-audit; $LASTEXITCODE`. Expect `AUDIT CHAIN BROKEN at sequence 1: event content was altered` and `1`. Even a superuser who disables the trigger is caught.
6. **Optional: undo the edit, then truncate.** With the trigger disabled again, `UPDATE audit_events SET action = 'keys.rewrapped' WHERE sequence = 1;` and re-enable it: `verify-audit` reports intact again (the chain checks content, and the content is back). Then, trigger disabled, `DELETE FROM audit_events WHERE sequence = 2;`, trigger enabled: `verify-audit` reports `AUDIT CHAIN BROKEN at sequence 2: events missing from the end` — the head still remembers event 2.
7. **Clean up.** In PowerShell: `Remove-Item Env:HUMANI_T_DATABASE_URL`, then `Get-ChildItem Env:HUMANI_T_*` must print nothing. **Do not run `uv run pytest` before this**: tests and CLI commands would use the tamper database. In pgAdmin, close the Query Tool on the tamper database, connect to `humani_t`, and run `DROP DATABASE humani_t_tamper;` (if pgAdmin still holds a connection to it: `DROP DATABASE humani_t_tamper WITH (FORCE);`).

- **Proves:** D4.5 and D4.7 end to end: the trigger can be bypassed only by a superuser on purpose, and the chain still reveals it.
- **If it fails:** an intact report after step 4 means the hash no longer covers `action`, which is a critical defect.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T4.1 Chain in memory | ☐ | |
| T4.2 Your audit trail, no emails | ☐ | |
| T4.3 Links and head | ☐ | |
| T4.4 Nobody can rewrite history | ☐ | |
| T4.5 verify-audit on dev | ☐ | |
| T4.6 Tampering detected | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the command, the output and any API log line. It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Actions are `noun.verb`; details need a scope; the same entry always hashes the same | `tests/test_audit.py::TestAuditEntry` |
| Edits (even with a recomputed hash), deletions, reordering and truncation are detected | `tests/test_audit.py::TestVerifyChain` |
| Events chain in order, roll back with their action, record actor and request; details encrypted, readable by the project, unreadable after a shred but still verifiable | `tests/test_audit.py::TestAuditLog` |
| Appending moves the head; an append that does not extend the head is refused (memory and Postgres) | `tests/test_storage_contract.py::TestAuditLog` |
| 50 concurrent writers never fork the chain; the app role cannot `UPDATE` or `DELETE` | `tests/test_storage_contract.py::TestPostgresAuditGuarantees` |
| Sign-ins, failures, sign-outs and credential changes are recorded, never with emails or passwords | `tests/test_auth_api.py::TestAuditTrail` |
| `verify-audit` exits 0 when intact, 1 and says `BROKEN` when not | `tests/test_cli.py` |

```powershell
uv run pytest tests/test_audit.py "tests/test_storage_contract.py::TestAuditLog" tests/test_cli.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G10 | The chain head is not anchored outside the database | A superuser could rewrite the whole chain consistently and `verify-audit` would not notice | Worker job writing an HMAC-signed head to the logs (M4) |
| — | `verify-audit` is run by hand | Tampering is found only when someone looks | Scheduled by the worker (M4) |
| — | `account.disabled` records no request id | That one event cannot be matched to a log line | Pass the request id through `disable_user` |
| — | Verification reads the whole log into memory | Slow and memory-hungry for a very long log | Verify incrementally from a signed checkpoint, with G10 |

## Glossary

| Term | Meaning |
| --- | --- |
| **Append-only** | Rows can be added but never changed or removed. |
| **Audit chain head** | The latest event's sequence and hash, kept in one locked row. |
| **Canonical form** | One fixed byte representation of a value, so it always hashes the same. |
| **Genesis hash** | The 32 zero bytes the first event chains onto. |
| **Hash chain** | A sequence where each item includes the hash of the one before, so any change breaks every later link. |
| **Row lock (`FOR UPDATE`)** | A Postgres lock on selected rows, held until the transaction ends; other writers wait. |
| **SHA-256** | A cryptographic hash function: a 32-byte fingerprint that changes completely if the input changes. |
| **Statement-level trigger** | A database function run once per statement, here to refuse `UPDATE`, `DELETE` and `TRUNCATE`. |
| **Superuser** | A Postgres role that bypasses permission checks (`postgres` in development). |
