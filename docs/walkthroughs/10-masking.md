# 10 — Masking

> **Milestones:** M11 · **Status:** in progress (papers, questions and the prompt guard done; documents and their review screen are P2) · **Previous:** [09 — Plan and discover](09-plan-and-discover.md) · **Next:** [11 — Discover, ingest and screening](11-screening.md)

After this phase, nothing reaches a model unmasked. Every paper's kept text is masked by the `mask` stage before screening reads it, every question and change request is masked before planning calls the model, and every outgoing prompt is scanned again immediately before it leaves, refusing the call rather than repairing it. Owners can add project masking rules ("always mask Kofi Mensah"), and every masked span says what produced it. This serves the brief's rules 1 to 5 and [`architecture.md` §3.9.1](../architecture.md#391-masking-is-derived-reviewable-and-re-runnable). **Masking is not anonymity:** detectors miss formats, unlabelled numbers are left alone on purpose, and rare details still identify people.

## What this phase delivers

- **A pattern detector, always on, with no model and no I/O:** emails (including `name [at] domain [dot] org`), phone numbers in international and labelled local forms, medical record numbers such as `MRN 448-1207` and its variants, national-ID-like and passport-like numbers next to their label, IBANs and card numbers whose check digits are right, IP addresses and GPS coordinates. Each has a table of texts it must find and texts it must leave alone.
- **A local name-and-place detector:** spaCy's `en_core_web_sm`, an optional `ner` extra, loaded only from an installed package or a local folder. A model name that looks like a URL or a hub id is refused at startup, never downloaded.
- **Masking as a function of raw text and spans.** Placeholders are typed and numbered (`[PERSON_1]`); the same value gets the same placeholder within one text; overlapping spans merge, and the wider region is masked. Spans keep their offsets into the unchanged raw text.
- **A last check on what leaves.** `LeakCheckPromptGuard` scans every message and the schema of every model call. Placeholders pass; anything the pattern detector still finds refuses the call, naming types and counts, never values.
- **The `mask` stage** masks each paper's kept text with the narrow list and the project's rules, stores masked text and spans sealed under the project's key, skips papers masked the same way already, and stops at the `privacy_review` gate when documents are waiting (P2).
- **Endpoints:** `GET`, `POST /api/v1/projects/{project_id}/masking-rules` and `DELETE …/masking-rules/{rule_id}`, owners only ([api.md](../api.md#masking-rules)).
- **Settings:** `HUMANI_T_MASKING_NER` (`auto`, `spacy` or `off`; default `auto`) and `HUMANI_T_MASKING_NER_MODEL` (default `en_core_web_sm`).

### Data model

| Table | Holds | Sealed |
| --- | --- | --- |
| `masking_rules` | id, project, entity type, who added it and when | `term_sealed`, bound to the rule's id |
| `paper_masks` | one row per paper: what it was masked with (`masked_with`) and when | `masked_sealed` and `spans_sealed`, bound to the paper's id |

Migration `0010` creates both, after `0009` creates `papers`. `paper_masks.paper_id` has no foreign key to `papers` yet; that is an open question for the owner.

## Architecture choices and why

### D10.1 spaCy for names and places, not GLiNER

- **Decision:** `SpacyNerDetector` adapts spaCy 3.8 with `en_core_web_sm` 3.8.0. It maps `PERSON` to PERSON, `GPE`, `LOC` and `FAC` to LOCATION and `DATE` to DATE, and ignores the rest.
- **Why:** about 13 MB of model plus spaCy's wheels, which install on Windows and Python 3.13; MIT-licensed code and model; CPU-only, a few milliseconds per abstract; `spacy.load` never fetches anything.
- **Rejected:** GLiNER, which is better at zero-shot names but needs PyTorch (hundreds of MB), downloads from the Hugging Face hub unless forced offline, and some of its PII models are under non-commercial licences; Presidio's analyser, which wraps the same spaCy model but adds `tldextract` (a network fetch of the public suffix list) and downloads missing spaCy models by itself; mmore's `openai_filter`, which sends the text to a third party (CLAUDE.md forbids it).
- **Costs:** the small model misses names it has not seen, especially non-European ones. GLiNER can be added later as a second detector behind the same Protocol.
- **Where:** [`app/privacy/spacy_ner.py`](../../services/api/app/privacy/spacy_ner.py), [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py) (`ner_detectors`).
- **Proven by:** `tests/test_privacy_sanitizer.py::TestSpacyNerAdapter` (a recorded-output fake), `::TestSpacyNerWithTheRealModel` (skipped without the extra), `::TestNerWiring`.

### D10.2 Papers get the narrow list

- **Decision:** papers are masked for structured identifiers only; questions add names and places; uploaded documents (P2) add dates too.
- **Why:** published abstracts name places and populations as evidence. Masking "Marib" would break screening against a setting criterion, and a question's years are its search scope.
- **Rejected:** one broad list for everything (it degrades screening); no masking for papers (corresponding authors' emails and phone numbers are personal data).
- **Costs:** a person named in an abstract is not masked unless a project rule names them.
- **Where:** [`app/domain/masking.py`](../../services/api/app/domain/masking.py) (`PAPER_ENTITY_TYPES`, `QUESTION_ENTITY_TYPES`, `DOCUMENT_ENTITY_TYPES`).
- **Proven by:** `tests/test_masking_domain.py::TestEntityLists`, `tests/test_privacy_sanitizer.py::TestDetectorSanitizer`.

### D10.3 The guard refuses, and runs patterns only

- **Decision:** the guard runs the pattern detector on the exact outgoing text and refuses on any hit outside a placeholder. Project rules are added per call with `GuardedInference.with_rules`.
- **Why:** a prompt repaired at the last moment is a prompt nobody reviewed. The guard shares its detector with the sanitiser, so text the sanitiser masked always passes.
- **Rejected:** running the name model in the guard, which would refuse ordinary screening prompts full of authors and places; replacing hits with placeholders silently.
- **Where:** [`app/privacy/guard.py`](../../services/api/app/privacy/guard.py), [`app/application/inference.py`](../../services/api/app/application/inference.py).
- **Proven by:** `tests/test_privacy_sanitizer.py::TestLeakCheckPromptGuard`, `::TestGuardedInferenceWithTheRealGuard`.

### D10.4 A masking records what made it

- **Decision:** a span's source is `detector@version`, `user:<id>` or `rule:<id>`; a paper's stored masking records the sorted detectors and rule ids (`masked_with`). The stage skips a paper whose `masked_with` matches, and replaces one that does not with a conditional write.
- **Why:** "why was this masked, and by what?" must have an answer, and a re-run after a gate or crash must not redo work, while a new rule must reach every paper.
- **Where:** [`app/application/masking.py`](../../services/api/app/application/masking.py), [`app/storage/postgres/masking.py`](../../services/api/app/storage/postgres/masking.py).
- **Proven by:** `tests/test_mask_stage.py::TestReRuns`, `tests/test_masking_storage_contract.py::TestPaperMasks`.

## Libraries

### spaCy 3.8.18 and en_core_web_sm 3.8.0 (optional `ner` extra)

- **What it does:** tokenises text and tags named entities; only the `tok2vec` and `ner` components are loaded.
- **Why this one:** small, local, permissively licensed, works on Windows (D10.1).
- **Alternatives:** GLiNER, Presidio, Flair.
- **Trade-offs:** spaCy pulls in `requests` and `httpx` for its own CLI downloads; humani-t never calls them, and import-linter still forbids any of our modules from opening HTTP outside `app.net`.
- **What we gave up:** the better recall of a transformer model.

## Files to read, in order

Start from the rule, then the seam, then where it is used.

1. [`services/api/app/domain/masking.py`](../../services/api/app/domain/masking.py): entity lists, `MaskSpan`, `apply_spans`, `rule_spans`, `masking_fingerprint`.
2. [`services/api/app/privacy/base.py`](../../services/api/app/privacy/base.py): `Detector` and `Sanitizer`.
3. [`services/api/app/privacy/patterns.py`](../../services/api/app/privacy/patterns.py): each pattern and its check.
4. [`services/api/app/privacy/sanitizer.py`](../../services/api/app/privacy/sanitizer.py) and [`guard.py`](../../services/api/app/privacy/guard.py).
5. [`services/api/app/application/masking.py`](../../services/api/app/application/masking.py): rules, `PaperMaskStore`, `MaskStage`.
6. [`services/api/app/api/dependencies.py`](../../services/api/app/api/dependencies.py): `prompt_guard`, `ner_detectors`, `_planning_sanitizer`, `_mask_stage`.

## Set up

The name model is optional. To install it into the API's environment:

```powershell
cd services\api
uv sync --extra ner
```

Without it, `HUMANI_T_MASKING_NER=auto` masks patterns and project rules only, and the API starts normally. With `HUMANI_T_MASKING_NER=spacy` the API refuses to start if the model is missing.

## Manual tests

### T10.1 A privacy receipt shows what the model received

- **Do:** sign in as a project owner in Postman, create a review whose question is `Should we call amina.yusuf@example.org about the camps near Marib?`, then `POST /api/v1/reviews/{review_id}/plan` with `{"mode": "shallow"}` (needs a model server; see 09).
- **Expect:** `201`. In `receipts[0].items[0]`, `sent` contains `[EMAIL_1]` and, with the `ner` extra installed, `[LOCATION_1]`; `counts` is `{"EMAIL": 1, "LOCATION": 1}` (or `{"EMAIL": 1}` without the extra). Neither `amina` nor `Marib` appears anywhere in the response's receipts.
- **Proves:** D10.2, the brief's rules 1 and 4.
- **If it fails:** check `HUMANI_T_MASKING_NER` and that `uv sync --extra ner` ran.

### T10.2 An unmasked prompt is refused

- **Do:**

```powershell
cd services\api
@'
import asyncio
from app.api.dependencies import prompt_guard
from app.domain.inference import ChatMessage, ChatRequest, ChatRole
request = ChatRequest((ChatMessage(ChatRole.USER, "Write to amina@example.org, MRN 448-1207"),), {"type": "object"})
print(asyncio.run(prompt_guard().inspect(request)))
masked = ChatRequest((ChatMessage(ChatRole.USER, "Write to [EMAIL_1], MRN [MEDICAL_RECORD_1]"),), {"type": "object"})
print(asyncio.run(prompt_guard().inspect(masked)))
'@ | uv run python -
```

- **Expect:** first `PromptVerdict(is_allowed=False, reason='The prompt still contains unmasked personal data (1 EMAIL, 1 MEDICAL_RECORD); nothing was sent.')`, then `PromptVerdict(is_allowed=True, reason='')`. No value appears in the reason.
- **Proves:** D10.3.
- **If it fails:** a pattern changed; run `uv run pytest tests/test_privacy_patterns.py -v`.

### T10.3 Masking rules are owners only and sealed

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T10.3.1 | As owner, `POST /api/v1/projects/{project_id}/masking-rules` with `{"term": "Kofi Mensah"}` and the CSRF header | `201`, `entity_type` `PERSON`, a `Location` header | rules can be added | the route is not mounted: check `app/main.py` |
| T10.3.2 | Same request again with `"KOFI MENSAH"` | `409` | duplicates ignore case | check `MaskingRuleService.add` |
| T10.3.3 | As a reviewer, `GET` the same path | `403` | owners only | check `require(Action.VIEW_RAW_TEXT)` |
| T10.3.4 | In pgAdmin, `SELECT term_sealed FROM masking_rules` | bytes, no readable name | sealed at rest | check `_TERM_PURPOSE` sealing |
| T10.3.5 | In pgAdmin, `SELECT action, target_id FROM audit_events ORDER BY sequence DESC LIMIT 3` | `masking_rule.listed`, `masking_rule.created`, ids only | audited without values | check the `record` calls |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T10.1 | ☐ | |
| T10.2 | ☐ | |
| T10.3 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| No masked value survives; the result does not depend on span order; offsets are kept | `tests/test_masking_domain.py::TestApplySpansProperties` |
| Every pattern finds its formats and leaves ordinary text alone | `tests/test_privacy_patterns.py::TestPatternDetector` |
| Any detector's span is masked; rules always apply | `tests/test_privacy_sanitizer.py::TestDetectorSanitizer` |
| An unmasked email stops the call; a masked one goes through | `tests/test_privacy_sanitizer.py::TestGuardedInferenceWithTheRealGuard` |
| The stage masks, seals, skips on re-runs and gates on documents | `tests/test_mask_stage.py` |
| Rules are owners only, sealed, shredded with the project and audited by id | `tests/test_masking_api.py` |

```powershell
uv run pytest tests/test_masking_domain.py tests/test_privacy_patterns.py tests/test_privacy_sanitizer.py tests/test_mask_stage.py tests/test_masking_api.py tests/test_masking_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G10.1 | Unlabelled numbers (a bare `4481207`) are not masked | an identifier written without its label can reach a model | deliberate, to keep the guard from refusing statistics; project rules cover known values |
| G10.2 | The small spaCy model misses unfamiliar names | a name in a question can reach the model | a second detector (GLiNER) behind the same Protocol; owners can add rules |
| G10.3 | Uploaded documents, the review screen and "not personal" corrections are not built | the `privacy_review` gate is only reached through a fake in tests | P2 |
| G10.4 | Postgres contract tests for `masking_rules` and `paper_masks` were not run in this phase (no database available) | a SQL mistake would show only at integration | run them against the lane database at integration |

## Glossary

| Term | Meaning |
| --- | --- |
| **Span** | A range of the raw text that is masked, with its entity type and what found it. |
| **Placeholder** | What replaces a span in masked text, such as `[PERSON_1]`. |
| **Masking rule** | A term an owner says is always masked in their project. |
| **Fingerprint** (`masked_with`) | The detectors, versions and rules a text was masked with. |
| **Prompt guard** | The check on every model call's exact outgoing text, which refuses rather than repairs. |
