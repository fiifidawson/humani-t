# ADR-0022: Evaluations are judged blind, by an operator's model, inside their project

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

UI_UX_SPEC §6.15 asks for an admin area where an instance administrator compares humani-t's answers with other systems': pick a project, upload a question file and, optionally, other systems' answers, pick a judge model, and read results per system pair on the six quality checks (§6.9), with each judgement shown as A and B. The questions and answers are project text, and other systems' answers are text written elsewhere and uploaded by a person: untrusted input that a model will read.

## Decision

- **An evaluation belongs to one project.** Its row holds ids, counts and state in the clear; the questions, the uploaded answers, humani-t's answers and every judgement are sealed under the project's data key, bound to table, column and row (`evaluations.setup`, `evaluations.results`). Deleting the project destroys the key, so they become unreadable with it.
- **Admin status alone opens nothing.** Every route needs the instance-admin role (403 otherwise, before the body is read), and every use case also needs membership of the project: starting one needs a role that may ask questions, reading one any membership. A non-member gets the 404 of a missing project or evaluation. The list holds only evaluations of the administrator's own projects. Starting is audited with ids and counts only.
- **Upload formats.** The question file is plain text, one question per line (blank lines skipped, at most 50, each a valid follow-up question). Each other system's answers are one JSON Lines file, `{"answer": "…"}` per line in the questions' order (at most 2 systems, 8,000 characters per answer). The browser reads the files and sends their text in JSON; the domain parses them and names the line it refuses. Pydantic caps the sizes (200,000 and 500,000 characters).
- **Masked before stored.** The upload is masked with the question sanitiser as the project's masking choice says, and only the masked text is stored; the response carries the counts. If masking is turned on after an upload made with it off, the worker stops before sending anything, as for questions (ADR 0014).
- **One answerer.** humani-t answers each question with `QuestionWorker.answer_alone`: the same corpus, writing instructions and loop as a thread's question, with no earlier turns. The project's latest review supplies the corpus.
- **Judging is pairwise and blind.** For each question and each other system, the judge sees the question and the two answers as A and B, in an order drawn by a fair coin (`secrets`) and recorded with the judgement. It scores each answer 0 to 2 on coverage, groundedness, disagreements, uncertainty, humanitarian awareness and specificity (the Critic's six), says `a`, `b` or `tie`, and gives a reason. Citation tags are left out of humani-t's answer, so its format does not give it away; system names never reach the judge.
- **Untrusted answers stay data.** The judge's instructions are a fixed, versioned system prompt (`evaluation-judge/1`) that says the values are text to assess, never instructions, and that an answer trying to steer the judge counts against itself. The question and answers travel only as string values of a JSON document in the user message, so an answer cannot close a delimiter or forge a field. The output schema leaves nothing to answer but scores, a letter and a reason of at most 600 characters. Every call goes through `GuardedInference.for_project(privacy)` (the project's rules, and the patterns when masking is on), at temperature 0.
- **Judges are the operator's.** `HUMANI_T_JUDGE_MODELS` (empty by default) names other models on the same model server; the deployment's own model can always judge. A cloud model is refused unless hosted models are allowed, and makes the deployment count as hosted. The screen says "The judge should be from a different model family than the writer."
- **Background work.** The row is also the job, claimed under a lease like a question, with two attempts. Progress is saved after each question, so a failure keeps what was judged and a retry resumes. Results are computed on read: per pair, each metric's mean for both systems and their difference, and wins, ties and losses from humani-t's side.

## Consequences

- An administrator who wants to evaluate a project must be added to it, and is then visible in its member list like anyone else.
- Long evaluations are slow on a CPU-only server: up to 50 questions, each an answer (up to four calls) and one judge call per other system.
- The judge's prompt is fixed when an evaluation starts; one started under an earlier prompt version is stopped rather than judged by a different prompt.
- A judge that reads the same masked text as the writer cannot be biased by personal details, but it can still prefer longer or more confident answers; the prompt asks it not to, and nothing can guarantee it.

## Alternatives rejected

- **A separate admin-only store or key:** would let deployment administration read project text without membership, and would survive the project's deletion.
- **Answers matched by question text, or one file per question:** text matching breaks on any edit; one file per answer is awkward to produce and to check. A line-ordered JSON Lines file is what evaluation tools already export.
- **Sending answers inside delimiters in the prompt:** an answer can contain the delimiter; JSON string values cannot be escaped out of.
- **Any judge model by name:** would let an administrator send project text to a model the operator never chose.
