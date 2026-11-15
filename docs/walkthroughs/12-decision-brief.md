# 12 — Decision brief

> **Milestones:** P1 of [UI_UX_SPEC §15](../UI_UX_SPEC.md#15-delivery-phases) · **Status:** in progress (built and tested on memory storage; not yet watched end to end against Ollama) · **Previous:** [11 — Discover, ingest and screening](11-screening.md) · **Next:** [13 — Web UI](13-web-ui.md)

Before this phase a run's last stage, `answer`, was a stand-in that reported a few empty agent steps. Now it writes a **decision brief** from the run's screened-in evidence: it masks the review's question with the project's rules, asks the deployment's own model to plan the options to compare, retrieves masked passages for each sub-question, has the model draft a brief that compares the options with cited claims, checks every citation in code against what was actually retrieved, and has the model critique the draft until it is accepted or the round limit is reached. Every step is streamed as `agent.step` and kept as the brief's trace. Any member can then read the brief, open each cited source with the claims that cite it, and see how the brief was made. This serves [`architecture.md` §4.2](../architecture.md#42-reviews) and §4.4, the CHITCHAT loop in [`dependencies.md` §3](../dependencies.md#3-ammore), and UI_UX_SPEC §6.9 to §6.11 and §8.

## What this phase delivers

- **The `answer` stage writes one brief per run.** Run again after a crash, it returns the brief already stored instead of writing a second one; a second run of the review writes version 2.
- **The loop is plain Python:** Planner (round 0), Retriever, then per round Writer, checks and Critic, and the Retriever again for what the Critic asked for. It stops when the Critic accepts a draft that passes the checks, at the round limit (default 3), or when a role answers invalidly twice in a row.
- **Only masked text reaches the model.** The question is masked with the question sanitiser and the project's masking rules; passages are the pipeline's masked abstracts; every call goes through `GuardedInference.with_rules`.
- **Code checks the citations.** A claim citing a passage that was never retrieved is reported as a problem, the Critic cannot accept past it, and the Writer is asked to fix it. Problems left on the kept draft are returned as `warnings`.
- **A brief never names a winner,** a rank or a total score, by construction.
- Endpoints: `GET /api/v1/reviews/{review_id}/brief?run_id=`, `GET /api/v1/briefs/{brief_id}/evidence/{tag}`, `GET /api/v1/briefs/{brief_id}/trace` ([`api.md`](../api.md#decision-briefs)).
- Setting: `HUMANI_T_BRIEF_MAX_ROUNDS` (1 to 10, default 3).
- Audit: `brief.created`, ids only.

### Data model

| Table | Columns | Notes |
| --- | --- | --- |
| `decision_briefs` (migration `0011`) | `id`, `project_id`, `review_id`, `run_id`, `version`, `created_at`, `model_id`, `rounds`, `kept_round`, `max_rounds`, `stopped_because`, `content_sealed`, `evidence_sealed`, `trace_sealed` | Unique `run_id` (one brief per run) and `(review_id, version)`. The three sealed columns are encrypted under the project's key and bound to the row. The app role has no `UPDATE`. |

## Architecture choices and why

### D12.1 A small state machine, not an agent framework

- **Decision:** the loop is `_Session.run` in `application/brief_loop.py`: one `GuardedInference` call each for Planner, Writer and Critic, each with a versioned prompt (`brief-planner/1`, `brief-writer/1`, `brief-critic/1`) and a JSON schema, at temperature 0. The Retriever and the checks are code. An invalid answer (schema or a domain rule) is asked for once more, with what was wrong; two in a row stop the loop.
- **Why:** [`dependencies.md` §3](../dependencies.md#3-ammore) chose the CHITCHAT loop over AMMORE's framework: every step is visible, testable with a scripted model, and reported as one `agent.step`.
- **Rejected:** `autogen-agentchat` (hides the steps and the prompts we must version); a Retriever that is a model call (one more call per round, and "which passages were retrieved" stops being something code decides).
- **Costs:** a new role means new code, not configuration.
- **Where:** [`application/brief_loop.py`](../../services/api/app/application/brief_loop.py).
- **Proven by:** `tests/test_briefs_loop.py`.

### D12.2 Citations are checked by code against what was retrieved

- **Decision:** `check_citations(brief, retrieved_tags)` reports every cited tag the Retriever never returned; `check_brief` adds a missing humanitarian criterion and an option rated on evidence with no cited claim. A Critic "accept" with problems is recorded as `overruled_by_checks` and the round continues.
- **Why:** a model can invent a plausible tag as easily as a plausible sentence (architecture §4.2: "Code, not the model, checks").
- **Rejected:** asking the Critic to check citations (same model, same blind spot); silently dropping bad citations (the brief would change without anyone seeing why).
- **Where:** [`domain/briefs.py`](../../services/api/app/domain/briefs.py).
- **Proven by:** `test_briefs_domain.py::TestCitationCheck`, `test_briefs_loop.py::TestChecksCatchInventedCitations`.

### D12.3 Which draft is kept

- **Decision:** the accepted draft; otherwise the best by no failing checks, then the Critic's scores (summed internally, never stored or shown), then the latest round. `kept_round` names it.
- **Why:** UI_UX_SPEC §6.11 shows "kept round 2 of 3"; a later draft is not always a better one.
- **Rejected:** always the last draft.
- **Proven by:** `TestCriticRevisions::test_without_an_accepted_draft_the_best_scored_round_is_kept`, `…_a_draft_without_problems_is_kept_over_a_better_scored_one_with_problems`.

### D12.4 BM25 in process for P1 ([ADR-0007](../adr/0007-bm25-retrieval-for-the-brief.md))

- **Decision:** a `Retriever` Protocol with an Okapi BM25 implementation over the run's included passages; at most 4 passages per search, 6 searches per step and 40 passages in all, so the prompt fits a local model.
- **Rejected:** Milvus through mmore now; pgvector. See the ADR.
- **Where:** [`briefs/base.py`](../../services/api/app/briefs/base.py), [`briefs/bm25.py`](../../services/api/app/briefs/bm25.py).
- **Proven by:** `tests/test_briefs_retrieval.py`.

### D12.5 One sealed row per run, written once

- **Decision:** `decision_briefs` holds ids and loop facts in the clear and three sealed columns: the content (masked question, body, caveat, warnings, Critic scores), the evidence (passages the Writer saw, with the search that found each and what the pipeline knew of each source, including the screening rationale) and the trace (every `AgentStep`, the manifest). Storing is conditional on the run having no brief (`ON CONFLICT DO NOTHING`).
- **Why:** "could a reviewer reconstruct this six months from now?" The brief keeps the exact passages and source details as they were, not a pointer to rows that may change.
- **Rejected:** claim and citation rows in their own tables: every one holds text, so each would be sealed and none could be queried anyway.
- **Where:** [`application/briefs.py`](../../services/api/app/application/briefs.py), [`storage/postgres/briefs.py`](../../services/api/app/storage/postgres/briefs.py), [`storage/memory_briefs.py`](../../services/api/app/storage/memory_briefs.py).
- **Proven by:** `test_briefs_stage.py::TestStoredSafely`, `TestRunningAgain`, `test_briefs_storage_contract.py`.

### D12.6 Converged on the pipeline's `Passage` and the mask lane's `Sanitizer`

- **Decision:** evidence comes through `EvidenceSource` and `Passage` in `domain/screening.py` (`ScreenedEvidence` in production); the question is masked by the question `Sanitizer` with `MaskingRuleService.rules_for`. Source details come through this lane's `SourceCatalogue` (`ScreenedSources` reads the paper record and the run's screening decision).
- **Rejected:** a separate passage type and masking seam for the brief (two contracts for one thing).

### D12.7 The honesty rule is in the answer format; a retry from Brief uses the last screening (2026-11-08)

- **Decision:**
  - A claim's format is one of two shapes: evidence that cites at least one passage, or a hypothesis, the model's own reasoning, that cites none. What a conflict means can only be a hypothesis or nothing. The follow-up answer loop shares the claim format.
  - A run that did not screen (a retry from Brief) takes its included papers from the newest earlier run of the review that screened the same paper set (`PaperStore.screened_by`).
- **Why:**
  - Left to the domain check alone, `gpt-5-mini` broke the rule twice in a row ("Claim.citations must be empty for a hypothesis"; "Conflict.meaning is the model's reasoning"), and the brief failed. In the format, OpenAI's strict mode makes the rule impossible to break; the domain check stays as the guard.
  - Decisions belong to the run that made them (ADR 0006), so a retry from Brief found none and said "No papers were included", although screening had included three.
- **Where:** [`app/application/brief_loop.py`](../../services/api/app/application/brief_loop.py) (`EVIDENCE_CLAIM_SCHEMA`, `HYPOTHESIS_CLAIM_SCHEMA`), [`app/application/papers.py`](../../services/api/app/application/papers.py) (`screened_by`).
- **Proven by:** `tests/test_briefs_loop.py::TestWriterFormat`, `tests/test_pipeline_stages.py::TestEvidence::test_a_run_that_did_not_screen_uses_the_newest_screening_of_its_papers`.

## Libraries

No new library. BM25 is about forty lines of standard-library Python ([ADR-0007](../adr/0007-bm25-retrieval-for-the-brief.md)).

## Files to read, in order

From the rules outwards:

1. [`app/domain/briefs.py`](../../services/api/app/domain/briefs.py) — the brief's types, what `__post_init__` refuses, `check_citations` and `check_brief`, `AgentStep.to_payload`.
2. [`app/briefs/bm25.py`](../../services/api/app/briefs/bm25.py) — the retriever; note ties keep corpus order.
3. [`app/application/brief_loop.py`](../../services/api/app/application/brief_loop.py) — prompts, schemas, `_Session.run` (the state machine) and `_call` (one step, its status and its error).
4. [`app/application/briefs.py`](../../services/api/app/application/briefs.py) — `AnswerStage.run` (read, mask, loop, store once) and `BriefService`.
5. [`app/api/routes/briefs.py`](../../services/api/app/api/routes/briefs.py) and [`app/api/schemas/briefs.py`](../../services/api/app/api/schemas/briefs.py) — the response shapes.
6. [`migrations/versions/20261105_0011_briefs.py`](../../services/api/migrations/versions/20261105_0011_briefs.py).

## Set up

Beyond [11](11-screening.md): a model server the worker can reach (`HUMANI_T_INFERENCE_URL`, `HUMANI_T_INFERENCE_MODEL`), and `alembic upgrade head`.

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
$env:HUMANI_T_PAPER_SOURCES = 'openalex'
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In a second window, the worker with the same variables: `uv run --env-file .env python -m app.worker`.

Have the project, review and accepted plan of T11.1, with Amara (owner), Maya (viewer) and Tariq (not a member).

## Manual tests

### T12.1 Write a brief

- **Do:** As Amara, `POST {{base}}/reviews/{{review}}/runs` with `{"stages":["discover","ingest","mask","screen","answer"]}`; follow `GET {{base}}/runs/{{run}}/events` until `job.finished`.
- **Expect:** under `answer`, `agent.step` events in order: `planner` (round 0), `retriever` (round 0, `queries`, `passages_new`), then `writer`, `checks` (with `problems`), `critic` per round, and `retriever` again when the Critic asked for searches. `stage.finished` has `brief_id`, `version: 1`, `stopped_because` and a `manifest` with `model_ids` and `prompt_versions` per role and `retriever: "bm25-okapi/1"`. `job.finished` has `outcome: "done"`.
- **Proves:** D12.1, D12.4.
- **If it fails:** "No papers were included in this run…": screening included nothing; check `GET …/papers`. "The model server isn't answering…": the worker cannot reach `HUMANI_T_INFERENCE_URL`.

### T12.2 Read the brief

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T12.2.1 | As Maya: `GET {{base}}/reviews/{{review}}/brief` | `200`; `version: 1`; 2 to 4 `options`; every claim with `support: "evidence"` has citations, every `hypothesis` none; `caveat` is the fixed text; `question` shows placeholders such as `[PERSON_1]` where the question named someone | Viewers read; masked question (D12.6) | `404 "This review has no decision brief yet."`: T12.1 did not finish |
| T12.2.2 | Search the body for `winner`, `rank`, `recommend`, `total` | none | D12.2's rule | Any is a bug |
| T12.2.3 | `GET …/brief?run_id={{run}}` | the same brief | Per-run access | |
| T12.2.4 | `GET …/brief?run_id=00000000-0000-4000-8000-000000000000` | `404` | | |
| T12.2.5 | As Tariq: T12.2.1, then with review id `00000000-0000-4000-8000-000000000000` | `404` both, identical bodies | §5.5 | |

### T12.3 Open a source

- **Do:** take a tag from `sources` (e.g. `P3`) and the brief's `id`; `GET {{base}}/briefs/{{brief}}/evidence/P3`.
- **Expect:** `200`; `passages[0].text` is masked text with `found_by` (a search phrase) and `round`; `source.title` and `source.rationale` (why screening included it); `cited_for` lists every claim citing `P3` with its `section` and `option`. `GET …/evidence/P9999` gives `404`.
- **Proves:** D12.5; UI_UX_SPEC §6.10.

### T12.4 How it was made

- **Do:** `GET {{base}}/briefs/{{brief}}/trace`.
- **Expect:** `200`; `steps` equal the `agent.step` events of T12.1, in order; `totals.calls` counts the model calls; `stopped_because` and `kept_round` match the brief.
- **Proves:** D12.1, D12.3.

### T12.5 Stored sealed and written once

- **Do:** pgAdmin (`humani_t`): `SELECT run_id, version, stopped_because, content_sealed FROM decision_briefs;` then, as `humani_t_app`, `UPDATE decision_briefs SET version = 9;`.
- **Expect:** one row per run; `content_sealed` is unreadable bytes (no question or option text); the update is refused with `permission denied`.
- **Proves:** D12.5.

### T12.6 The loop's edge cases, end to end

- **Do:** `uv run pytest tests/test_briefs_loop.py tests/test_briefs_stage.py -v`.
- **Expect:** all pass: an invented citation caught and revised, invalid JSON retried then stopped, a Critic rejecting then accepting, the round limit, a refused prompt, an unreachable server, a crash before and after the brief is stored, a cancel mid-loop.
- **Proves:** D12.1 to D12.5 with a scripted model, cases a real model will not produce on demand.

### T12.7 Instructions for the model ([ADR-0015](../adr/0015-project-instructions-are-added-not-replacing.md))

Run `alembic upgrade head` first (revision `0017`). Have a masking rule "Kofi Mensah" ready to add in step 4, not before.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T12.7.1 | As Amara: `GET {{base}}/projects/{{project}}/model-instructions` | `200` `{"current": null, "versions": []}` | Nothing saved means no instructions | `404`: the route is missing; restart uvicorn |
| T12.7.2 | As Amara: `PUT …/model-instructions` with `{"screening": "Treat studies in refugee camps as displaced settings. Ask whether Kofi Mensah ran the study.", "writing": "Write for a field manager: short sentences, no jargon.", "based_on_version": null}` | `200`; `version: 1`, both texts, `saved_by.display_name` is Amara's name | Owners save; version 1 | `422`: a masking rule already holds "Kofi Mensah"; remove it and retry |
| T12.7.3 | Send T12.7.2 again unchanged (`based_on_version: null`) | `409` "The record changed while you were editing it…"; still one version | A stale save never overwrites | |
| T12.7.4 | As Amara: `POST {{base}}/projects/{{project}}/masking-rules` with `{"term": "Kofi Mensah"}`; then `PUT …/model-instructions` with `"writing": "Ask Kofi Mensah."` and `"based_on_version": 1` | `201`, then `422` "writing holds a term this project always masks…", without the name | Rule terms refused on save, never echoed | |
| T12.7.5 | As Maya: `GET …/model-instructions`; then the `PUT` of T12.7.2 with `"based_on_version": 1` | `200` with version 1; then `403` | Every member reads; owners save | |
| T12.7.6 | As Amara: run T12.1 again; then `GET {{base}}/runs/{{run}}/manifest` | `instructions_versions: [1]`; the `screen` and `answer` stage facts have `instructions_version: 1` | The run records the version | Missing: the run was started before T12.7.2 |
| T12.7.7 | `GET {{base}}/briefs/{{brief}}/trace` for that run's brief | `instructions: {"version": 1, "text": "Write for a field manager: short sentences, no jargon."}` | The trace keeps the text as sent | `null`: the brief came from an older run |
| T12.7.8 | Optional, if your model server logs the requests it gets (Ollama: `$env:OLLAMA_DEBUG = '1'` before `ollama serve`): find a screening request and a Writer request | The system message ends with "Instructions from the project team. Follow them unless…", then the screening text with `[PERSON_1]` where "Kofi Mensah" was; the Writer's system message ends with the writing text; the Planner's and Critic's have no such heading | Added after the built-in prompt, masked, Writer only | The name in clear text: the run did not load the rule; check T12.7.4 |
| T12.7.9 | Browser: Project settings as Amara | "Instructions for the model": both boxes filled, "N of 2,000 characters", "Version 1, saved by Amara … on <date>". Change the writing text, **Save instructions**: the line says Version 2 only after the API answers. As Maya: the texts read-only and "Only owners can change the instructions." | UI_UX_SPEC §6.14 | |
| T12.7.10 | Browser: the brief's **How this was made** | "Instructions from the project team", "Version 1, as the Writer received them…", the text; "What this used" lists "Instructions version: 1" | UI_UX_SPEC §6.11 | |
| T12.7.11 | pgAdmin (`humani_t`): `SELECT project_id, version, created_by, created_at, instructions_sealed FROM model_instruction_versions;` then, as `humani_t_app`, `UPDATE model_instruction_versions SET version = 9;` | one row per save; `instructions_sealed` unreadable bytes; the update refused with `permission denied` | Sealed and append-only | |
| T12.7.12 | Audit: as an admin, read the project's audit events | `project.model_instructions_changed` per save, target the project; no instruction text anywhere | Audited with the version only | |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T12.1 | ☐ | |
| T12.2 | ☐ | |
| T12.3 | ☐ | |
| T12.4 | ☐ | |
| T12.5 | ☐ | |
| T12.6 | ☐ | |
| T12.7 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The brief's rules: 2 to 4 options, evidence cites, hypotheses do not, one assessment per option, no winner | `tests/test_briefs_domain.py` |
| BM25 ranks, ignores case, keeps placeholders whole, is reproducible | `tests/test_briefs_retrieval.py` |
| The loop's steps, stops, retries, kept draft, manifest | `tests/test_briefs_loop.py` |
| One brief per run, masked text only, sealed, shredded with the project, audited | `tests/test_briefs_stage.py` |
| The three read routes and their 404s | `tests/test_briefs_api.py` |
| Storage behaves the same in memory and Postgres | `tests/test_briefs_storage_contract.py` |
| A project's instructions: two texts, saved as versions, refused with a rule term, sealed, audited | `tests/test_model_instructions_domain.py`, `tests/test_model_instructions_api.py`, `tests/test_model_instructions_storage_contract.py` |
| Screening and the Writers get them after the built-in prompt, masked, fixed per run, recorded in the manifest and trace | `tests/test_model_instructions_in_prompts.py` |

```powershell
uv run pytest tests/test_briefs_domain.py tests/test_briefs_retrieval.py tests/test_briefs_loop.py tests/test_briefs_stage.py tests/test_briefs_api.py tests/test_briefs_storage_contract.py -v
uv run pytest tests/test_model_instructions_domain.py tests/test_model_instructions_api.py tests/test_model_instructions_storage_contract.py tests/test_model_instructions_in_prompts.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G12.1 | Not watched end to end against Ollama yet | Prompt wording may need tuning for small local models | Run T12.1 after integration |
| G12.2 | Retrieval is lexical, without stemming | "price" does not find "prices" | A vector store behind `Retriever` ([ADR-0007](../adr/0007-bm25-retrieval-for-the-brief.md)) |
| G12.3 | ~~No "no new evidence and no better score" stop~~ | Closed in [15](15-exports-and-questions.md): `stopped_because: no_progress` | Done |
| G12.4 | ~~Team documents (`D` tags) are not passages yet~~ | Closed in [16](16-documents.md): approved documents are evidence for the brief | Done |
| G12.5 | Brief versions are not compared, and "your view first" is not stored | UI_UX_SPEC §6.9 P3 parts | P3 |
| G12.6 | ~~`decision_briefs` revision 0011 branches from 0007~~ | Resolved: re-chained after `0010` | Done |

## Glossary

| Term | Meaning |
| --- | --- |
| **Decision brief** | The comparison of options a run writes: options, trade-offs, disagreements, considerations, gaps and a bottom line, each as claims. |
| **Claim** | One statement: evidence (cites passages) or a hypothesis (the model's reasoning, cites nothing). |
| **Passage** | The masked text a claim is drawn from, tagged `P<n>`. |
| **Agent step** | One step of the loop: round, role, status and what that role did. The trace is all of them. |
| **Kept round** | The round whose draft became the brief. |
| **BM25** | A standard way to rank texts by the query words they share, weighting rare words more. |
