# Evaluations (removed)

> **Status:** removed 2026-11-26 · **Design:** [ADR 0022](../adr/0022-evaluations-are-judged-blind-inside-their-project.md) (withdrawn, kept as the record) · **Last commit with it:** `3814817` on `staging`

Evaluation admin let an instance administrator compare humani-t's answers with other systems' answers inside one of their projects. A model the operator chose judged each pair blind. It was built in P4 (UI_UX_SPEC §6.15, CHITCHAT reference §7), was off by default behind `HUMANI_T_EVALUATIONS_ENABLED`, and was removed before it was ever turned on. This page keeps what is needed to bring it back.

## What it did

- **Who:** instance administrators only (403 before the body is read). Admin status alone opened no project: starting an evaluation needed a role that may ask questions, and reading one needed any membership.
- **Input:** a question file (plain text, one question per line, at most 50) and up to 2 other systems' answers (JSON Lines, `{"answer": "…"}` per question). The browser sent the files' text in JSON.
- **Privacy:** the upload was masked as the project chooses and sealed under the project's key before it was stored. Deleting the project made its evaluations unreadable.
- **Answering:** the worker answered each question with the follow-up pipeline (`QuestionWorker.answer_alone`: same corpus, instructions and loop, no thread).
- **Judging:** for each question and each other system, the judge saw the two answers as A and B, in an order drawn at random and recorded. It scored each 0–2 on the Critic's six checks and said `a`, `b` or `tie`, with a reason. The prompt was fixed (`evaluation-judge/1`); answers travelled only as JSON string values, so an uploaded answer could not steer the judge. No system names and no citation tags reached the judge.
- **Judges:** the deployment's own model, plus any listed in `HUMANI_T_JUDGE_MODELS` on the same model server. A cloud judge needed `allow_hosted_inference` and made the deployment count as hosted.
- **Web:** an **Evaluations** rail link (shown when the session's `can_evaluate` was true), `/admin/evaluations`, `/admin/evaluations/new`, `/admin/evaluations/:evaluationId`.

## What was removed

| Area | Files |
| --- | --- |
| Domain and use case | `app/domain/evaluations.py`, `app/application/evaluations.py`, `QuestionWorker.answer_alone` in `app/application/questions.py` |
| HTTP | `app/api/routes/evaluations.py`, `app/api/schemas/evaluations.py`, `can_evaluate` on `CurrentSessionOut` |
| Storage | `EvaluationRepository` in `app/storage/base.py`, `app/storage/memory_evaluations.py`, `app/storage/postgres/evaluations.py`, the `evaluations` table |
| Wiring | `EvaluationService`, `EvaluationWorker`, `_judges` and `is_evaluations_on` in `app/api/dependencies.py`; the worker's judging task in `app/worker.py` |
| Settings | `evaluations_enabled`, `judge_models`, `judge_model_ids` and the cloud-judge check in `app/settings.py` |
| Tests | `tests/test_evaluations_api.py`, `tests/test_evaluations_domain.py`, `tests/test_evaluations_storage_contract.py`, `tests/evaluation_support.py`, `TestJudgeModels`, `TestAdministratorsOnly` |
| Web | `features/admin/EvaluationsPage.tsx`, `NewEvaluationPage.tsx`, `EvaluationPage.tsx`, `Evaluations.test.tsx`, `words.ts`, `app/evaluationQueries.ts`, `test/fakeEvaluations.ts` |
| Docs | `docs/walkthroughs/18-p4-evaluations.md`, the Evaluations section of `docs/api.md` (copied below) |

Migration `0024` drops the table. Migration `0022`, which created it, stays, because deployed databases have already run it.

## Bringing it back

1. Start from the commit that removed it: `git revert <that commit>` restores every file at once. If too much has changed since, restore files one by one instead: `git checkout 3814817 -- <path>`.
2. Do not delete migration `0024`. Add a new migration that creates the table again: the body of `0024`'s `downgrade()` is exactly that.
3. Run `pnpm --filter @humani-t/web gen:api` so the web app sees the routes and `can_evaluate` again.
4. Set ADR 0022's status back to Accepted, or write a new ADR if the design changes.
5. Known gaps when it was removed: evaluations could not be deleted or rerun.

## The API contract, as it was

humani-t's answers against other systems', judged blind by a model the operator configured (UI_UX_SPEC §6.15, [ADR 0022](../adr/0022-evaluations-are-judged-blind-inside-their-project.md)). An evaluation belongs to one project: everything in it is sealed under the project's key and goes with the project.

**Off until the operator turns it on.** While `HUMANI_T_EVALUATIONS_ENABLED` is false (the default), every route below answers `404` to everyone, administrators included, the worker judges nothing, and `GET /auth/session` answers `"can_evaluate": false`. The web app shows the Evaluations link only when `can_evaluate` is true: an instance administrator on a deployment with evaluations on.

**Who:** instance administrators only; anyone else gets `403` before the body is read. Admin status alone opens nothing: starting one also needs membership of the project with a role that may ask questions (owner or reviewer), reading one any membership. A non-member gets the same `404` as for a missing project or evaluation.

### `GET /api/v1/admin/evaluations/judges`

`200` `{"writer_model": "qwen3.5:9b", "judge_models": ["qwen3.5:9b", "mistral-small:24b"]}`: the deployment's own model first, then `HUMANI_T_JUDGE_MODELS`. The judge should be from a different model family than the writer.

### `POST /api/v1/admin/evaluations`

```json
{
  "project_id": "…",
  "judge_model": "mistral-small:24b",
  "question_file": "Do cash transfers raise prices?\nWhich markets recover first?",
  "other_systems": [{"name": "System B", "answers_file": "{\"answer\": \"…\"}\n{\"answer\": \"…\"}"}]
}
```

The files travel as their text. `question_file` is plain text, one question per line (blank lines skipped, 1 to 50 questions of at most 2,000 characters, at most 200,000 characters in all). Each `answers_file` is JSON Lines, one `{"answer": "…"}` per question in the same order (at most 8,000 characters an answer, 500,000 in all). At most 2 other systems, each named 1 to 60 characters, distinct, and not `humani-t`; with none, humani-t answers and nothing is judged. The questions and answers are masked as the project's masking choice says before they are stored. humani-t answers from the project's latest review.

`202` with `Location: /api/v1/admin/evaluations/{id}` and an `EvaluationOut`, `status` `queued`. `422` for a file the rules refuse (the message names the line), a judge the deployment does not offer, or a project with no review yet.

### `GET /api/v1/admin/evaluations`

`200` a list of summaries, newest first: `id`, `project_id`, `status` (`queued`, `running`, `finished`, `failed`), `judge_model`, `writer_model` (once the first question is answered), `question_count`, `answered_count`, `system_count`, `created_at`, `finished_at`, `error` (a plain sentence when failed).

### `GET /api/v1/admin/evaluations/{evaluation_id}`

`200` `EvaluationOut`: the summary, plus `systems` (`humani-t` first), `is_masked`, `masked` (counts by type over the upload), `judge_prompt_version`, `answers` (`position`, `question`, humani-t's `answer`), `judgements` (`position`, `question`, `system_a`, `system_b`, `answer_a`, `answer_b`, `scores_a`, `scores_b` with the six quality checks 0 to 2, `winner` `a`, `b` or `tie`, `reason`) and `results`, one per other system judged so far: `first` (humani-t), `second`, `judged`, `wins`, `ties`, `losses` (from humani-t's side) and `metrics`, each `{metric, first_mean, second_mean, difference}` rounded to 2 places. Poll while `queued` or `running`.

### The judging worker

Runs in the worker process. For each question it answers with the follow-up pipeline, then for each other system asks the judge to compare the two answers, shown as A and B in an order drawn at random and recorded. The judge never sees system names or humani-t's citation tags. Progress is saved after each question, so a failure keeps what was judged. A lapsed lease is retried once. If the project's masking was turned on after an upload made with it off, the evaluation stops before anything is sent.

### Audit

`evaluation.started` (target `evaluation`), with the question and system counts. No question, answer or system name.
