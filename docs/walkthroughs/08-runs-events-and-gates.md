# 08 — Runs, events and gates

> **Milestones:** [architecture §4.4](../architecture.md#44-runs-live-events-and-gates) · **Status:** done · **Previous:** [07 — Projects and roles](07-projects-and-roles.md) · **Next:** [09 — Plan and discover](09-plan-and-discover.md)

Before this phase a project could hold members and run a search, but nothing could take longer than one HTTP request. Now a member can ask a **review** question inside a project and start a **run** of some of the pipeline's stages. The request returns `202` at once; a separate **worker** process executes the stages; and every member can follow the run live on one Server-Sent Events stream, which replays whatever they missed after a dropped connection. A run can stop at a **gate** (confirm a vocabulary, approve masking) and wait, holding nothing, until a member with the right role decides; it can be cancelled, keeping the stages that finished; and if a worker dies mid-stage, its lease expires and another worker resumes the run, or fails it in plain language, so no run is ever stuck. Every event is encrypted under the project's key before it is sent, so deleting the project shreds the log with everything else. The stages themselves are stand-ins until the discover, mask, screen and brief phases plug theirs in. This implements [`architecture.md` §4.4](../architecture.md#44-runs-live-events-and-gates) and [UI_UX_SPEC §11.1–11.2](../UI_UX_SPEC.md#11-interaction-and-state), and serves brief rules 7, 8, 15, 16 and 17 and [§5.7](../architecture.md#57-concurrency-and-resources).

## What this phase delivers

- **Reviews** inside a project: a question, stored only encrypted under the project's key, with a status that follows UI_UX_SPEC §11.1.
- **Runs** as background jobs: started with `202`, executed by `python -m app.worker`, any number of workers sharing one queue in Postgres.
- **A live event stream** per run: `stage.started`, `stage.progress`, `stage.finished`, `agent.step`, `gate.required`, `gate.resolved`, `job.finished`; replay after `Last-Event-ID`; a keep-alive comment every 15 s; the stream ends after `job.finished`, or within a minute of the session or membership ending.
- **Gates** of two kinds, `vocabulary` and `privacy_review`, decided once: the first decision wins and every open stream sees it; a second gets `409`.
- **Cancel**, keeping finished stages; **failure** in plain language, never a stack trace; **crash recovery** through leases.
- **A stage seam** (`app/runs/base.py`) that the real stages plug into, with a fake stage that uses every part of it.
- **Endpoints** (all under `/api/v1`):

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| `POST` | `/projects/{project_id}/reviews` | owner, reviewer | Ask a question |
| `GET` | `/projects/{project_id}/reviews` | any member | The project's reviews, newest first |
| `GET` | `/projects/{project_id}/reviews/{review_id}` | any member | One review |
| `POST` | `/reviews/{review_id}/runs` | owner, reviewer | Start a run: `202 {run_id}` |
| `GET` | `/reviews/{review_id}/runs` | any member | The review's runs, newest first |
| `GET` | `/runs/{run_id}/events` | any member | The run's event stream (`text/event-stream`) |
| `POST` | `/runs/{run_id}/cancel` | owner, reviewer | Cancel; finished stages are kept |
| `POST` | `/gates/{gate_id}` | owner; reviewer too for a `vocabulary` gate | Decide a gate |

- **Migration** `0007` (`20261103_0007_runs.py`): tables `reviews`, `runs`, `run_events`, `gates`.
- **Operator commands:** `uv run --env-file .env python -m app.worker`, and `python -m app.worker --check-health` for the container's health check: it exits `0` only while the worker's heartbeat file (default `/tmp/humani-t-worker-heartbeat`, or `--heartbeat-file`) is younger than 15 seconds, three poll intervals. The worker touches it on every turn of its loop and every lease renewal, so a long stage still counts as alive.
- **Settings:** none. The worker uses the same settings as the API; Valkey, when configured, also carries the wake-up signals.

### Data model

| Table | Holds | Notes |
| --- | --- | --- |
| `reviews` | `project_id`, `question_sealed`, `status`, `created_by`, `version` | The question is AES-GCM under the project's key, bound to `reviews.question` and the review id |
| `runs` | `review_id`, `project_id`, `stages[]`, `state`, `stages_finished`, `stage_started_at`, `started_at`, `finished_at`, `cancel_requested_at`, `lease_owner`, `lease_expires_at`, `recoveries`, `version` | Also the job queue. Partial indexes `ix_runs_queue` (queued) and `ix_runs_leases` (running) |
| `run_events` | `(run_id, id)`, `name`, `payload_sealed`, `occurred_at` | `id` counts from 1 in each run. Payload bound to `(run, id)`. The app role has no `UPDATE` |
| `gates` | `run_id`, `project_id`, `stage`, `kind`, `payload_sealed`, `resolved_at`, `resolved_by`, `decision`, `revisions_sealed` | One gate per `(run, stage, kind)`; resolved at most once |

## Architecture choices and why

### D8.1 A run is a background job; the request that starts it returns at once

- **Decision:** `POST /reviews/{id}/runs` validates, stores a `queued` run, audits `run.started` and answers `202 {run_id}` with `Location: /api/v1/runs/{run_id}/events`. A worker process executes it.
- **Why:** a run takes minutes and may wait hours for a person at a gate. Holding an HTTP request (and a database connection) that long breaks proxies, pools and the architecture's rule that waiting costs nothing (§4.4, §5.7).
- **Rejected:** running stages inside the request (times out, holds a connection); an `asyncio` task in the API process (lost on restart, cannot spread across processes).
- **Costs:** one more process to run (`python -m app.worker`); stages must be safe to run again (D8.6).
- **Where:** [`application/runs.py`](../../services/api/app/application/runs.py) (`start`), [`worker.py`](../../services/api/app/worker.py).
- **Proven by:** `tests/test_runs_api.py::TestStartingARun`.

### D8.2 The run table is the job queue, claimed with `SKIP LOCKED`

- **Decision:** a queued run is a job. A worker claims the longest-queued one with `SELECT … FOR UPDATE SKIP LOCKED` and marks it `running` in the same transaction. See [ADR-0003](../adr/0003-run-queue-and-event-fan-out.md).
- **Why:** the run, its review's status and the audit event commit together; backups, encryption and grants cover the queue for free; two workers never take the same run and never wait for each other.
- **Rejected:** Celery, RQ, arq, Dramatiq (a second source of truth outside our transaction; Valkey here runs without persistence); Valkey streams (two-system writes, and the log must be encrypted and shredded with the project).
- **Costs:** throughput is bounded by Postgres row locks, which is ample for minute-long runs.
- **Where:** [`storage/postgres/runs.py`](../../services/api/app/storage/postgres/runs.py) (`claim_next`), migration `0007`.
- **Proven by:** `tests/test_runs_storage_contract.py::TestTheQueue`, `TestPostgresUnderConcurrency::test_two_workers_claiming_at_once_get_different_runs_without_waiting`.

### D8.3 Leases, not locks; an expired lease is resumed, failed or cancelled

- **Decision:** a claimed run carries `lease_owner` and `lease_expires_at` (60 s), renewed every 20 s while a stage runs. Before claiming, every worker looks for runs whose lease has expired: it queues them again (counting a `recovery`), fails them after `MAX_RECOVERIES` (2) with a plain-language message, or cancels them if someone asked. A worker told to stop (SIGTERM, Ctrl+C) hands its run back at once without counting a recovery.
- **Why:** a worker can die at any moment. A row lock held for the whole stage would pin a connection for minutes; a lease holds nothing and still guarantees the run is never stuck as `running`. Counting recoveries stops a stage that kills its worker every time from looping forever.
- **Rejected:** no recovery (stuck runs); unlimited retries (poison runs); a separate reaper process (one more thing to deploy; every worker does it in passing).
- **Costs:** a worker frozen longer than the lease (a debugger, a suspended VM) loses its run to another worker. It then finds every write refused (D8.5) and stops silently.
- **Where:** [`domain/runs.py`](../../services/api/app/domain/runs.py) (`Run.claimed`, `requeued`, `released`, `recovery_for`), [`application/run_worker.py`](../../services/api/app/application/run_worker.py) (`recover_expired`, `_heartbeat`, `_release`).
- **Proven by:** `tests/test_runs_domain.py::TestCrashRecovery`, `tests/test_runs_worker.py::TestCrashRecovery`.

### D8.4 Every event is stored, encrypted, before it is sent

- **Decision:** each event is appended to `run_events` in the same transaction as the change it reports, with the next id in its run (allocated under the run's row lock), and its payload sealed under the project's data key and bound to `(run, id)`. Streams only read this table.
- **Why:** replay after `Last-Event-ID` needs every event; any API instance must serve any stream; the log is project data and must die with the project's key (§5.4). Binding to `(run, id)` means a payload copied to another position fails to decrypt, so the log cannot be reordered unnoticed.
- **Rejected:** sending from the worker's memory (lost on restart, tied to one process); plaintext payloads (the log would outlive a deleted project).
- **Costs:** one short transaction per event, including one per screened paper.
- **Where:** [`application/run_log.py`](../../services/api/app/application/run_log.py), `RunEventRepository.next_id` in [`storage/base.py`](../../services/api/app/storage/base.py).
- **Proven by:** `tests/test_runs_storage_contract.py::TestEventLog`, `TestPostgresUnderConcurrency::test_concurrent_writers_never_repeat_or_skip_an_event_id`, `tests/test_runs_api.py::TestPrivacy`.

### D8.5 Exactly one `job.finished`, always last

- **Decision:** a run reaches a final state only through `RunLog.finish`, which appends `job.finished` and saves the final state in one transaction. `next_id` refuses any event for a finished run, and, for a worker, any event unless that worker holds the lease.
- **Why:** the interface's final strip state and the browser notification both hang on `job.finished` (UI_UX_SPEC §11.2). Two would contradict each other; none would leave the interface spinning.
- **Rejected:** writing `job.finished` from several places with checks at each (one forgotten check is a duplicate).
- **Where:** [`application/run_log.py`](../../services/api/app/application/run_log.py) (`finish`), `next_id` in [`storage/memory.py`](../../services/api/app/storage/memory.py) and [`storage/postgres/runs.py`](../../services/api/app/storage/postgres/runs.py).
- **Proven by:** `tests/test_runs_worker.py::TestExactlyOneFinish`, `TestCrashRecovery::test_a_worker_that_lost_its_lease_cannot_write_to_the_run_any_more`.

### D8.6 A gate ends the stage's execution; resuming runs the stage again, which then gets the decision

- **Decision:** `context.require_gate(kind, payload)` stores the gate, emits `gate.required`, sets the run `waiting`, gives the lease back and ends the stage's execution. Deciding the gate queues the run; a worker runs the same stage again from its start, and this time `require_gate` returns the decision (`approve` or `reject`, plus any `revisions`). `stage.started` is not repeated.
- **Why:** waiting must cost no worker and no connection (§4.4). Re-running the stage is the only resumption that also survives a crash, so one mechanism covers both.
- **Rejected:** keeping the stage's coroutine alive while it waits (a worker per waiting run, lost on restart); serialising the stage's state (every stage would need its own resumable format).
- **Costs:** stages must be idempotent: work already stored is found and skipped. That rule is on the `Stage` Protocol.
- **Where:** [`runs/base.py`](../../services/api/app/runs/base.py), `_Context.require_gate` in [`application/run_worker.py`](../../services/api/app/application/run_worker.py).
- **Proven by:** `tests/test_runs_api.py::TestGates`, `tests/test_runs_worker.py::TestGatesAtTheWorker`.

### D8.7 A gate is decided once, by a role that may

- **Decision:** `POST /gates/{gate_id}` resolves with one conditional update (`… WHERE resolved_at IS NULL`); the loser gets `409`. A `privacy_review` gate needs an owner (approving masking means reading raw text, which only owners may); a `vocabulary` gate an owner or reviewer. The table is `action_to_resolve` in the domain.
- **Why:** §4.4 and UI_UX_SPEC §5.3.6 ("Only owners can do this"). Two members deciding at once must not both win.
- **Rejected:** check-then-write (both pass the check); one role for both kinds (a reviewer could approve masking of text they may not read).
- **Where:** [`domain/runs.py`](../../services/api/app/domain/runs.py) (`action_to_resolve`), [`application/runs.py`](../../services/api/app/application/runs.py) (`resolve_gate`).
- **Proven by:** `tests/test_runs_domain.py::TestGates`, `tests/test_runs_api.py::TestGates`, `tests/test_runs_storage_contract.py::TestPostgresUnderConcurrency::test_concurrent_resolutions_of_one_gate_have_exactly_one_winner`.

### D8.8 Server-Sent Events, replayed from the log

- **Decision:** `GET /runs/{run_id}/events` sends `retry: 3000`, every stored event after `Last-Event-ID` (or `?last_event_id=`, for a new `EventSource` that cannot set the header; the header wins), then follows live. Every event's shape is published in OpenAPI as `RunStreamEvent`, a union discriminated by `event`, so the web app generates its types. An id the run does not have (malformed, `0`, negative, beyond its latest event) replays from the start. The stream ends after `job.finished`, so a finished run replays and closes. Data is one line of ASCII JSON.
- **Why:** events flow one way; `EventSource` reconnects and sends `Last-Event-ID` by itself; SSE runs over plain HTTP through Caddy. Replaying from the start on an unknown id means a client may see an event twice but never misses one.
- **Rejected:** WebSockets (a second authentication path and cross-site hijacking to defend, for one-way traffic); polling (laggy or heavy, and the browser would rebuild replay).
- **Costs:** ids count from 1 in every run, so an id from another run is recognised only when it is beyond this run's latest event (noted in §4.4). `EventSource` never sends one, so only a misbehaving client meets this.
- **Where:** [`api/sse.py`](../../services/api/app/api/sse.py), [`api/schemas/run_events.py`](../../services/api/app/api/schemas/run_events.py), [`api/routes/runs.py`](../../services/api/app/api/routes/runs.py), `open_stream` in [`application/runs.py`](../../services/api/app/application/runs.py), `resume_point` in [`domain/runs.py`](../../services/api/app/domain/runs.py).
- **Proven by:** `tests/test_runs_api.py::TestTheEventStream`, `tests/test_runs_domain.py::TestResumePoint`.

### D8.9 Signals say "look again" and carry nothing

- **Decision:** after each commit the writer publishes on `runs:queued` or `run:<id>` through the `Signals` seam: Valkey pub/sub when configured, in-process otherwise. Every waiter also wakes on its own timeout (2 s for a stream, 5 s for an idle worker) and reads the database anyway.
- **Why:** the database is the truth; a signal only shortens the wait. A lost signal costs up to 2 s, never an event. Without Valkey, everything still works by polling.
- **Rejected:** Postgres `LISTEN/NOTIFY` (a dedicated connection per process outside the bounded pool); putting events on the pub/sub channel (at-most-once delivery, and project data outside the encrypted log).
- **Where:** [`signals/`](../../services/api/app/signals/).
- **Proven by:** `tests/test_signals_contract.py` (memory and Valkey), `TestValkeyOutage`, `tests/test_runs_worker.py::TestGatesAtTheWorker::test_resolving_wakes_the_workers`.

### D8.10 A long stream does not outlive its session, and never holds a connection

- **Decision:** the stream re-checks, at least once a minute, that the session is live, the user enabled and still a member of a live project; if not, it ends. The check does not extend the session: watching is not activity. While quiet it sends `: keep-alive` every 15 s. It reads the log in a short transaction and waits with none open.
- **Why:** §4.4 and §5.7. A revoked session must stop receiving project data; an idle tab must still reach the 1-hour idle timeout; Caddy and other proxies close idle connections.
- **Rejected:** checking only at connect (a revoked session would keep streaming for hours); touching the session on each check (an open tab would never idle out).
- **Costs:** a member who only watches a long run is signed out after an hour; the browser's reconnect then gets `401`.
- **Where:** `_follow` and `_still_allowed` in [`application/runs.py`](../../services/api/app/application/runs.py).
- **Proven by:** `tests/test_runs_worker.py::TestTheLiveStream`, `tests/test_runs_api.py::TestTheEventStream::test_a_quiet_stream_sends_keep_alive_comments_and_ends_with_the_session`.

### D8.11 Routes that name a review, run or gate find the project from it

- **Decision:** every use case loads the review, run or gate, then checks membership of *its* project with `require_member`: `404` with one message for "missing" and "not yours", then `403`. Routes with a body check in a dependency first, so the refusal comes before the body is read. The route inventory counts `{review_id}`, `{run_id}` and `{gate_id}` routes as project routes.
- **Why:** §5.5: ids must not be probeable, and a non-member must learn nothing, not even whether their body was valid.
- **Rejected:** nesting every path under `/projects/{project_id}/…` (the §4.4 contract names these paths; and the project in the path would still have to be checked against the object's own project).
- **Where:** [`application/membership.py`](../../services/api/app/application/membership.py), [`api/routes/runs.py`](../../services/api/app/api/routes/runs.py), [`tests/test_route_inventory.py`](../../services/api/tests/test_route_inventory.py) (`_PROJECT_PARAMETERS`).
- **Proven by:** `tests/test_runs_api.py::TestAccess`, `tests/test_route_inventory.py::TestMembershipIsRequired`.

### D8.12 The review's status follows its run, in the same transaction

- **Decision:** the review status table (§11.1) is a pure function tested as a table; starting, pausing, resuming and finishing a run move the review in the same transaction. A run may start from any state in which no run is going: before acceptance, the run's first stage asks for the vocabulary through a `vocabulary` gate.
- **Why:** the project list shows "Running", "Needs you", "Brief ready" from one read; a second run while one is going would race on the same review.
- **Where:** [`domain/reviews.py`](../../services/api/app/domain/reviews.py), `move_review` in [`application/run_log.py`](../../services/api/app/application/run_log.py).
- **Proven by:** `tests/test_runs_domain.py::TestReviewStateMachine`, `tests/test_runs_api.py::TestStartingARun::test_a_second_run_cannot_start_while_one_is_going`.

### D8.13 Failures are explained in plain language; details stay in the logs

- **Decision:** a stage that raises `StageFailedError("The model server isn't answering.")` has that message shown. Any other exception fails the run with "The Discover stage stopped because of an unexpected error. Finished stages are kept; …", and the traceback goes only to the deployment's own log.
- **Why:** §4.4: never raw text or a stack trace. An exception's text can hold a document snippet or an internal address.
- **Where:** `stage_failed_message` in [`domain/runs.py`](../../services/api/app/domain/runs.py), `_execute_stages` in [`application/run_worker.py`](../../services/api/app/application/run_worker.py).
- **Proven by:** `tests/test_runs_api.py::TestFailure`.

### D8.14 A deleted project's runs end without writing anything

- **Decision:** once a project's key is destroyed, a run of it ends `cancelled` at its next write, with no event (its log can never be read again; its stream answers `404`). Ending a run never depends on the key.
- **Why:** otherwise every attempt to finish or recover that run would raise, and the recovery sweep would fail on every turn, blocking every other run in the deployment.
- **Where:** `finish` in [`application/run_log.py`](../../services/api/app/application/run_log.py), `_execute` in [`application/run_worker.py`](../../services/api/app/application/run_worker.py).
- **Proven by:** `tests/test_runs_worker.py::TestDeletedProjects`.

### D8.15 Stages are a Protocol with a fake; the real ones plug in later

- **Decision:** a stage is `Stage.run(context) -> summary`; the context offers `progress`, `agent_step`, `require_gate` and `raise_if_cancelled`, each one short transaction. Until the real stages exist, `api/dependencies.py` wires a `FakeStage` for every id: five items half a second apart, a `privacy_review` gate before `mask`, two rounds of agent steps in `answer`.
- **Why:** the run machinery can be built, tested and used end to end now; the discover, mask, screen and brief lanes add one class each and change one line in `dependencies.py`.
- **Costs:** until then, every environment runs fake stages (listed under Known gaps).
- **Where:** [`runs/base.py`](../../services/api/app/runs/base.py), [`runs/fake.py`](../../services/api/app/runs/fake.py), `_stages` in [`api/dependencies.py`](../../services/api/app/api/dependencies.py).
- **Proven by:** every run spec drives real `RunWorker`s through this seam with scripted stages (`tests/run_support.py`).

### Deliberately not used

| Library or approach | Why not |
| --- | --- |
| Celery, RQ, arq, Dramatiq | A job store outside Postgres; see D8.2 and ADR-0003 |
| `sse-starlette` | Twenty lines of formatting (`api/sse.py`) do not justify a dependency; Starlette's `StreamingResponse` already handles disconnects |
| WebSockets | See D8.8 |
| Postgres `LISTEN/NOTIFY` | See D8.9; kept as the fallback if Valkey is ever removed |

## Libraries

No dependency was added in this phase. Two already locked are used in new ways.

### redis (redis-py) 8.1.0, for pub/sub

- **What it does:** one pattern subscription per process (`humani-t:signal:*`) whose messages wake that process's waiters.
- **Why this one:** already the Valkey client for rate limits; its asyncio `PubSub` blocks without a read timeout while listening.
- **Alternatives:** a client per waiting stream (a connection each); `LISTEN/NOTIFY` (D8.9).
- **Trade-offs:** pub/sub is at most once and ignores the database index, so signals carry nothing and channels are prefixed. An outage is logged and waited out, never raised.
- **What we gave up:** guaranteed delivery, which the design does not need.

### Starlette 1.6.0 `StreamingResponse`, for the stream

- **What it does:** sends each `bytes` the generator yields as it is produced, and cancels the generator when the client disconnects.
- **Why this one:** part of FastAPI already; the middleware stack is plain ASGI (not `BaseHTTPMiddleware`), so nothing buffers the stream ([`api/middleware.py`](../../services/api/app/api/middleware.py)).
- **Trade-offs:** the test client buffers whole responses, so a stream that has not ended cannot be read over HTTP in tests. Live behaviour is specified one layer down, on `RunService.open_stream`, with a clock that moves only when a wait times out.

## Files to read, in order

From the domain rules outwards, because every other file obeys them.

1. [`app/domain/reviews.py`](../../services/api/app/domain/reviews.py) — the review status table and the question rules.
2. [`app/domain/runs.py`](../../services/api/app/domain/runs.py) — the run state table, `Run` and its transitions, `recovery_for`, event names and payloads, `resume_point`, gates and `action_to_resolve`.
3. [`app/runs/base.py`](../../services/api/app/runs/base.py) — the stage seam and its two rules; then [`app/runs/fake.py`](../../services/api/app/runs/fake.py).
4. [`app/signals/base.py`](../../services/api/app/signals/base.py) — why a signal carries nothing; then `memory.py` and `valkey.py`.
5. [`app/storage/base.py`](../../services/api/app/storage/base.py) — `ReviewRepository`, `RunRepository`, `RunEventRepository`, `GateRepository`: read the docstrings of `lock`, `claim_next`, `next_id` and `resolve`.
6. [`app/storage/postgres/runs.py`](../../services/api/app/storage/postgres/runs.py) and [`migrations/versions/20261103_0007_runs.py`](../../services/api/migrations/versions/20261103_0007_runs.py) — `SKIP LOCKED`, the row lock in `next_id`, the conditional gate update, the grants.
7. [`app/application/membership.py`](../../services/api/app/application/membership.py) — the one access check.
8. [`app/application/run_log.py`](../../services/api/app/application/run_log.py) — appending, reading and the one way a run finishes.
9. [`app/application/runs.py`](../../services/api/app/application/runs.py) — start, cancel, resolve a gate, and the stream (`_follow`).
10. [`app/application/run_worker.py`](../../services/api/app/application/run_worker.py) — claim, execute, recover, release; `_Context`.
11. [`app/api/routes/runs.py`](../../services/api/app/api/routes/runs.py), [`app/api/sse.py`](../../services/api/app/api/sse.py), [`app/api/routes/reviews.py`](../../services/api/app/api/routes/reviews.py) — the boundary.
12. [`app/worker.py`](../../services/api/app/worker.py) and `_signals`, `_stages` in [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py) — composition.

## Set up

Migrate the development database to `0007` and start the API as in [S0.9](00-setup.md#s09-start-the-api):

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
uv run --env-file .env alembic current
```

Expect `0007 (head)`. Then, in a **second terminal you keep open**, start a worker:

```powershell
cd services\api
uv run --env-file .env python -m app.worker
```

Expect a `worker_started` log line with the worker id (`host:pid:random`) and the eight stage ids. Leave it running.

You need the project, members and Postman devices from [07](07-projects-and-roles.md#set-up): the admin owns `{{project}}`, Kofi is a reviewer on device B (`session_b`, `csrf_b`), Maya is a viewer. Tariq (`tariq@example.org`) or any other account that is not a member stands in for an outsider. If MFA is required, finish each sign-in with the second factor first.

For the stream, `curl.exe` (shipped with Windows 10 and 11; `curl` in PowerShell 5.1 is an alias for `Invoke-WebRequest`, which buffers) needs the session cookie. Copy the Postman variable into the shell:

```powershell
$env:HT_BASE = 'http://127.0.0.1:8731/api/v1'
$env:HT_SESSION = '<paste {{session}}>'
```

`-N` turns off curl's buffering so each event appears as it arrives.

## Manual tests

All Postman requests carry the session cookie and `X-CSRF-Token` from [S0.10](00-setup.md#s010-postman). "As admin" is the main variables; "as Kofi" is device B.

### T8.1 Reviews

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T8.1.1 | As admin: `POST {{base}}/projects/{{project}}/reviews` `{"question":"  Do cash transfers reduce acute malnutrition among displaced children?  "}` | `201`; `question` trimmed, `status` = `new`, `project_id` = `{{project}}`; `Location` ends `/reviews/<id>`. **Note the id** as `{{review}}` | Create (D8.12) |
| T8.1.2 | As Maya (viewer): the same request | `403` | Viewers ask nothing |
| T8.1.3 | `POST …/reviews` with `{"question":""}`, then `{"question":"Cash?","status":"accepted"}` | `422` each time | Question rules; no mass assignment |
| T8.1.4 | As Kofi: `GET {{base}}/projects/{{project}}/reviews` and `…/reviews/{{review}}` | `200`, the review with its question in the clear | Members read |
| T8.1.5 | pgAdmin (`humani_t`): `SELECT id, question_sealed, status FROM reviews;` | `question_sealed` is unreadable bytes; nothing reads "malnutrition" | Encrypted at rest |

### T8.2 Start a run and follow it

- **Do:**
  1. As admin: `POST {{base}}/reviews/{{review}}/runs` `{"stages":["discover","queries"]}`. Note `run_id`.
  2. At once, in PowerShell:

```powershell
$env:HT_RUN = '<run_id>'
curl.exe -N -i -H "Cookie: __Host-session=$env:HT_SESSION" "$env:HT_BASE/runs/$env:HT_RUN/events"
```

- **Expect:**
  - step 1: `202`, body `{"run_id": "…"}`, header `Location: /api/v1/runs/<run_id>/events`;
  - step 2: status `200`, `content-type: text/event-stream; charset=utf-8`, `cache-control: no-store`; first `retry: 3000`; then `id: 1 / event: stage.started` with `"stage":"queries","position":1,"total":2` (queries first, though it was listed second); five `stage.progress` events half a second apart; `stage.finished` with `"summary":{"items":5}`; the same for `discover` (`position` 2); finally `job.finished` with `"outcome":"done"`, and curl exits by itself;
  - the worker terminal shows nothing per event (only warnings and errors are logged).
- **Proves:** D8.1, D8.4, D8.5, D8.8 — ids `1` to `15` with no gap, pipeline order, the stream closes after `job.finished`.
- **If it fails:** a `202` but no events means no worker is running, or it cannot reach the database; check its terminal. Events arriving all at once at the end means something buffers: you used `curl` instead of `curl.exe`, or forgot `-N`.

### T8.3 Replay and reconnect

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T8.3.1 | Run the T8.2 command again (the run has finished) | The whole history at once, ids `1`–`15`, then curl exits | Finished runs replay, then close (D8.8) |
| T8.3.2 | Add `-H "Last-Event-ID: 7"` | Events `8` to `15` only | Replay after the last id seen |
| T8.3.3 | `-H "Last-Event-ID: 15"` | Only `retry: 3000`, then curl exits | Nothing missed, nothing repeated |
| T8.3.4 | `-H "Last-Event-ID: abc"`, then `"Last-Event-ID: 999"` | All events from `1` each time | Unknown id replays from the start |
| T8.3.5 | No header, URL ending `/events?last_event_id=7`; then the same with `-H "Last-Event-ID: 3"` | Events `8`–`15`; then events `4`–`15` | A new `EventSource` can resume; the header wins |
| T8.3.6 | Open http://127.0.0.1:8731/docs, the events route | Its `200` response is `text/event-stream` with schema `RunStreamEvent`; Schemas lists `StageStartedEvent` … `JobFinishedEvent` | Event types are published for generation |
| T8.3.7 | As admin: `GET {{base}}/reviews/{{review}}/runs` | `200`, newest first; this run `"state":"done","outcome":"done","stages_finished":2` | Run list |

### T8.4 A gate pauses the run

- **Do:**
  1. As admin: `POST {{base}}/reviews/{{review}}/runs` `{"stages":["mask","screen"]}`; follow it with curl as in T8.2.
  2. When `gate.required` appears, note `gate_id` as `{{gate}}` and **leave curl running** for a minute.
  3. As Kofi (reviewer): `POST {{base}}/gates/{{gate}}` `{"decision":"approve"}`.
  4. As admin: `POST {{base}}/gates/{{gate}}` `{"decision":"approve"}`.
  5. Send step 4 again.
- **Expect:**
  - step 1–2: `stage.started` for `mask`, then `gate.required` with `"kind":"privacy_review"` and `"payload":{"items":5,"stage":"mask"}`; then nothing but `: keep-alive` lines, one every 15 seconds. pgAdmin: `SELECT state, lease_owner, lease_expires_at FROM runs WHERE id = '<run_id>';` gives `waiting`, null, null;
  - step 3: `403` — a privacy review needs an owner;
  - step 4: `200` `{"gate_id":"…","decision":"approve","by":"<admin user id>"}`; curl shows `gate.resolved` with the same fields, then `mask` progresses (no second `stage.started` for `mask`), `stage.finished` with `"summary":{"items":5,"decision":"approve"}`, then `screen`, then `job.finished`;
  - step 5: `409`, `"Someone already decided this. Showing their decision."`.
- **Proves:** D8.6 (waiting holds no worker or lease; the stage gets the decision), D8.7 (role per kind; first decision wins), D8.10 (keep-alive).
- **If it fails:** `200` at step 3 means the gate kind's action table is wrong; a second `gate.resolved` after step 5 means the conditional update is not conditional.

### T8.5 Everyone sees the decision

- **Do:** start `{"stages":["mask"]}` as admin. Open **two** PowerShell windows with curl on the same run: one with the admin's session, one with Kofi's (`$env:HT_SESSION = '<{{session_b}}>'`). When both show `gate.required`, approve as admin in Postman.
- **Expect:** both windows show the same `gate.resolved` event with the same `id` at the same moment, then the rest of the run.
- **Proves:** `gate.resolved` reaches every open stream; with Valkey configured the wake-up is immediate (D8.9). Stop Valkey (`docker compose stop valkey`) and repeat: the same events arrive, up to 2 seconds later. Start it again afterwards (`docker compose start valkey`).

### T8.6 Cancel

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T8.6.1 | Start `{"stages":["discover","screen","export"]}`; follow it; after `discover`'s `stage.finished`, `POST {{base}}/runs/<run_id>/cancel` | `202` with `"state":"running","cancel_requested":true`; within half a second curl shows `job.finished` with `"outcome":"cancelled"` | Cancel stops at the next checkpoint |
| T8.6.2 | `GET {{base}}/reviews/{{review}}/runs` | That run: `"state":"cancelled","stages_finished":1` | Finished stages are kept |
| T8.6.3 | Cancel it again | `409`, `"This run has already finished."` | |
| T8.6.4 | Start `{"stages":["mask"]}`; at `gate.required`, cancel; then approve the gate | Cancel: `202`, `"state":"cancelled"`, curl shows `job.finished` cancelled at once. Approve: `409` | A waiting run ends at once; its gate closes |
| T8.6.5 | As Maya (viewer): cancel any running run | `403` | Viewers cancel nothing |

### T8.7 Outsiders see nothing

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T8.7.1 | As Tariq (not a member): `GET {{base}}/runs/<run_id>/events` (Postman, or curl with Tariq's session) | `404`, `{"type":"about:blank","title":"Not Found","status":404,"detail":"No such run."}` | D8.11 |
| T8.7.2 | The same with `00000000-0000-4000-8000-000000000000` as the run id | **Byte-for-byte the same body** | No existence leak |
| T8.7.3 | As Tariq: `POST {{base}}/gates/{{gate}}` with body `{}`; then `POST {{base}}/reviews/{{review}}/runs` with `{}` | `404` each, not `422` | Membership is checked before the body |
| T8.7.4 | Without cookies: `curl.exe -i "$env:HT_BASE/runs/$env:HT_RUN/events"` | `401` problem document | Sign-in required |

### T8.8 A revoked session's stream ends

- **Do:** start `{"stages":["mask"]}` as admin and follow it in curl **with Kofi's session**. When `gate.required` appears, sign Kofi out from device B (`POST {{base}}/auth/logout` as Kofi). Watch the clock.
- **Expect:** curl exits within 60 seconds of the logout, with no error event. Running curl again gives `401`.
- **Proves:** D8.10, the session is re-checked at least once a minute.
- **If it fails:** a stream that keeps receiving keep-alives for more than a minute after logout outlives its session.

### T8.9 A worker crash and a graceful stop

- **Do (crash):**
  1. Start `{"stages":["queries","discover","screen"]}` and follow it.
  2. During `discover`, find the worker's process id (the middle part of the worker id in its `worker_started` line) and kill it without warning: `Stop-Process -Id <pid> -Force`.
  3. pgAdmin: `SELECT state, lease_owner, lease_expires_at, recoveries FROM runs WHERE id = '<run_id>';`
  4. Wait until `lease_expires_at` has passed (about a minute), then start the worker again.
- **Expect:** step 3: `running`, the dead worker's id, a time about a minute ahead, `0`. Step 4: a `run_lease_expired` warning with `action=requeue`; the run continues from the start of `discover` (its progress counts restart at 1, but no second `stage.started` for `discover`), then `screen`, then `job.finished` `done`. curl, if it dropped, resumes without gaps when run again with `-H "Last-Event-ID: <last id it showed>"`.
- **Do (graceful):** start another run; during a stage, press Ctrl+C in the worker terminal; check pgAdmin; start the worker again.
- **Expect:** `worker_stopped` in the log; pgAdmin shows `queued`, `lease_owner` null, `recoveries` `0` at once (no minute's wait); the new worker picks it up immediately.
- **Proves:** D8.3.
- **If it fails:** a run left `running` after its lease expired and a worker ran means recovery is broken; that is the "stuck forever" failure this phase exists to prevent.

### T8.10 Failures in plain language

There is no fake stage that fails, so this one is automated only: `tests/test_runs_api.py::TestFailure`. Read its three tests and the messages they assert.

### T8.11 Audit and storage

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T8.11.1 | pgAdmin: `SELECT action, target_type, target_id FROM audit_events WHERE action IN ('review.created','run.started','run.cancelled','gate.resolved') ORDER BY sequence;` | One row per action you took, ids only | Audit with ids only |
| T8.11.2 | `uv run --env-file .env python -m app.cli verify-audit` | `audit chain intact: N event(s)` | The chain still verifies |
| T8.11.3 | pgAdmin: `SELECT run_id, id, name, payload_sealed FROM run_events ORDER BY run_id, id LIMIT 20;` | `name` readable, `payload_sealed` unreadable bytes | Payloads encrypted (D8.4) |

### T8.12 Deleting the project

- **Do:** reauthenticate as admin (`POST {{base}}/auth/reauthenticate`, with the cookie-capture script from [07](07-projects-and-roles.md#set-up) on its Post-response tab), then `DELETE {{base}}/projects/{{project}}`. Step-up replaced the session, so copy the new `{{session}}` into `$env:HT_SESSION` first, then run the T8.2 curl command again for any of its runs.
- **Expect:** `204`; the stream answers `404` "No such run."; `run_events` rows are still there in pgAdmin, but nothing can decrypt them any more.
- **Proves:** crypto-shredding covers the run log (D8.4, D8.14).

### T8.13 The worker's health check

- **Do:** with the worker running, in a third terminal in `services\api`: `uv run python -m app.worker --check-health; $LASTEXITCODE`. Stop the worker (Ctrl+C), wait 20 seconds, and run it again.
- **Expect:** `0` while it runs (also in the middle of a long run), then `1` once it has been stopped for more than 15 seconds.
- **Proves:** the deploy lane can give the `worker` service a Compose `healthcheck` that runs this command.
- **If it fails:** `1` while the worker runs means it is not touching the file (check `--heartbeat-file` is the same path for both commands, and that the temp directory is writable).

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T8.1 | ☐ | |
| T8.2 | ☐ | |
| T8.3 | ☐ | |
| T8.4 | ☐ | |
| T8.5 | ☐ | |
| T8.6 | ☐ | |
| T8.7 | ☐ | |
| T8.8 | ☐ | |
| T8.9 | ☐ | |
| T8.10 | ☐ | automated only |
| T8.11 | ☐ | |
| T8.12 | ☐ | |
| T8.13 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API or worker log line (with its request id). It gets a failing test before it gets a fix.

## Plugging in a real stage

For the discover, mask, screen and brief lanes:

1. Write a class with `async def run(self, context: StageContext) -> Mapping[str, JsonValue]` in your own package (for example `app/screening/stage.py`). It inherits from nothing; it satisfies [`app/runs/base.py`](../../services/api/app/runs/base.py)'s `Stage` Protocol by shape.
2. Inside it: call `await context.raise_if_cancelled()` between items; `await context.progress(done, total, label)` once per item (labels are masked text only); `await context.agent_step({...})` per agent step; `resolution = await context.require_gate(kind, payload)` where a person must decide — code after it runs on the next execution, with `resolution.decision` and `resolution.revisions`. Return the summary (counts) for `stage.finished`.
3. Make it idempotent: it may run again from its start after a gate, a crash or a deploy. Find and skip what it already stored.
4. Raise `StageFailedError("…")` for a failure a member can understand; anything else becomes the generic message.
5. Wire it in `_stages()` in [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py): `stages["screen"] = ScreeningStage(...)`. Nothing else changes: not the routes, not the worker, not the events.
6. Test it with a `RunWorker` and a scripted context, as `tests/run_support.py` does.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The review and run state machines are exactly their tables | `tests/test_runs_domain.py::TestReviewStateMachine`, `TestRunStateMachine` |
| Stages run in pipeline order; unknown, repeated or unavailable ones are refused | `tests/test_runs_domain.py::TestStagePlan`, `tests/test_runs_api.py::TestStartingARun` |
| An unknown `Last-Event-ID` replays from the start | `tests/test_runs_domain.py::TestResumePoint`, `tests/test_runs_api.py::TestTheEventStream` |
| Wire format, ordering, ids without gaps, replay (header or query), finished runs close | `tests/test_runs_api.py::TestTheEventStream` |
| Every event a run sends validates against its published schema | `tests/test_runs_api.py::TestTheEventStream::test_every_event_matches_its_published_schema` |
| Exactly one `job.finished`, always last, in every way a run ends | `tests/test_runs_worker.py::TestExactlyOneFinish` |
| Gates pause and resume; the stage receives the decision; one winner, then `409` | `tests/test_runs_api.py::TestGates`, `tests/test_runs_worker.py::TestGatesAtTheWorker` |
| Cancel keeps finished stages | `tests/test_runs_api.py::TestCancelling` |
| A crashed worker's run is resumed, failed or cancelled, never stuck | `tests/test_runs_worker.py::TestCrashRecovery` |
| A non-member gets `404` on every run, gate and event route, same as a missing id | `tests/test_runs_api.py::TestAccess`, `tests/test_route_inventory.py::TestMembershipIsRequired` |
| A revoked or expired session, or a lost membership, ends the stream within a minute | `tests/test_runs_worker.py::TestTheLiveStream` |
| A disconnect does not stop the run; streams hold no connection while waiting | `tests/test_runs_worker.py::TestTheLiveStream` |
| No question text in events or audit rows; payloads only as ciphertext | `tests/test_runs_api.py::TestPrivacy` |
| Deleting a project makes the log unreadable and ends its runs without blocking others | `tests/test_runs_api.py::TestPrivacy::test_deleting_the_project_makes_every_event_unreadable`, `tests/test_runs_worker.py::TestDeletedProjects` |
| Storage behaves the same in memory and Postgres: queue, leases, event ids, gates | `tests/test_runs_storage_contract.py` |
| Signals wake waiters; an outage only slows them | `tests/test_signals_contract.py` |
| The health check passes only while the heartbeat is fresh; the worker beats when idle and during a long stage | `tests/test_worker_process.py` |

How to run only this phase's tests (memory, then Postgres and Valkey):

```powershell
cd services\api
uv run pytest tests/test_runs_domain.py tests/test_runs_storage_contract.py tests/test_runs_api.py tests/test_runs_worker.py tests/test_signals_contract.py tests/test_route_inventory.py
$env:TEST_DATABASE_URL = (Select-String -Path .env -Pattern '^HUMANI_T_DATABASE_URL=(.*)').Matches[0].Groups[1].Value -replace '/humani_t$', '/humani_t_test'
$env:TEST_VALKEY_URL = (Select-String -Path .env -Pattern '^HUMANI_T_VALKEY_URL=(.*)').Matches[0].Groups[1].Value
uv run pytest tests/test_runs_storage_contract.py tests/test_signals_contract.py
```

The test database must be migrated to `0007` first ([S0.6](00-setup.md#s06-migrate-the-test-database)).

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G8.1 | Every stage is a fake | Runs produce no real results in any environment | Closed: phases 09–12 replaced every stage but `index`, which was removed because BM25 indexes per search (ADR-0007) |
| G8.2 | Cancellation is cooperative | A stage that never calls `raise_if_cancelled` stops only when it finishes | Real stages check between items |
| G8.3 | Watching a stream is not activity | A member who only watches a long run is signed out after an hour idle | Revisit with the web app (phase 13) |
| G8.4 | Deleting a project does not cancel its runs at once | A running stage continues until its next write, then ends without output | Acceptable: nothing it writes is readable; revisit if stages grow long silent stretches |
| G8.5 | `run_events` grows forever | Disk use grows with every screened paper | A retention or compaction rule, with the deployment guide (phase 14) |
| G8.6 | ~~No `RunManifest` yet~~ | Closed: `GET /runs/{run_id}/manifest` and the `manifest.json` export | Done |
| G8.7 | The HTTP test client buffers streams | Live streaming is specified below HTTP, not end to end | Playwright against a real server (phase 13) |

## Glossary

| Term | Meaning |
| --- | --- |
| **Review** | A question inside a project, with its status (§11.1). |
| **Run** | One background execution of some of a review's stages; its row is also its job in the queue. |
| **Stage** | One step of the pipeline (`queries`, `discover`, `ingest`, `mask`, `screen`, `export`, `answer`; `index` was removed, and runs stored with it still load), plugged in through `app/runs/base.py`. |
| **Worker** | The process (`python -m app.worker`) that claims queued runs and executes their stages. |
| **Lease** | A claim on a run that expires unless renewed; a dead worker's lease runs out and another worker takes over. |
| **`SKIP LOCKED`** | A Postgres option that makes a query pass over rows another transaction has locked, so workers never wait for each other. |
| **Gate** | A pause in a run until a person decides: confirm a vocabulary or approve masking. |
| **Server-Sent Events (SSE)** | A one-way stream of text events over plain HTTP, read in the browser by `EventSource`. |
| **`Last-Event-ID`** | The header `EventSource` sends on reconnecting: the id of the last event it saw. |
| **Keep-alive** | A comment line (`: keep-alive`) sent while a run is quiet so proxies do not close the connection. |
| **Signal** | A "look again" message on a channel; it carries no data, and every waiter also polls. |
| **Idempotent** | Safe to run again: a second run finds the first's work and does not repeat or duplicate it. |
| **Crypto-shredding** | Making data unreadable by destroying its key, wherever copies of the ciphertext remain. |
