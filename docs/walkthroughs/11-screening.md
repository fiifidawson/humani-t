# 11 — Discover, ingest and screening

> **Milestones:** P1 of [UI_UX_SPEC §15](../UI_UX_SPEC.md#15-delivery-phases) · **Status:** in progress (built and tested; the mask store and the brief stage are still to be wired to it) · **Previous:** [09 — Plan and discover](09-plan-and-discover.md) · **Next:** `12-decision-brief` (planned)

Before this phase a run's Discover, Ingest, Screen and Export stages were stand-ins that counted to five. Now a run searches every paper source the operator switched on with the review's accepted plan, merges what they return into one list of distinct papers, keeps each paper's title and abstract for the mask stage, sends each paper's **masked** text to the deployment's own model with the accepted criteria, and stores one `ScreeningDecision` per paper with the model id, prompt version and criteria version that produced it. Export counts the run PRISMA-style. Members can list a run's papers with their decisions, open one, see the PRISMA counts and read the run manifest. Everything is sealed under the project's key, so deleting the project shreds it. This answers the provenance test in [`CLAUDE.md`](../../CLAUDE.md) ("could a reviewer reconstruct why this paper was included or excluded, six months from now?"), [`architecture.md` §3.8](../architecture.md#38-screening) and [§4.2](../architecture.md#42-reviews), and the screening parts of UI_UX_SPEC [§5.3.7](../UI_UX_SPEC.md#537-screening-summary), [§6.7](../UI_UX_SPEC.md#67-run-progress) and [§6.8](../UI_UX_SPEC.md#68-screening-results).

## What this phase delivers

- **Discover** searches every enabled source with the accepted plan's compiled query, one progress row per source, merges duplicates by DOI (or title and year), and stores the run's papers in their merged order. A source that fails is recorded and shown by its id, and the others carry on; the stage fails only when every source fails.
- **A paper is stored once per review.** A later run that finds it again gets the same paper id, so runs can be compared paper by paper.
- **Ingest** keeps each paper's title and abstract (or the title alone) for the mask stage. Full text is a recorded gap.
- **Screen** sends each paper's masked text, never raw text, through `GuardedInference`, at most two papers at a time by default. It stores label, priority, score, rationale, the criteria met and the exclusion reasons, with full provenance. One progress row per paper shows its label and a one-line reason.
- **A malformed answer is asked again once.** A second failure is recorded as `uncertain` with the reason "the model's answer could not be read".
- **Every stage is safe to repeat:** a crash, a deploy or a retry never searches twice, duplicates a paper or re-screens a decided one.
- **A run started without Discover** (for example "try again from Screen") works from the review's newest discovery.
- **Export** puts the PRISMA counts and a dataset summary in its stage summary. Downloadable files are P2.
- **Endpoints:** `GET /api/v1/reviews/{review_id}/papers`, `GET /api/v1/reviews/{review_id}/papers/{paper_id}`, `GET /api/v1/reviews/{review_id}/prisma`, `GET /api/v1/runs/{run_id}/manifest` ([`api.md`](../api.md#papers-screening-and-the-manifest)).
- **For the brief stage:** `ScreenedEvidence`, the `EvidenceSource` that yields included papers' masked passages tagged `P<place>`.
- **Settings added:** `HUMANI_T_PAPERS_PER_SOURCE` (default 25), `HUMANI_T_SCREENING_CONCURRENCY` (default 2).
- **Migration** `0009`, after `0008` (plans).

### Data model

| Table | Holds | Sealed (project key, bound to the row) | In the clear |
| --- | --- | --- | --- |
| `papers` | one row per paper per review | `record_sealed`: the whole `PaperRecord` | `identity_digest` (HMAC of the identity), `source_id`, `first_run_id`, dates |
| `run_paper_sets` | which run's discovery a run works from | — | `run_id`, `basis_run_id` |
| `run_papers` | a run's merged list | — | `position`, `found_by` (source ids) |
| `run_sources` | each source a run asked | `query_sealed`: the rendered query | `outcome`, `found`, `plan_version`, `vocabulary_version` |
| `paper_texts` | the text kept for masking | `text_sealed` | `kind` (`abstract` or `title`) |
| `screening_decisions` | one per run and paper | `rationale_sealed` | label, priority, score, criterion ids, model id, prompt version, criteria version, `evidence_kind`, `is_unreadable`, `decided_at` |

The app role has no `UPDATE` on `screening_decisions`: a decision is provenance and is never rewritten.

## Architecture choices and why

### D11.1 Discover compiles its query from the accepted plan

- **Decision:** `DiscoverStage` reads the accepted plan through the plan lane's `PlanSource` and compiles it with `compile_vocabulary`. It does not read the `queries` stage's summary. It stores the plan version, vocabulary version and rendered query with each source's outcome.
- **Why:** the plan is typed and versioned, and compiling it is pure, so the same plan always gives the same query. A run may start without `queries`. Parsing another stage's JSON summary would make Discover depend on a format it does not own.
- **Rejected:** reading `summary["manifest"]["queries"]` from the run's `queries` event. That fails for runs that skip Queries and breaks silently if the summary changes shape.
- **Costs:** if the plan changed between the two stages of one run, the stages would disagree. Planning is refused while a run is going, so this cannot happen, and if it did the manifest shows both versions.
- **Where:** [`app/application/pipeline.py`](../../services/api/app/application/pipeline.py).
- **Proven by:** `tests/test_pipeline_stages.py::TestDiscover::test_every_source_is_searched_with_the_accepted_plans_query`, `::test_the_manifest_records_what_was_asked_of_whom`.

### D11.2 One transaction, guarded by a conditional write, per stage's work

- **Decision:** Discover searches first, with no transaction open. It then stores the paper set, papers, run papers and source outcomes in one transaction that begins with `add_set`, which inserts only if the run has no set yet. A repeat finds the set and rebuilds its summary from storage. Ingest's `add_text` and Screen's `screening.add` are conditional in the same way, so a stage repeated after a crash never duplicates or overwrites.
- **Why:** the stage seam's first rule (a stage may run more than once) and [§5.7](../architecture.md#57-concurrency-and-resources) (no connection held across HTTP or model calls; no blind overwrites).
- **Rejected:** storing each source's results as they arrive (a crash between sources leaves a half-merged list whose counts are wrong); deleting and rewriting a run's papers on repeat (a blind overwrite, and it loses decisions keyed to them).
- **Costs:** a crash after searching and before committing repeats the search.
- **Where:** [`pipeline.py`](../../services/api/app/application/pipeline.py), [`storage/postgres/papers.py`](../../services/api/app/storage/postgres/papers.py).
- **Proven by:** `TestDiscover::test_a_repeated_discover_searches_nothing_again_and_stores_nothing_twice`, `TestScreen::test_a_rerun_skips_papers_already_decided`, `tests/test_papers_storage_contract.py`.

### D11.3 A paper's identity is an HMAC, its record is sealed

- **Decision:** a paper is found again by `identity_digest`, an HMAC-SHA256 of `PaperRecord.identity` under a subkey of the project's data key. The record (title, abstract, DOI, link) is sealed and bound to the paper's row.
- **Why:** which papers a review found reveals what it is about. The DOI in the clear would let a database copy reconstruct that.
- **Rejected:** storing the DOI or normalised title in the clear for a unique index (leaks the topic); one paper table shared across projects (crosses the project's encryption and deletion boundary).
- **Costs:** a database client cannot search papers by title. Lookups go through the application.
- **Where:** [`domain/discovery.py`](../../services/api/app/domain/discovery.py) (`identity_digest`), [`application/papers.py`](../../services/api/app/application/papers.py) (`PaperStore`). Recorded in [ADR-0006](../adr/0006-papers-per-review-decisions-per-run.md).
- **Proven by:** `tests/test_screening_domain.py::TestDiscovery::test_an_identity_digest_depends_on_the_key`, `TestDiscover::test_paper_records_are_stored_only_sealed`, `TestDeletedProjects`.

### D11.4 A run works from a paper set: its own, or an earlier run's

- **Decision:** every run that reaches Ingest, Screen or Export has a `PaperSet` naming the run whose discovery it uses. A run with Discover has its own. A run without Discover adopts the review's newest earlier set on first use and records it, so every later stage of that run sees the same papers. Decisions are always the run's own.
- **Why:** "try again from this stage" (UI_UX_SPEC §6.7) starts a run without Discover, and it must know which papers it saw. Tags (`P<place>`) stay the same across such runs, so a brief and its retry cite papers alike.
- **Rejected:** copying the earlier run's rows into the new run (duplicates the sealed queries and loses the link back); failing any run without Discover (no retry without searching again).
- **Costs:** a retried run re-screens every paper, because decisions belong to the run that made them. Reusing a decision when the masked text, criteria version, prompt version and model are unchanged would save model time; it is not built (G11.4).
- **Where:** [`application/papers.py`](../../services/api/app/application/papers.py) (`PaperStore.adopt`).
- **Proven by:** `TestIngest::test_a_run_without_discover_works_from_the_newest_discovery`, `::test_a_run_without_any_discovery_fails_plainly`.

### D11.5 Screen reads masked text through a seam, and never anything else

- **Decision:** the screen stage gets text only from `MaskedTextSource.masked_text`, which the mask stage fills. A paper with no masked text is skipped and counted (`not_masked`); if no paper is masked at all, the stage fails plainly. Until the mask store is wired, the composition root's placeholder returns `None` for every paper, so nothing is sent to a model.
- **Why:** the one rule: data leaves only after it has been masked. Fail closed: an unwired mask store must mean "nothing screened", never "raw text screened".
- **Rejected:** falling back to the kept raw text when masking is missing; letting Screen call the sanitiser itself (a second masking path to review and keep in step).
- **Costs:** in a development run today, Screen fails with "None of the papers' text has been masked yet" (T11.6). That is the intended behaviour until the mask lane lands.
- **Where:** [`domain/screening.py`](../../services/api/app/domain/screening.py) (`MaskedTextSource`), [`application/screening.py`](../../services/api/app/application/screening.py), [`api/dependencies.py`](../../services/api/app/api/dependencies.py) (`_NothingMaskedYet`).
- **Proven by:** `TestScreen::test_the_model_only_ever_sees_masked_text`, `::test_a_paper_not_yet_masked_is_never_sent_and_is_counted_as_skipped`.

### D11.6 A label must agree with its criteria

- **Decision:** the model answers JSON matching a schema (label, score, priority, rationale, criteria met, ids from the plan only). The domain then checks meaning: an inclusion may meet no exclusion criterion; an exclusion's reasons are the exclusion criteria it meets, then the inclusion criteria it misses, and there must be at least one. An answer that breaks either rule counts as unreadable: it is asked again once, then recorded `uncertain`.
- **Why:** PRISMA reports exclusions with reasons, and a reviewer must be able to see which rule excluded a paper (UI_UX_SPEC §6.8, "Rule: …").
- **Rejected:** trusting the label alone (an "exclude" with nothing against the paper is not auditable); dropping unreadable papers (they vanish from the counts).
- **Costs:** a small model that labels well but lists criteria badly produces more `uncertain` papers for a person to check.
- **Where:** [`domain/screening.py`](../../services/api/app/domain/screening.py) (`exclusion_reasons`, `ScreeningDecision`), [`application/screening.py`](../../services/api/app/application/screening.py).
- **Proven by:** `tests/test_screening_domain.py::TestLabelRules`, `TestScreen::test_an_answer_that_contradicts_its_criteria_counts_as_unreadable`, `::test_an_answer_unreadable_twice_is_recorded_as_uncertain_with_the_reason`.

### D11.7 Bounded concurrency; in-flight papers finish after a failure

- **Decision:** a `BoundedSemaphore` of `HUMANI_T_SCREENING_CONCURRENCY` (default 2). Each paper's decision is stored and audited as soon as it is made. After the first failure (model server down, cancel, lease lost) no new paper starts, but those already in flight finish and are stored.
- **Why:** a CPU-only Ollama slows down sharply with parallel requests. A paid-for answer should not be thrown away.
- **Rejected:** a task group that cancels siblings on the first error (it loses in-flight answers, and wraps the worker's control exceptions in an exception group the worker would not recognise); unbounded parallelism.
- **Where:** [`application/screening.py`](../../services/api/app/application/screening.py) (`_screen_all`).
- **Proven by:** `TestScreen::test_screening_runs_at_most_the_configured_number_of_papers_at_once`, `::test_a_model_server_that_is_down_fails_the_stage_plainly_and_keeps_decisions`.

### D11.8 The manifest is gathered, never reconstructed

- **Decision:** each stage records its facts under `"manifest"` in its `stage.finished` summary (vocabulary, criteria and plan versions, sources, queries, model ids, prompt versions, `papers_from_run`). `GET /runs/{run_id}/manifest` decrypts the run's event log and assembles them. When two stages disagree, both values show.
- **Why:** provenance is captured when a decision is made ([`CLAUDE.md`](../../CLAUDE.md)). The event log is already stored, encrypted and append-only.
- **Rejected:** a separate manifest table written at the end of a run (a run that fails halfway has none; a second copy of the same facts can drift).
- **Where:** [`domain/manifest.py`](../../services/api/app/domain/manifest.py), [`application/papers.py`](../../services/api/app/application/papers.py) (`PaperService.manifest`).
- **Proven by:** `tests/test_screening_domain.py::TestRunManifest`, `tests/test_papers_api.py::TestManifest`.

### D11.9 No `ScreeningProvider` seam

- **Decision:** screening is a use case that calls the model through `GuardedInference` with a versioned prompt (`SCREEN_PROMPT_VERSION`), not a provider behind its own Protocol as §3.8 sketched.
- **Why:** a second seam in front of the model would be a second place a provider could skip the guard. The prompt and schema are what vary, and they are versioned constants.
- **Rejected:** `ScreeningProvider.screen(paper, criteria)` as in §3.8.
- **Where:** [`application/screening.py`](../../services/api/app/application/screening.py). Recorded in [`architecture.md` §4.2](../architecture.md#42-reviews).

### D11.10 Every criterion is answered, and a refused answer says why (2026-11-08)

- **Decision:**
  - the screening schema requires `answers`, exactly one per criterion (`minItems` = `maxItems`);
  - each refused answer is logged as `screening_answer_refused` with the run id, paper id, attempt and reason, never the paper's text or the model's words;
  - a model that is still rate limited after its retries stops the stage with its own message: "The model service's rate limit was reached. Papers screened so far are kept; wait a minute, then run it again.";
  - any other unavailable model is logged with its reason, for the operator only.
- **Why:**
  - with ten criteria, `gpt-4o-mini` answered four;
  - refusals were silent, which made 32 "uncertain" papers impossible to explain;
  - a burst of four concurrent calls hit a rate limit and was reported as "isn't answering".
- **Where:** [`application/screening.py`](../../services/api/app/application/screening.py).
- **Proven by:** `tests/test_pipeline_stages.py::TestScreen::test_the_answer_format_asks_for_exactly_one_answer_per_criterion`, `…::test_a_refused_answer_is_logged_with_its_reason_and_never_the_text`, `…::test_a_rate_limited_model_stops_screening_saying_so_and_keeps_decisions`, `tests/test_papers_api.py::TestCriterionAnswers`.
- **Still open:** a hosted model often joins quotes with "…", changes a capital letter, or quotes past 300 characters. Settled in D11.11.

### D11.11 A quote is found loosely and stored in the paper's own words (2026-11-12)

- **Decision:**
  - the model's quote is matched against the masked text ignoring case and runs of whitespace, and may join pieces with `…` or `...` if they appear in that order without overlapping and each has at least three words;
  - what is stored is the matching span of the masked text, never the model's wording; several pieces are joined with ` … `, and a result over 300 characters is cut at a word and ended with `…`;
  - still refused, and asked again: words not in the text, pieces out of order, a joined piece under three words, a blank quote;
  - the screening schema no longer sets `maxLength` on a quote; its description says longer quotes are cut. The prompt text is unchanged, but the version is now `screen-3`, so a decision records which quote rule it was made under.
- **Why:** gpt-5-mini quoted honestly but not exactly (a changed capital, an ellipsis, a long passage), so papers were refused twice and recorded `uncertain`, and real runs stalled at Screen. Storing the text's own span keeps the provenance rule: a reviewer only ever sees the paper's words as the paper's.
- **Rejected:** storing the model's quote once it matches loosely (a reviewer would read the model's capitals and joins as the paper's); fuzzy matching (it accepts paraphrase); one-word pieces around an ellipsis (`the … and … of` matches almost any abstract); refusing quotes over 300 characters (an honest quote, asked again, fails the same way).
- **Where:** [`domain/screening.py`](../../services/api/app/domain/screening.py) (`CriterionAnswer.located`), [`application/screening.py`](../../services/api/app/application/screening.py) (`screening_schema`, `_criterion_answers`).
- **Proven by:** `tests/test_screening_domain.py::TestQuotes`, `::TestQuoteProperties::test_every_stored_piece_is_an_exact_piece_of_the_text`, `tests/test_pipeline_stages.py::TestScreen::test_a_quote_in_another_case_is_decided_at_once_in_the_papers_own_words`, `…::test_the_answer_format_lets_a_long_quote_through_to_be_shortened`.

### Deliberately not used

| Approach | Why not |
| --- | --- |
| Fuzzy title matching to merge preprints and published versions | A wrong merge is far harder to notice than a missed one ([`domain/paper.py`](../../services/api/app/domain/paper.py)) |
| Re-using decisions across runs | Masked text may have changed; see G11.4 |
| Model-chosen criterion ids | The schema's `enum` restricts ids to the plan's own (`I1`, `E1`…) |

## Libraries

No new libraries. The phase uses SQLAlchemy's PostgreSQL `INSERT … ON CONFLICT DO NOTHING` (already a dependency) for the conditional writes, and the standard library's `hmac` and `asyncio`.

## Files to read, in order

From the domain rule outwards, because every later file only applies these rules.

1. [`services/api/app/domain/screening.py`](../../services/api/app/domain/screening.py): `ScreeningDecision` and its checks, `exclusion_reasons`, `tier_of`, `Passage`, the `EvidenceSource` and `MaskedTextSource` Protocols, `prisma_counts`.
2. [`services/api/app/domain/discovery.py`](../../services/api/app/domain/discovery.py): `merge_found`, `identity_digest`, `screening_text`, the stored shapes, and `IngestedTextSource`.
3. [`services/api/app/domain/manifest.py`](../../services/api/app/domain/manifest.py): which manifest keys combine how.
4. [`services/api/app/storage/base.py`](../../services/api/app/storage/base.py): `PaperRepository` and `ScreeningRepository`, and which writes are conditional.
5. [`services/api/app/storage/postgres/papers.py`](../../services/api/app/storage/postgres/papers.py) and [`migrations/versions/20261103_0009_papers.py`](../../services/api/migrations/versions/20261103_0009_papers.py): `ON CONFLICT DO NOTHING`, and `REVOKE UPDATE` on decisions.
6. [`services/api/app/application/papers.py`](../../services/api/app/application/papers.py): `PaperStore` (every encryption context), `adopt`, `ScreenedEvidence`, `PaperService`.
7. [`services/api/app/application/pipeline.py`](../../services/api/app/application/pipeline.py): Discover, Ingest and Export.
8. [`services/api/app/application/screening.py`](../../services/api/app/application/screening.py): the prompt, the schema, retry once, the semaphore.
9. [`services/api/app/api/routes/papers.py`](../../services/api/app/api/routes/papers.py) and [`schemas/papers.py`](../../services/api/app/api/schemas/papers.py): what a member is shown.
10. [`services/api/app/api/dependencies.py`](../../services/api/app/api/dependencies.py): `_pipeline_stages`, and the `_NothingMaskedYet` placeholder.

## Set up

Beyond [00 — Setup](00-setup.md): migrate, switch one real source on, and restart the API and a worker. OpenAlex needs no key; outbound HTTPS to `api.openalex.org` must be allowed.

```powershell
cd services\api
uv run --env-file .env alembic upgrade 0009
$env:HUMANI_T_PAPER_SOURCES = 'openalex'
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In a second PowerShell window:

```powershell
cd services\api
$env:HUMANI_T_PAPER_SOURCES = 'openalex'
uv run --env-file .env python -m app.worker
```

Have a project `{{project}}` with Amara (owner) and Maya (viewer), as in [07](07-projects-and-roles.md), and a review `{{review}}` from T8.1.1.

## Manual tests

### T11.1 Accept a plan without a model

- **Do:** As Amara, `POST {{base}}/reviews/{{review}}/plan/vocabulary` with body `{"format":"text","content":"[intervention]\ncash transfer; voucher\n[outcome]\nmalnutrition","criteria":[{"kind":"inclusion","text":"Displaced people or refugees"},{"kind":"inclusion","text":"Measures malnutrition"},{"kind":"exclusion","text":"Not a humanitarian or crisis setting"}]}`. Then `POST {{base}}/reviews/{{review}}/plan/accept` with `{"version":1}`.
- **Expect:** `200` each time; the accepted plan shows criteria `I1`, `I2` and `E1`.
- **Proves:** D11.1's input exists (the plan lane's routes; see its walkthrough for details).
- **If it fails:** the plan routes are the plan lane's; check [`api.md`](../api.md) for their current shape.

### T11.2 Discover, ingest and export

- **Do:** `POST {{base}}/reviews/{{review}}/runs` with `{"stages":["discover","ingest","export"]}`. Follow `GET {{base}}/runs/{{run}}/events` in Postman until `job.finished`.
- **Expect:** under `discover`, one `stage.progress` with the item `"openalex · 25 found"` (or fewer), then `stage.finished` with `identified`, `after_deduplication`, `new_papers` and a `manifest` holding `plan_version: 1`, `sources: ["openalex"]` and the rendered query. Under `ingest`, `abstracts` + `title_only` = the papers, `full_texts: 0`, and a gap about full text. Under `export`, `prisma` and `dataset`. `job.finished` has `outcome: "done"`.
- **Proves:** D11.1, D11.2, the ingest gap and export.
- **If it fails:** `job.finished` with "None of the paper sources could be searched": the worker cannot reach OpenAlex (check the worker log for `paper_source_failed`). "No paper sources are switched on": the worker did not get `HUMANI_T_PAPER_SOURCES`.

### T11.3 The paper list and PRISMA

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T11.3.1 | As Maya (viewer): `GET {{base}}/reviews/{{review}}/papers` | `200`; `run_id` = `{{run}}`; `total` = `after_deduplication`; every row has `tag` `P<position>` and `decision: null`; `counts.unscreened` = `total` | Viewers read; unscreened papers listed | `404 "This review has no papers yet."`: T11.2 did not finish |
| T11.3.2 | `GET …/papers?label=maybe` | `422` | Filter values checked | |
| T11.3.3 | `GET …/papers?page=999` | `200`, `papers: []`, `total` unchanged | Pagination | |
| T11.3.4 | `GET {{base}}/reviews/{{review}}/papers/<a paper_id>` | `200`; `text_kind: "abstract"`; `masked_text: null` (nothing masked yet); no `abstract` field anywhere | Members never get raw kept text (D11.5) | Any raw abstract in the body is a bug |
| T11.3.5 | `GET {{base}}/reviews/{{review}}/prisma` | `200`; the same numbers as the export summary's `prisma` | One source of counts | |
| T11.3.6 | As Tariq (not a member): T11.3.1, then the same with review id `00000000-0000-4000-8000-000000000000` | `404` both times, identical bodies | §5.5 | |

### T11.4 The manifest

- **Do:** `GET {{base}}/runs/{{run}}/manifest`.
- **Expect:** `200`; `stages` = `["discover","ingest","export"]`; `is_complete: true`; `vocabulary_versions: [1]`; `plan_versions: [1]`; `sources: ["openalex"]`; `queries.openalex` is the rendered query; `stage_facts` has three entries.
- **Proves:** D11.8.
- **If it fails:** `is_complete: false` means a stage did not finish; read its `job.finished` error.

### T11.5 A second run finds the same papers

- **Do:** Run T11.2 again, then `GET …/papers` (the newest run) and compare `paper_id`s with the first run's (`GET …/papers?run_id=<first run>`).
- **Expect:** the same `paper_id`s for the same papers (OpenAlex may return a slightly different set); `discover`'s `new_papers` counts only papers the first run did not have.
- **Proves:** a paper is stored once per review (D11.3).
- **If it fails:** new ids for the same DOIs mean the identity digest changed: check that the project's data key was not replaced.

### T11.6 Screening fails closed while nothing is masked

- **Do:** `POST {{base}}/reviews/{{review}}/runs` with `{"stages":["screen"]}` and follow it.
- **Expect:** a `stage.started` for `screen`, then `job.finished` with `outcome: "failed"` and `error`: "None of the papers' text has been masked yet, so none was sent to the model. Run the Mask stage, then Screen again." The model server's log shows no request.
- **Proves:** D11.5 and D11.4 (a run without Discover uses the newest discovery: no "no papers yet").
- **If it fails:** any model request here is a leak path. Stop and report it.

### T11.7 What is stored is sealed

- **Do:** pgAdmin (`humani_t`): `SELECT source_id, encode(identity_digest,'hex'), record_sealed FROM papers LIMIT 3;`, `SELECT kind, text_sealed FROM paper_texts LIMIT 3;`, `SELECT source_id, outcome, found, plan_version, query_sealed FROM run_sources;`.
- **Expect:** `record_sealed`, `text_sealed` and `query_sealed` are unreadable bytes; no title, DOI or query term is readable anywhere.
- **Proves:** D11.3.
- **If it fails:** readable text in a sealed column is a bug.

### T11.8 Decisions cannot be rewritten

- **Do:** pgAdmin, connected as `humani_t_app`: `UPDATE screening_decisions SET label = 'include';`
- **Expect:** `permission denied for table screening_decisions`.
- **Proves:** decisions are append-only provenance (migration `0009`).
- **If it fails:** the database was created without the role script; see [02](02-configuration-and-persistence.md).

### T11.9 Screening itself, end to end

- **Do:** `uv run pytest tests/test_pipeline_stages.py::TestScreen -v`.
- **Expect:** every test passes. They run the real worker, stages, storage, encryption and audit log, with a scripted model (`FakeInferenceProvider`) and the mask lane's contract faked.
- **Proves:** D11.5 to D11.7, until the mask store is wired and screening can be watched by hand against Ollama.
- **If it fails:** the failing test's name says which guarantee broke.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T11.1 | ☐ | |
| T11.2 | ☐ | |
| T11.3 | ☐ | |
| T11.4 | ☐ | |
| T11.5 | ☐ | |
| T11.6 | ☐ | |
| T11.7 | ☐ | |
| T11.8 | ☐ | |
| T11.9 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| A decision is valid by construction and carries its provenance | `tests/test_screening_domain.py::TestScreeningDecision` |
| Labels agree with their criteria; exclusions carry reasons | `tests/test_screening_domain.py::TestLabelRules` |
| The shown names follow from label and priority | `tests/test_screening_domain.py::TestTiers` |
| Merging keeps one record per paper and who found it | `tests/test_screening_domain.py::TestDiscovery` |
| PRISMA counts add up | `tests/test_screening_domain.py::TestPrisma` |
| The manifest gathers stage facts and shows disagreements | `tests/test_screening_domain.py::TestRunManifest` |
| Storage behaves the same on memory and Postgres; repeated writes are conditional | `tests/test_papers_storage_contract.py` |
| Discover: every source, merged, one row per source, failures recorded, repeat-safe | `tests/test_pipeline_stages.py::TestDiscover` |
| Ingest keeps sealed text; adopts the newest discovery | `tests/test_pipeline_stages.py::TestIngest` |
| Screen: masked text only, provenance, retry once, bounded, repeat-safe, audited | `tests/test_pipeline_stages.py::TestScreen` |
| Export carries PRISMA and the dataset | `tests/test_pipeline_stages.py::TestExport` |
| Evidence for the brief: included, masked, tagged by place | `tests/test_pipeline_stages.py::TestEvidence` |
| Deleting the project makes it all unreadable | `tests/test_pipeline_stages.py::TestDeletedProjects`, `tests/test_papers_api.py::TestDeletedProject` |
| The routes: order, filters, masked text only, 404 for non-members | `tests/test_papers_api.py` |

How to run only this phase's tests:

```powershell
uv run pytest tests/test_screening_domain.py tests/test_papers_storage_contract.py tests/test_pipeline_stages.py tests/test_papers_api.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G11.1 | The mask store is not wired: `MaskedTextSource` is a placeholder that has nothing | Screen fails closed in a real run (T11.6) | Wire the mask lane's store in `api/dependencies.py` at integration |
| G11.2 | Abstracts only; open-access full text and PDF extraction are not built | Screening and the brief see less than the paper says | P2 |
| G11.3 | No downloadable files (`dataset.csv`, `dataset.jsonl`, `prisma.md`) | Export is summary-only | P2 |
| G11.4 | Decisions are per run: a retried or repeated run re-screens every paper | Model time on a CPU-only server | Reuse a decision when masked text, criteria version, prompt version and model are all unchanged |
| G11.5 | Relabelling and the rules editor (UI_UX_SPEC §6.8) are not built | Decisions are read-only | P2 |
| G11.6 | Titles, DOIs and links are served as the paper's public bibliographic record, not masked | A member sees a paper's real title | Decide with the owner whether titles must be masked too |
| G11.7 | The papers list pages in the application, after reading every row of the run | Slow for very large runs (thousands of papers) | Page in SQL when runs grow past P1's sizes |
| G11.8 | ~~The migration branches from `0007`~~ | Resolved: re-chained as `0009`, after `0008` | Done |

## Glossary

| Term | Meaning |
| --- | --- |
| **Paper set** | Which run's discovery a run works from: its own, or the newest earlier one. |
| **Tag** | `P` and a paper's place in its run's merged list (`P12`); how the brief cites it. |
| **Tier** | The name a decision is shown with: Highly relevant, Relevant, Possibly relevant, Excluded, Uncertain. |
| **Exclusion reasons** | The exclusion criteria an excluded paper meets, then the inclusion criteria it misses. |
| **Unreadable** | A decision recorded `uncertain` because the model's answer could not be read twice. |
| **PRISMA** | The standard flow of a review: identified, after duplicates, screened, included, excluded with reasons. |
| **Manifest** | Everything a run used, as each stage recorded it when it finished. |
