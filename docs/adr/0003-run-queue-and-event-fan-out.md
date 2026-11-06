# ADR-0003: The run table is the job queue; Valkey pub/sub only wakes waiters

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

A review run is a background job ([architecture §4.4](../architecture.md#44-runs-live-events-and-gates)). Three things have to move between processes:

1. **Work.** The API queues a run; one worker, of possibly several, must execute it. A run pauses at a gate, holding nothing, and is queued again when someone decides. A worker can die mid-stage, and the run must not stay "running" forever.
2. **Events.** Every event is stored, encrypted under the project's key, before anyone is told; any API instance must be able to serve any stream, replaying after `Last-Event-ID`.
3. **Wake-ups.** A worker waiting for work and a stream waiting for events should react within a moment, without polling the database every few milliseconds.

The deployment already runs Postgres (the source of truth) and Valkey (rate limits). Every added service is more to secure, back up, monitor and explain to a small humanitarian IT team.

## Decision

**The `runs` table is the queue.** A queued run is a job. A worker claims the longest-queued run with `SELECT … FOR UPDATE SKIP LOCKED` and, in the same transaction, marks it running under a **lease** (`lease_owner`, `lease_expires_at`). It renews the lease while a stage runs. Any worker that finds a run whose lease has expired resumes it, or fails it after `MAX_RECOVERIES` resumptions, or cancels it if someone asked. A gate pauses the run and gives the lease back; deciding the gate queues the run again. Every change to a run is made under its row lock and is conditional on the lease, so a worker that lost its lease finds every write refused.

**The event log is a table too.** `run_events` holds each event with an id that increases by one within its run, allocated under the run's row lock, payload sealed under the project's data key and bound to `(run, id)`. Streams only ever read this table.

**Valkey pub/sub carries wake-ups, never data.** After a commit, the writer publishes on `runs:queued` or `run:<id>`. Each process holds one pattern subscription and wakes its own waiters. A signal says only "look again"; every waiter also wakes on a timeout (2 s for a stream, 5 s for an idle worker) and looks anyway. A lost signal costs latency, never an event. Without Valkey configured, signals stay in-process and everything still works by polling.

## Alternatives considered

| Option | Why not |
| --- | --- |
| **Celery** (with Valkey or RabbitMQ as broker) | A second source of truth for "which runs exist and in what state", which must then be kept consistent with Postgres; tasks are acknowledged outside our transaction, so "queued" can commit without the run row, or the reverse. Its visibility-timeout redelivery is per broker and easy to misconfigure. A large dependency for one queue. |
| **RQ, arq, Dramatiq** | Lighter, but the same split: the job lives in Valkey, the run in Postgres. Valkey here runs with persistence off (`--save '' --appendonly no`); a restart would silently drop queued jobs. |
| **Valkey streams** (consumer groups, `XAUTOCLAIM`) for the queue | Has pending-entry lists and claiming, which is most of a lease. But the run's state would still live in Postgres, so every transition is a two-system write with no shared transaction, and replay would need Valkey retention we do not run. |
| **Valkey streams** as the event log | Replay by id is native, but the log is project data that must be encrypted under the project's key and shredded with it, backed up with everything else, and readable by any instance after a Valkey restart. Postgres already gives all of that. |
| **Postgres `LISTEN/NOTIFY`** for wake-ups | Delivered on commit, which is elegant. But each process needs a dedicated connection outside the bounded pool, held open forever and re-established by hand after a restart, through asyncpg rather than SQLAlchemy. Valkey is already deployed and required in production, and one pub/sub connection per process does the same job. Kept as the fallback if Valkey is ever removed. |
| **Polling only** | Correct (the design already falls back to it), but either laggy or a steady load on the database from every open stream. |
| **An in-process `asyncio` queue in the API** | Loses runs on restart and cannot spread work across processes. |

## Consequences

**Easier**

- Queuing a run, the audit event and the review's status change commit together or not at all.
- Backups, encryption at rest, crypto-shredding and the `humani_t_app` grants cover the queue and the log with no extra work.
- A crash is handled by one rule (lease expiry), tested with a fake clock in the storage contract and the worker specs.
- Any API instance serves any stream, and a restart loses nothing.

**Harder**

- Throughput is bounded by Postgres row locking. That is ample for review runs (minutes long, a handful at a time); a high-volume job type would need its own decision.
- A stage may run more than once (after a gate, a crash or a deploy), so stages must be idempotent. This is written on the `Stage` Protocol, where stage authors read it.
- The worker renews its lease every 20 s; a worker paused longer than the 60 s lease (a debugger, a frozen VM) loses the run to another worker and is then silenced by the lease check.
- Pub/sub delivers at most once. Correctness never depends on it, but a missed signal shows as up to 2 s of delay on a stream.
