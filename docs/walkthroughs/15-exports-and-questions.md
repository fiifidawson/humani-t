# 15 — Exports and follow-up questions

> **Milestones:** P2 of [UI_UX_SPEC §15](../UI_UX_SPEC.md#15-delivery-phases) · **Status:** in progress (built and tested; answers have not been watched against Ollama) · **Previous:** [12 — Decision brief](12-decision-brief.md) · **Next:** [16 — Documents](16-documents.md)

Before this phase a member could read a review's papers and its brief only on screen, and could not ask anything about them. Now any member can download what the screens show: the screening dataset as CSV or JSON lines, the included papers as RIS for a reference manager, the PRISMA counts, the run manifest, and the brief as Markdown, JSON or a print-ready page the browser saves as PDF. Reviewers and owners can ask follow-up questions in threads; each question is masked before it is stored, answered in the background from the review's own evidence by the brief's machinery, and every claim in the answer cites a passage that was actually retrieved. The brief loop also gains the "no new evidence and no better score" stop that UI_UX_SPEC §6.9 names. This serves UI_UX_SPEC §6.8, §6.9 (downloads), §6.12 and §5.3.9 (ask), [`architecture.md` §4.2](../architecture.md#42-reviews), §5.3 and §5.7.

## What this phase delivers

- **Exports** at `GET /api/v1/reviews/{review_id}/exports/{name}?run_id=`: `dataset.csv`, `dataset.jsonl`, `included.ris`, `prisma.md`, `manifest.json`, `brief.md`, `brief.json`, `brief.html` ([`api.md`](../api.md#exports)). Members only, audited, `Cache-Control: no-store`, an attachment name built from ids only, CSV formula injection neutralised.
- **Follow-up questions** in threads: `POST /api/v1/reviews/{review_id}/questions`, `GET /api/v1/reviews/{review_id}/threads`, `GET /api/v1/threads/{thread_id}`, `GET /api/v1/questions/{question_id}` ([`api.md`](../api.md#follow-up-questions)).
- **The worker answers questions** beside its runs (`app.worker`), under a lease.
- **The brief loop stops on `no_progress`:** a round that did no better than the kept draft, with no new passage from the Critic's searches.
- Audit: `export.downloaded` and `question.asked`, ids only.

### Data model

| Table | Columns | Notes |
| --- | --- | --- |
| `question_threads` (migration `0014`) | `id`, `project_id`, `review_id`, `created_at`, `created_by` | One line of questioning about one review. |
| `questions` (migration `0014`) | `id`, `thread_id`, `project_id`, `review_id`, `position`, `asked_by`, `asked_at`, `status`, `question_sealed`, `attempts`, `lease_owner`, `lease_expires_at`, `answered_at`, `model_id`, `answer_sealed`, `error`, `version` | Unique `(thread_id, position)`. Also the queue: a partial index on waiting questions. Both sealed columns are under the project's key, bound to the row. |
| `decision_briefs` (changed by `0014`) | `stopped_because` | Now also `no_progress`. |

## Architecture choices and why

### D15.1 Exports are generated on request, never stored

- **Decision:** each export is built when it is asked for, through the same use cases (`PaperService`, `BriefService`) and the same response models (`PaperOut`, `PrismaOut`, `ManifestOut`, `BriefOut`) the screens read.
- **Why:** a file then holds exactly what the screen shows, masked where the screen is masked, and nothing is stored twice: deleting the project leaves no export to shred. The data is small (a few hundred rows), so building it costs milliseconds.
- **Rejected:** storing files at the `export` stage (a second copy of project data, sealed or not, that can drift from what was relabelled since); a background export job (no export is slow enough to need one).
- **Costs:** a large review is re-read on every download; the dataset reads one page per transaction, so no connection is held for long.
- **Where:** [`application/exports.py`](../../services/api/app/application/exports.py), [`api/exports.py`](../../services/api/app/api/exports.py), [`api/routes/exports.py`](../../services/api/app/api/routes/exports.py).
- **Proven by:** `tests/test_exports_api.py` (each JSON export equals its screen's route).

### D15.2 A safe download

- **Decision:** CSV cells starting with `=`, `+`, `-`, `@`, a tab or a carriage return get a leading `'` (OWASP "CSV Injection"); the filename is `humani-t-run-<8 hex>-<name>`, checked against `[A-Za-z0-9._-]`; the print page escapes every text and has no script; every response is an attachment with `no-store` and `nosniff`.
- **Why:** paper titles and model rationales are data from outside; a spreadsheet would run a formula, and a title in a filename could break the header.
- **Rejected:** a server-side PDF engine (a large native dependency with a history of vulnerabilities, for what the browser's print does already); putting the review's title in the filename (it can reveal the operation).
- **Where:** [`domain/exports.py`](../../services/api/app/domain/exports.py).
- **Proven by:** `tests/test_exports_domain.py`, `test_exports_api.py::TestBrief::test_the_print_brief_escapes_the_models_text`.

### D15.3 Questions are answered in the background

- **Decision:** asking stores the question and returns `202`; the worker claims it from the `questions` table under a 10-minute lease, renewed after each agent step, and the browser polls `GET /questions/{id}`. A lapsed lease is claimed once more, then the question fails in plain words.
- **Why:** an answer is up to four model calls, minutes on a local model (UI_UX_SPEC §6.11 shows two minutes for one Writer step). Holding a request that long ties up a connection through the proxy and breaks on any timeout.
- **Rejected:** answering inside the request; running questions as runs (a run is a review's stages, and one run at a time per review would block questions during a run); a Server-Sent Events stream for answers (polling a single row is enough for one status, and the run stream already exists for the brief).
- **Costs:** an answer waits for a free worker; workers answer one question at a time.
- **Where:** [`application/questions.py`](../../services/api/app/application/questions.py) (`QuestionWorker`), [`app/worker.py`](../../services/api/app/worker.py).
- **Proven by:** `test_questions_api.py::TestTheQueue`, `tests/test_questions_storage_contract.py`.

### D15.4 The answer reuses the brief's machinery

- **Decision:** `LoopRunner` (model steps, BM25 retrieval, the trace) and `revise_until_done` (Writer, checks and Critic per round, with the brief's stopping rules) were extracted from the brief loop; the answer loop supplies only its prompts (`answer-writer/1`, `answer-critic/1`) and its schema (up to six claims). There is no Planner: the question is the search.
- **Why:** the task asked for no duplication, and one implementation of "cite only what was retrieved" is one place to get it right.
- **Rejected:** a single-call answer (no check and no revision of invented citations).
- **Where:** [`application/brief_loop.py`](../../services/api/app/application/brief_loop.py), [`application/answer_loop.py`](../../services/api/app/application/answer_loop.py).
- **Proven by:** `test_questions_api.py::TestAnswering::test_a_claim_citing_a_passage_never_retrieved_is_sent_back_for_revision`.

### D15.5 What a follow-up carries

- **Decision:** the thread's last three questions and answers, as stored (masked question, answer claims with their tags), and the previous question as a second search. A thread takes one open question at a time.
- **Why:** a follow-up ("and in larger towns?") means nothing without the turn before; three turns keep the prompt small; one at a time means a follow-up always reads the answer before it. Everything carried is masked already, so nothing new is exposed.
- **Rejected:** the whole thread (unbounded prompt); no context (follow-ups would not work).
- **Proven by:** `test_questions_api.py::TestThreads`.

### D15.6 Evidence for an answer

- **Decision:** the included passages of the run that wrote the review's latest brief, or of its newest run with any, plus the passages that brief cited. With nothing retrieved, the answer is "Nothing in this project answers that." and no model is asked.
- **Why:** UI_UX_SPEC §6.12: "Answers use only the papers and documents in this project."
- **Proven by:** `TestAnswering::test_nothing_relevant_is_an_answer_that_says_so_without_asking_the_model`.

### D15.7 The brief stops on no progress

- **Decision:** after a round the Critic did not accept, if the draft did no better than the kept one and the Critic's searches found no new passage, the loop stops with `no_progress`.
- **Why:** another round could only rewrite the same evidence. UI_UX_SPEC §6.9 lists the stop ("no new evidence and no better score").
- **Proven by:** `test_briefs_loop.py::TestNoProgress`.

## Libraries

No new library: `csv`, `html` and `json` from the standard library.

## Files to read, in order

From the rules outwards:

1. [`app/domain/exports.py`](../../services/api/app/domain/exports.py) — the export list, `neutralise_cell`, the safe filename.
2. [`app/domain/questions.py`](../../services/api/app/domain/questions.py) — a question's lifecycle and lease, `Answer`, `check_answer`.
3. [`app/application/brief_loop.py`](../../services/api/app/application/brief_loop.py) — `LoopRunner` and `revise_until_done`, now shared.
4. [`app/application/answer_loop.py`](../../services/api/app/application/answer_loop.py) — the answer's prompts and loop.
5. [`app/application/questions.py`](../../services/api/app/application/questions.py) — `QuestionService.ask` (mask, then store) and `QuestionWorker`.
6. [`app/application/exports.py`](../../services/api/app/application/exports.py) and [`app/api/exports.py`](../../services/api/app/api/exports.py) — gathering and rendering.
7. [`migrations/versions/20261105_0014_questions.py`](../../services/api/migrations/versions/20261105_0014_questions.py).

## Set up

Beyond [12](12-decision-brief.md): once integration has re-chained the migrations, migrate, then run the API and a worker (the worker answers questions).

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In a second window: `uv run --env-file .env python -m app.worker`.

Have the review of T12.1 with a brief, Amara (owner), Kofi (reviewer), Maya (viewer) and Tariq (not a member), each signed in to Postman.

## Manual tests

### T15.1 Download the dataset

- **Do:** As Maya, `GET {{base}}/reviews/{{review}}/exports/dataset.csv`. Open the file in a spreadsheet.
- **Expect:** `200`; `Content-Disposition: attachment; filename="humani-t-run-xxxxxxxx-dataset.csv"`; `Cache-Control: no-store`; one row per paper in the screening table's order; any rationale starting with `=`, `+`, `-` or `@` shows a leading `'` and is not evaluated.
- **Proves:** D15.1, D15.2.
- **If it fails:** `404 "This review has no papers yet."` means no run discovered papers.

### T15.2 The other downloads

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T15.2.1 | `GET …/exports/dataset.jsonl` | One JSON object per line, each equal to a row of `GET …/papers` | D15.1 | |
| T15.2.2 | `GET …/exports/included.ris`; import it into Zotero | Only included papers, with title, year, DOI and URL | | |
| T15.2.3 | `GET …/exports/prisma.md` | A table with the numbers of `GET …/prisma` | D15.1 | |
| T15.2.4 | `GET …/exports/manifest.json` | The body of `GET /runs/{run}/manifest` | D15.1 | |
| T15.2.5 | `GET …/exports/brief.md` and `brief.json` | The caveat, options, trade-offs and sources; the question shows placeholders | D15.1 | `404` means no brief yet |
| T15.2.6 | `GET …/exports/brief.html`, open it in a browser, print to PDF | Every page has the caveat in its footer | D15.2 | |
| T15.2.7 | `GET …/exports/passwords.txt` | `404` | | |
| T15.2.8 | As Tariq, `GET …/exports/dataset.csv`, then with review id `00000000-0000-4000-8000-000000000000` | `404` both, identical bodies | §5.5 | |

### T15.3 Ask a question

- **Do:** As Kofi, `POST {{base}}/reviews/{{review}}/questions` with `{"question": "Do cash transfers raise food prices near Marib?"}` (use a place your masking detects, or add `Marib` as a project masking rule first).
- **Expect:** `202`; `status: "pending"`; `question_sent` shows `[LOCATION_1]`; `masked` is `{"LOCATION": 1}`; a `Location` header. Then poll `GET {{base}}/questions/{{question}}` until `status` is `answered`: `answer.claims` cite tags listed in `answer.sources`, each with its masked passage; `answer.steps` shows retriever, writer, checks and critic.
- **Proves:** D15.3, D15.4.
- **If it fails:** stuck at `pending` means no worker runs; `failed` with "The model server isn't answering" means the worker cannot reach the model.

### T15.4 Follow up in the thread

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T15.4.1 | Ask again with `thread_id` from T15.3 while it is still `pending` | `409` | One open question per thread | |
| T15.4.2 | After it is answered, ask `"And in larger towns?"` with the same `thread_id` | `202`, `position: 2` | D15.5 | |
| T15.4.3 | `GET {{base}}/reviews/{{review}}/threads`, then `GET {{base}}/threads/{{thread}}` | Newest thread first; the thread's questions in order | | |
| T15.4.4 | As Maya, ask a question | `403` | Viewers read only | |
| T15.4.5 | Ask `"What do zebra crossings cost?"` | Answered with `nothing_found: true` and "Nothing in this project answers that." | D15.6 | |

### T15.5 Stored sealed

- **Do:** pgAdmin (`humani_t`): `SELECT status, question_sealed, answer_sealed FROM questions;`
- **Expect:** unreadable bytes; no question or answer text, no place name.
- **Proves:** D15.3, §5.4.

### T15.6 Everything above, automated

- **Do:** `uv run pytest tests/test_exports_domain.py tests/test_exports_api.py tests/test_questions_domain.py tests/test_questions_api.py tests/test_questions_storage_contract.py tests/test_briefs_loop.py -v`
- **Expect:** all pass (the Postgres half of the contract skips without `TEST_DATABASE_URL`).
- **Proves:** cases a real model will not produce on demand: invented citations, a dead worker twice, an unreachable model.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T15.1 | ☐ | |
| T15.2 | ☐ | |
| T15.3 | ☐ | |
| T15.4 | ☐ | |
| T15.5 | ☐ | |
| T15.6 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Formula cells are neutralised; filenames are safe; every listed export exists | `tests/test_exports_domain.py` |
| Each export holds what its screen shows, is audited, never cached, 404 for non-members | `tests/test_exports_api.py` |
| A question's lifecycle, lease and answer rules | `tests/test_questions_domain.py` |
| Asking, answering, threads, the queue, sealing and shredding | `tests/test_questions_api.py` |
| Storage behaves the same in memory and Postgres | `tests/test_questions_storage_contract.py` |
| The brief's `no_progress` stop | `tests/test_briefs_loop.py::TestNoProgress` |

```powershell
uv run pytest tests/test_exports_domain.py tests/test_exports_api.py tests/test_questions_domain.py tests/test_questions_api.py tests/test_questions_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G15.1 | No streamed progress for an answer, only its status | The UI shows "answering" without the step in progress | Poll is enough for P2; persist steps as they happen if needed |
| G15.2 | "Add to brief notes" (UI_UX_SPEC §5.3.9) | Not in the API | Needs a notes model; P3 |
| G15.3 | ~~Team documents (`D` tags) are not yet evidence for answers~~ | Closed in [16](16-documents.md): approved documents are evidence for answers | Done |
| G15.4 | Questions are answered one at a time per worker | A busy review waits | Run more workers |
| G15.5 | ~~Migration 0014 branches from 0011~~ | Resolved: 0014 follows 0013 | Done |

## Glossary

| Term | Meaning |
| --- | --- |
| **Export** | A file made from what a screen shows, on request. |
| **CSV injection** | A spreadsheet running a cell that starts with `=` as a formula; prevented by a leading `'`. |
| **Thread** | A line of follow-up questions about one review. |
| **Lease** | A worker's claim on a question for a while; if the worker dies, it lapses and another worker answers. |
