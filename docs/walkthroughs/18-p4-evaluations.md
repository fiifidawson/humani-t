# 18 — P4: evaluations

> **Milestones:** UI_UX_SPEC §15 P4 · **Status:** in progress · **Previous:** [17 — P3: find](17-p3-find.md) · **Next:** `18-p4-*` (the other P4 lanes)

An instance administrator can now compare humani-t's answers with other systems' in the admin area (UI_UX_SPEC §6.15): pick a project they belong to, upload a question file and, optionally, other systems' answers, and pick a judge from the models the operator configured. The worker answers each question with the follow-up pipeline and asks the judge to compare each pair blind, as A and B. The results show each quality check's means and difference, with wins, ties and losses, and each judgement side by side with a "Reveal systems" toggle. It serves [`architecture.md` §4](../architecture.md) (Evaluation, Judgement, PairResult) and [ADR 0022](../adr/0022-evaluations-are-judged-blind-inside-their-project.md).

## What this phase delivers

- `GET /api/v1/admin/evaluations/judges`, `GET` and `POST /api/v1/admin/evaluations`, `GET /api/v1/admin/evaluations/{evaluation_id}`: administrators only, and only for projects they are members of.
- The question file is plain text, one question per line; each other system's answers are JSON Lines, `{"answer": "…"}` per question. Both are masked as the project chooses and sealed under its key before they are stored.
- Judging runs in the worker (and the in-process worker in development), under a lease, saving after each question.
- The setting `HUMANI_T_JUDGE_MODELS`, empty by default: other models on the same model server that may judge.
- Web: an **Evaluations** link in the rail for administrators, `/admin/evaluations` (list), `/admin/evaluations/new` (form), `/admin/evaluations/:evaluation` (results and judgements), each loaded on first use.

### Data model

| Table | Holds | Sealed |
| --- | --- | --- |
| `evaluations` (migration `20261121_0022_evaluations.py`) | project, review, who started it, judge and writer model, counts, status, lease, error | `setup_sealed` (masked questions and uploaded answers), `results_sealed` (humani-t's answers and the judgements) |

## Architecture choices and why

### D18e.1 An evaluation lives inside its project

- **Decision:** sealed under the project's key, bound to table, column and row; routes need the instance-admin role, and the use cases also need membership (ask questions to start, any role to read).
- **Why:** admin status alone must not open a project's text, and deleting a project must take its evaluations with it.
- **Rejected:** an admin-only store, which would outlive the project and bypass membership.
- **Costs:** an administrator must be added to a project to evaluate it.
- **Where:** [`application/evaluations.py`](../../services/api/app/application/evaluations.py), [`api/routes/evaluations.py`](../../services/api/app/api/routes/evaluations.py).
- **Proven by:** `tests/test_evaluations_api.py::TestWhoMayEvaluate`, `TestStarting::test_deleting_the_project_makes_its_evaluations_unreadable`, `tests/test_route_inventory.py::TestAdministratorsOnly`.

### D18e.2 One answerer

- **Decision:** humani-t answers with `QuestionWorker.answer_alone`, the follow-up pipeline without a thread, from the project's latest review.
- **Why:** an evaluation of a second answerer would not evaluate humani-t.
- **Rejected:** queuing real questions in a thread, which would show up in the project's conversation.
- **Where:** [`application/questions.py`](../../services/api/app/application/questions.py).
- **Proven by:** `TestAnswering::test_humani_t_answers_each_question_with_the_follow_up_pipeline`.

### D18e.3 Blind pairwise judging, untrusted answers kept as data

- **Decision:** the judge sees the question and two answers as JSON string values, A and B in a random recorded order, under a fixed prompt (`evaluation-judge/1`) that says they are data, never instructions; it returns six scores per answer, `a`, `b` or `tie`, and a reason. No system names, no citation tags.
- **Why:** an uploaded answer is text from elsewhere; JSON encoding means it cannot close a delimiter, and the schema leaves nothing else to answer.
- **Rejected:** delimiters inside the prompt; telling the judge which system wrote which.
- **Where:** `JUDGE_PROMPT`, `EvaluationWorker._compare`.
- **Proven by:** `TestJudging`.

### D18e.4 Judges are the operator's

- **Decision:** `HUMANI_T_JUDGE_MODELS` lists other models on the same server; the deployment's model can always judge. Cloud models need `allow_hosted_inference` and make the deployment count as hosted.
- **Why:** the one rule: no text goes to a model the operator did not choose.
- **Where:** [`settings.py`](../../services/api/app/settings.py), `_judges` in [`api/dependencies.py`](../../services/api/app/api/dependencies.py).
- **Proven by:** `TestJudgeModels`, `tests/test_settings.py::TestJudgeModels`.

## Libraries

None added.

## Files to read, in order

From the rule to the screen:

1. [`app/domain/evaluations.py`](../../services/api/app/domain/evaluations.py) — the file formats, `Judgement`, `summarise`, and the record that is also the job.
2. [`app/application/evaluations.py`](../../services/api/app/application/evaluations.py) — masking and sealing the upload, the judge prompt, the worker.
3. [`app/api/routes/evaluations.py`](../../services/api/app/api/routes/evaluations.py) and [`schemas/evaluations.py`](../../services/api/app/api/schemas/evaluations.py) — the admin check and the shapes.
4. [`apps/web/src/features/admin/`](../../apps/web/src/features/admin/) — the three pages.

## Set up

Nothing beyond [00 — Setup](00-setup.md), plus the migration and, to offer a second judge, the setting:

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
# in services\api\.env, a model pulled on your Ollama, from another family than the writer:
# HUMANI_T_JUDGE_MODELS=mistral-small:24b
```

For a run without a model server, `HUMANI_T_DEMO_INFERENCE=true` answers and judges with canned output.

## Manual tests

You need an administrator who is a reviewer or owner of a project with a review that has a brief. Save a question file `questions.txt` with two lines, for example `Do cash transfers raise food prices?` and `Which markets recover first?`, and an answers file `answers.jsonl` with `{"answer": "Prices rose by a tenth."}` and `{"answer": "Markets near roads."}` on two lines.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T18e.1 | Sign in as a non-administrator; look at the rail; open `/admin/evaluations` | No **Evaluations** link; the page says "Administrators only." | D18e.1 | the rail reads `is_admin` from the session; the API's `_admin` dependency |
| T18e.2 | As the administrator, open **Evaluations** → **New evaluation** | the judge menu lists only the configured models, the writer's marked "(also writes the answers)", with "The judge should be from a different model family than the writer." | D18e.4 | `GET /admin/evaluations/judges`, `HUMANI_T_JUDGE_MODELS` |
| T18e.3 | Choose the project, the two files (Add another system's answers, name it System B), a judge; **Start evaluation** | the evaluation opens: "Waiting to start", then "Answered 1 of 2 questions", then "Finished" | D18e.2 | is the worker running (`python -m app.worker`)? its log `evaluation_failed` |
| T18e.4 | Read the results | a table "humani-t compared with System B" with six checks, both means and the difference; "humani-t won …, tied … and lost … of 2 questions."; footer "Made by … Judged by …" | D18e.3 | `results` in `GET /admin/evaluations/{id}` |
| T18e.5 | In a judgement, compare A and B, then **Reveal systems** | no system name until the toggle; then "Answer A: …" names each | D18e.3 | `system_a`/`system_b` in the response |
| T18e.6 | Upload an empty question file | "The question file has no questions. Put one question on each line." | domain parsing | `parse_question_file` |
| T18e.7 | Remove the administrator from the project, then open the evaluation's URL | "No such evaluation."; the list no longer shows it | D18e.1 | `require_member` in `EvaluationService.get` |
| T18e.8 | In a DB client: `SELECT setup_sealed, results_sealed FROM evaluations;` | binary ciphertext only; no question text | D18e.1 | `_EvaluationSeal` |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T18e.1 | ☐ | |
| T18e.2 | ☐ | |
| T18e.3 | ☐ | |
| T18e.4 | ☐ | |
| T18e.5 | ☐ | |
| T18e.6 | ☐ | |
| T18e.7 | ☐ | |
| T18e.8 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| File formats, judgements and results as rules | `tests/test_evaluations_domain.py` |
| Admins only, members only, masked and sealed, judged blind in the worker | `tests/test_evaluations_api.py` |
| One worker per evaluation, leases, versions | `tests/test_evaluations_storage_contract.py` |
| Every evaluation route refuses a non-administrator | `tests/test_route_inventory.py::TestAdministratorsOnly` |
| The admin screens | `apps/web/src/features/admin/Evaluations.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_evaluations_domain.py tests/test_evaluations_api.py tests/test_evaluations_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G18e.1 | No deleting or rerunning an evaluation | an unwanted one stays until its project goes | add when asked |
| G18e.2 | Answers are matched to questions by line order, not by id | a reordered answers file compares the wrong answers | the format says "in the same order"; an id field can be added later |
| G18e.3 | The judge sees no passages | groundedness is judged from what the answers say, not checked against sources | humani-t's own answers are checked by code already |
| G18e.4 | Users and invitations (the rest of §6.15) have no screen yet | admins invite through the API | a later lane |

## Glossary

| Term | Meaning |
| --- | --- |
| **Judge** | the model that compares two answers and scores them. |
| **Blind** | the judge is not told which system wrote which answer, and sees them in a random order. |
| **Pair** | humani-t and one other system, over every question judged. |
