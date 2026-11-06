# 16 — Documents

> **Milestones:** UI_UX_SPEC §15 P2 (documents, privacy review) · **Status:** in progress (API done; the web screens are wave B) · **Previous:** [15 — Exports and follow-up questions](15-exports-and-questions.md) · **Next:** none yet

Before this phase a team could only bring papers from the paper sources. Now an owner uploads the team's own files to a project (PDF, Word, plain text, Markdown). Each one is scanned for malware, read in a separate process that can do nothing but read, and masked with the broad list: names and places (with the name model installed), dates, contacts and identifiers, plus the project's rules. An owner then reviews the masked result: sees the original and every mask with what made it, masks what was missed, removes what is not personal with a reason, and approves or rejects the exact masked version they saw. Only an approved document becomes evidence, for the decision brief and follow-up questions, cited as `D1`, `D2`, … with a page or character range. A run that reaches the mask stage while documents wait pauses at the `privacy_review` gate, and deciding the last one resumes it. Everything is sealed under the project's key, and deleting the project shreds it. This serves the brief's rules 2, 3, 12, 13 and 17, [`architecture.md` §3.3 and §3.9.1](../architecture.md#391-masking-is-derived-reviewable-and-re-runnable) and UI_UX_SPEC [§6.6](../UI_UX_SPEC.md#66-gates-vocabulary-and-privacy-review) and [§6.13](../UI_UX_SPEC.md#613-documents). **Masking is not anonymity:** text that is only in an image is not read, and so not masked.

## What this phase delivers

- **Upload** (`POST /api/v1/projects/{project_id}/documents`, multipart, owners only): the type is read from the bytes; size, per-project quotas and a per-person hourly rate are enforced before anything is stored; the name is made safe to show and stored sealed; the file is stored sealed on a volume. The request returns `202` at once.
- **CSRF on the one non-JSON body:** multipart is accepted on the upload path alone, and the origin checks and the session's `X-CSRF-Token` still apply.
- **A worker** (`DocumentProcessor`, in `python -m app.worker` and the development in-process worker) claims each upload under a lease, scans it, extracts its text in a child process, masks it, and leaves it `awaiting_review`. A crashed worker's document is picked up again; after three tries it fails as `interrupted`.
- **Seams:** `FileStore` (memory, local volume), `MalwareScanner` (fake that finds EICAR, ClamAV's `clamd`, and an explicit `none`), `DocumentExtractor` (fake, in-thread local, isolated child process).
- **Masking review:** the masked text for any member (`…/masked`), the owner's review with raw text (`…/review`, audited), `…/masks` to mask a missed range, `…/unmask` to remove a mask with a reason, `…/approve` and `…/reject` naming the version.
- **Evidence:** approved documents' masked passages, tagged `D<n>`, in the pipeline's `ScreenedEvidence` (used by the brief stage and by follow-up questions).
- **The privacy_review gate** fed by the real queue of documents awaiting review.
- **Delete** (`DELETE …/documents/{document_id}`): rows, then the file; tags are never reused.
- **Endpoints:** ten, listed in [`api.md`](../api.md#documents).
- **Settings:** `HUMANI_T_DOCUMENT_STORAGE_DIR`, `HUMANI_T_UPLOAD_MAX_BYTES`, `HUMANI_T_DOCUMENTS_PER_PROJECT`, `HUMANI_T_DOCUMENT_BYTES_PER_PROJECT`, `HUMANI_T_UPLOADS_PER_HOUR`, `HUMANI_T_MALWARE_SCANNER`, `HUMANI_T_CLAMD_ADDRESS`, `HUMANI_T_EXTRACTION_TIMEOUT_SECONDS`, `HUMANI_T_EXTRACTION_MAX_PAGES`, `HUMANI_T_EXTRACTION_MAX_CHARACTERS`.
- **Migration** `0013`, with `down_revision` `0011` until the wave's migrations are chained at integration.

### Data model

| Table | Holds | Sealed (project key, bound to the row) | In the clear |
| --- | --- | --- | --- |
| `documents` | one row per document, also its processing job | `name_sealed` | number, type, size, status, failure code, scanner, extractor, page and character counts, masked counts by type, mask version, lease |
| `document_numbers` | the highest tag number a project used | — | `highest_number` |
| `document_texts` | the extracted raw text | `raw_sealed` | `page_starts` |
| `document_masks` | the current masked version | `masked_sealed`, `detail_sealed` (spans, removed masks with reasons, masked page starts), bound to the document and the version | `version`, `masked_with`, `masked_by` |
| `document_reviews` | who approved or rejected which version, when | — | ids, decision, version, time; no `UPDATE` for the app role; no foreign key to `documents`, so it outlives them |

The file itself is `<HUMANI_T_DOCUMENT_STORAGE_DIR>/<project id>/<document id>.sealed`, sealed with purpose `documents.file`.

## Architecture choices and why

### D16.1 Files sealed on a volume, not in an object store

- **Decision:** `LocalVolumeFileStore` writes ciphertext only, at a key of two UUIDs, atomically and never overwriting. The application seals each file under the project's data key with associated data naming the document.
- **Why:** one self-hosted stack with small volumes; backups of the volume hold ciphertext; deleting the project shreds every file (brief rule 17).
- **Rejected:** MinIO (community images no longer published, repository in maintenance mode), SeaweedFS (a distributed system for scale we do not have), Garage (the best fit if we need S3 later, but a second stateful service now), files in Postgres (bloats the database and its backups). See [ADR-0008](../adr/0008-uploaded-files-on-a-sealed-volume.md), which names what would trigger a move.
- **Costs:** the API and the worker must share the volume; deleting one document leaves copies in backups readable with the project's key until the retention period passes.
- **Where:** [`app/files/`](../../services/api/app/files/), [`application/documents.py`](../../services/api/app/application/documents.py) (`DocumentSeal`).
- **Proven by:** `tests/test_document_seams.py::TestFileStores`, `tests/test_documents_api.py::TestSealing`, `::TestDeletion`.

### D16.2 The upload is parsed strictly, in memory

- **Decision:** our own parser (`app/api/uploads.py`) accepts exactly one part named `file` with a filename, from a body already capped at `upload_max_bytes` + 64 KiB.
- **Why:** Starlette's form parser spools parts over 1 MiB to a temporary file, which would leave an unencrypted copy of a team's document on disk before anything checked or sealed it.
- **Rejected:** `python-multipart` through FastAPI's `UploadFile` (the spooling above, and one more dependency).
- **Costs:** a body is held in memory whole (20 MiB by default); the per-hour limit bounds how many at once one person can send.
- **Where:** [`app/api/uploads.py`](../../services/api/app/api/uploads.py), [`app/api/routes/documents.py`](../../services/api/app/api/routes/documents.py).
- **Proven by:** `tests/test_document_uploads.py::TestMultipartParser`, `::TestRefusals`.

### D16.3 CSRF for the one non-JSON body

- **Decision:** `CrossSiteRequestMiddleware` admits `multipart/form-data` only on `UPLOAD_PATH`; everywhere else the JSON-only rule stands. The origin checks and the per-session token apply to the upload as to every write.
- **Why:** a cross-site HTML form can send multipart, so the JSON-only layer cannot protect this route; the token in `X-CSRF-Token` is what a form can never send.
- **Rejected:** a separate upload token, or a second origin: more moving parts for the same guarantee.
- **Where:** [`api/middleware.py`](../../services/api/app/api/middleware.py), [`main.py`](../../services/api/app/main.py).
- **Proven by:** `tests/test_document_uploads.py::TestCrossSiteRequests`.

### D16.4 The type comes from the bytes

- **Decision:** `%PDF-` is a PDF; a zip with `[Content_Types].xml` and `word/document.xml` (and no macros) is a DOCX; UTF-8 text without control characters is text, and only then does the name decide Markdown or plain text, which are read the same way.
- **Why:** OWASP's file upload guidance; a misleading name or browser type must never choose a parser.
- **Rejected:** trusting `Content-Type` or the extension; `python-magic` (a native library for four types we can recognise in a few lines).
- **Costs:** XLSX, PPTX, EML and HTML (UI_UX_SPEC §6.13) are refused for now (G16.1).
- **Where:** [`domain/documents.py`](../../services/api/app/domain/documents.py) (`detect_document_type`).
- **Proven by:** `tests/test_documents_domain.py::TestTypeDetection`.

### D16.5 A small extractor, isolated; mmore later

- **Decision:** text and Markdown with the standard library, DOCX with `zipfile` and `expat` (no doctype allowed; members, sizes and ratios bounded), PDF with pypdf. Production runs it in a child process started with `-I`, an empty environment and an audit hook refusing network, processes and file writes, killed at the timeout.
- **Why:** the brief's rule 13; a thread cannot be stopped mid-parse; the child never holds a secret.
- **Rejected:** mmore now (a model stack and downloads for formats P2 does not accept), python-docx (lxml), PyMuPDF (native, AGPL). See [ADR-0009](../adr/0009-local-extraction-in-an-isolated-process.md).
- **Costs:** about half a second per document to start Python; text in images is not read.
- **Where:** [`app/extraction/`](../../services/api/app/extraction/).
- **Proven by:** `tests/test_document_extraction.py`.

### D16.6 Scanning is a decision the operator writes down

- **Decision:** `HUMANI_T_MALWARE_SCANNER=clamd` (with `HUMANI_T_CLAMD_ADDRESS`) or `none`. Unset outside production means not scanned; unset in production switches uploads off with a `503` naming the reason. Every document records `scanned_with`. An infected file is deleted unread; an unreachable scanner leaves the document queued.
- **Why:** the brief's rule 12, without shipping a ClamAV service that pulls signature updates from the internet on its own; the choice not to scan is visible on every file and in [`controls.md`](../security/controls.md).
- **Rejected:** a hosted scanning API (sends the raw file out of the deployment); a client library (`pyclamd`, `clamd`): the protocol is a few lines of `asyncio`.
- **Where:** [`app/malware/`](../../services/api/app/malware/), [`settings.py`](../../services/api/app/settings.py) (`uploads_disabled_reason`).
- **Proven by:** `tests/test_document_seams.py::TestClamd`, `tests/test_documents_settings.py::TestScannerChoice`, `tests/test_documents_api.py::TestProcessing::test_an_infected_file_is_deleted_unread_and_fails_plainly`.

### D16.7 Masked versions, and an approval that names one

- **Decision:** the worker stores masked version 1. Each correction (mask a range, remove a mask with a reason) is a new version, made from the unchanged raw text, the spans in force and the project's current rules, written only if the stored version is still the one the owner saw. An approval names the version; a later correction sends the document back to review, and the evidence uses a document only while its last review approves its current version.
- **Why:** architecture §3.9.1 (masking is derived and correctable) and the provenance test: who approved what, and when, six months later.
- **Rejected:** editing masked text directly; approving "the document" rather than a version.
- **Costs:** project rules added after an approval reach the document only at its next correction (G16.4).
- **Where:** [`domain/documents.py`](../../services/api/app/domain/documents.py) (`Document.corrected`, `Document.decided`, `removable_spans`), [`application/documents.py`](../../services/api/app/application/documents.py) (`_correct`, `decide`, `DocumentEvidence`).
- **Proven by:** `tests/test_documents_api.py::TestReview`, `::TestApproval`, `::TestEvidence`.

### D16.8 Documents as evidence without screening

- **Decision:** `ScreenedEvidence` takes an optional `DocumentPassageSource` and appends the project's approved documents after the run's included papers: chunks of about 1,200 characters cut at whitespace (never inside a placeholder), tag `D<number>`, `source_kind` `document`, locator `page N` / `pages N-M` for PDFs, `characters a-b` (masked text) otherwise.
- **Why:** the team chose these files, and an owner approved their masking; screening them against criteria written for papers would drop them for the wrong reasons. One evidence source means the brief and follow-up questions get them alike.
- **Rejected:** screening documents like papers; a separate evidence source the brief lane would have to merge.
- **Costs:** a brief's source details (`ScreenedSources`) know only papers, so a `D` tag shows no title yet (G16.5).
- **Where:** [`application/papers.py`](../../services/api/app/application/papers.py) (`ScreenedEvidence`), [`domain/documents.py`](../../services/api/app/domain/documents.py) (`document_passages`).
- **Proven by:** `tests/test_documents_api.py::TestEvidence`.

### D16.9 Deciding the last document resumes the run

- **Decision:** after an approval, rejection or deletion, if no document of the project waits for review, every open `privacy_review` gate of its runs is approved in the decider's name through `RunService.resolve_gate` (the same conditional write as `POST /gates/{gate_id}`). A gate approved while documents still wait lets the run continue without them.
- **Why:** UI_UX_SPEC §6.6, "Approving the last document resumes the run."
- **Where:** [`application/documents.py`](../../services/api/app/application/documents.py) (`_resume_runs`).
- **Proven by:** `tests/test_documents_gate.py`.

### Deliberately not used

| Approach | Why not |
| --- | --- |
| Pre-signed download URLs | They bypass membership and audit (architecture §3.3); there is no raw-file download at all |
| Low-confidence spans first | Detectors report no confidence yet (G16.3) |
| Re-masking approved documents when a rule changes | Not built in this wave (G16.4) |

## Libraries

### pypdf 6.21.4

- **What it does:** reads the text of each PDF page.
- **Why this one:** pure Python, BSD-3-Clause, no dependencies on Python 3.13, no network code, built-in caps on stream decompression.
- **Alternatives:** pdfminer.six, PyMuPDF, mmore.
- **Trade-offs:** it has had denial-of-service bugs on crafted files; the child process, its timeout and the page and character limits contain them.
- **What we gave up:** layout, tables and images.

No other library: DOCX, multipart and the ClamAV protocol use the standard library.

## Files to read, in order

From the rule outwards.

1. [`services/api/app/domain/documents.py`](../../services/api/app/domain/documents.py): types and detection, `Document` and its transitions, corrections, masked positions, passages.
2. [`services/api/app/files/base.py`](../../services/api/app/files/base.py), [`malware/base.py`](../../services/api/app/malware/base.py), [`extraction/base.py`](../../services/api/app/extraction/base.py): the three seams.
3. [`services/api/app/extraction/local.py`](../../services/api/app/extraction/local.py), then [`isolated.py`](../../services/api/app/extraction/isolated.py) and [`_child.py`](../../services/api/app/extraction/_child.py).
4. [`services/api/app/malware/clamd.py`](../../services/api/app/malware/clamd.py) and [`files/volume.py`](../../services/api/app/files/volume.py).
5. [`services/api/app/application/documents.py`](../../services/api/app/application/documents.py): `DocumentSeal`, `DocumentService`, `DocumentProcessor`, `DocumentReviewQueue`, `DocumentEvidence`.
6. [`services/api/app/api/uploads.py`](../../services/api/app/api/uploads.py), [`routes/documents.py`](../../services/api/app/api/routes/documents.py), [`schemas/documents.py`](../../services/api/app/api/schemas/documents.py), and the `multipart_paths`/`larger_limits` additions in [`middleware.py`](../../services/api/app/api/middleware.py).
7. [`services/api/app/storage/postgres/documents.py`](../../services/api/app/storage/postgres/documents.py) and [`migrations/versions/20261105_0013_documents.py`](../../services/api/migrations/versions/20261105_0013_documents.py).
8. [`services/api/app/api/dependencies.py`](../../services/api/app/api/dependencies.py): `_document_sanitizer`, `_file_store`, `_malware_scanner`, `_upload_policy`.

## Set up

Beyond [00 — Setup](00-setup.md): give uploads a directory, choose not to scan (development only), migrate, and run a worker.

```powershell
cd services\api
uv sync
New-Item -ItemType Directory -Force ..\..\.data\documents | Out-Null
$env:HUMANI_T_DOCUMENT_STORAGE_DIR = (Resolve-Path ..\..\.data\documents).Path
$env:HUMANI_T_MALWARE_SCANNER = 'none'
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In a second PowerShell window, with the same two `$env:` lines:

```powershell
cd services\api
$env:HUMANI_T_DOCUMENT_STORAGE_DIR = (Resolve-Path ..\..\.data\documents).Path
$env:HUMANI_T_MALWARE_SCANNER = 'none'
uv run --env-file .env python -m app.worker
```

Until the wave is integrated, `alembic upgrade head` reports several heads (0012, 0013 and 0014 all follow 0011); upgrade to `0013` explicitly if you test this lane alone. Have a project `{{project}}` with Amara (owner), Kofi (reviewer) and Maya (viewer), as in [07](07-projects-and-roles.md). Create a file `report.txt` holding: `Interview on 12 March 2024 with amina.yusuf@example.org. Kofi Mensah leads the team. MRN 448-1207 was referred.`

### Troubleshooting

- **`503` "the operator has not set a storage directory"**: `HUMANI_T_DOCUMENT_STORAGE_DIR` is not set in the API's window.
- **A document stays `uploaded`**: no worker is running, or it lacks the two variables.
- **`413` through Caddy**: the compose proxy still caps bodies at 1 MiB (G16.6); test against `uvicorn` directly.

## Manual tests

### T16.1 Upload and processing

- **Do:** In Postman, as Amara: `POST {{base}}/projects/{{project}}/documents`, Body → form-data, key `file` of type File, value `report.txt`, header `X-CSRF-Token: {{csrf}}`. Then `GET {{base}}/projects/{{project}}/documents` until the status changes.
- **Expect:** `202`, `status: "uploaded"`, `tag: "D1"`, a `Location` header. A few seconds later the listing shows `status: "awaiting_review"`, `scanned_with: "none"`, `mask_version: 1` and `masked_items` with `DATE`, `EMAIL` and `MEDICAL_RECORD` (and `PERSON` with the `ner` extra).
- **Proves:** D16.2, D16.5, D16.6, the broad list.
- **If it fails:** `415` means the body was not multipart; a stuck `uploaded` means no worker (see Troubleshooting).

### T16.2 Refusals

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.2.1 | Upload any `.exe` renamed `notes.txt` | `415`, nothing new in the listing | D16.4 | check `detect_document_type` |
| T16.2.2 | Upload an empty file | `422` | D16.4 | |
| T16.2.3 | Upload `report.txt` without the `X-CSRF-Token` header | `403` | D16.3 | check `api/session.py` |
| T16.2.4 | Same request with header `Origin: https://evil.example` | `403` | D16.3 | check `CrossSiteRequestMiddleware` |
| T16.2.5 | As Kofi (reviewer), upload | `403` | roles | check `require(Action.MANAGE_DOCUMENTS)` |
| T16.2.6 | As Tariq (not a member), upload | `404`, the same body as for a missing project | §5.5 | |

### T16.3 What the model will see, and the review

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.3.1 | As Maya, `GET …/documents/{{document}}/masked` | `200`; `[EMAIL_1]`, `[DATE_1]` in `masked_text`; no `amina`, no `448-1207`; each item's `sources` names `patterns@1` or `dates@1` | members see masked text only | any raw value here is a leak: stop and report |
| T16.3.2 | As Maya, `GET …/review` | `403` | raw text is owners only | |
| T16.3.3 | As Amara, `GET …/review` | `200` with `raw_text`, `spans` (email `can_remove: false`, date `can_remove: true`) and the "This view is recorded" notice | D16.7 | |
| T16.3.4 | pgAdmin: `SELECT action, target_id FROM audit_events ORDER BY sequence DESC LIMIT 3` | `document.raw_text_viewed` with the document's id; no name or text | audited by id | |

### T16.4 Corrections and approval

- **Do:** from T16.3.3, find the offset of `Kofi Mensah` in `raw_text` (`start`) and `POST …/masks` with `{"version": 1, "start": <start>, "end": <start + 11>, "entity_type": "PERSON"}`. Then `POST …/unmask` with the date span's `start` and `end`, `"version": 2` and `"reason": "Date of a public meeting"`. Then `POST …/approve` with `{"version": 1}`, and again with `{"version": 3}`.
- **Expect:** `mask_version` 2 then 3; `Kofi Mensah` gone from `masked_text`, the date back; `removed` lists the date with the reason. The first approve is `409`, the second `200` with `status: "approved"` and `last_review.mask_version: 3`.
- **Proves:** D16.7.
- **If it fails:** a `422` on `/unmask` means the range is not exactly a mask's; copy `start` and `end` from `spans`.

### T16.5 The privacy review gate

- **Do:** upload a second file and wait for `awaiting_review`. Create a review and `POST {{base}}/reviews/{{review}}/runs` with `{"stages": ["mask"]}`; follow `GET {{base}}/runs/{{run}}/events`. Then approve the document as in T16.4.
- **Expect:** `gate.required` with `kind: "privacy_review"` and the document's id and counts, no text; after the approval, `gate.resolved` with `decision: "approve"` and the run continues.
- **Proves:** D16.9.
- **If it fails:** a run that never pauses means no document was `awaiting_review` when the mask stage ran.

### T16.6 What is stored, and deleting

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.6.1 | Open the `.sealed` file under the documents directory in a text editor | unreadable bytes; no `amina` | D16.1 | |
| T16.6.2 | pgAdmin: `SELECT number, status, name_sealed, masked_counts FROM documents;` and `SELECT raw_sealed FROM document_texts;` | names and texts unreadable; counts only | sealed at rest | |
| T16.6.3 | `DELETE …/documents/{{document}}`, then upload again | `204`; the file is gone from the directory; the new upload is `D3`, not a reused tag | tags never reused | |
| T16.6.4 | pgAdmin as `humani_t_app`: `UPDATE document_reviews SET decision = 'reject';` | `permission denied` | reviews are provenance | the role script did not run |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T16.1 | ☐ | |
| T16.2 | ☐ | |
| T16.3 | ☐ | |
| T16.4 | ☐ | |
| T16.5 | ☐ | |
| T16.6 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Types from bytes, safe names, quotas, the document's life, corrections, positions, passages, dates | `tests/test_documents_domain.py` |
| Extraction within limits; XXE, entity bombs, zip bombs, encrypted and damaged files; the child killed at its timeout and refusing side effects | `tests/test_document_extraction.py` |
| File stores, the fake scanner, the ClamAV client against a loopback clamd | `tests/test_document_seams.py` |
| Multipart parsing, CSRF on the upload, every refusal before storage | `tests/test_document_uploads.py` |
| Processing, review, corrections, approval, evidence, deletion, sealing, audit, no connection held across slow work | `tests/test_documents_api.py` |
| The privacy_review gate with real documents | `tests/test_documents_gate.py` |
| Scanner and storage settings, and the production wiring | `tests/test_documents_settings.py` |
| Storage behaves the same on memory and Postgres | `tests/test_documents_storage_contract.py` |

```powershell
uv run pytest tests/test_documents_domain.py tests/test_document_extraction.py tests/test_document_seams.py tests/test_document_uploads.py tests/test_documents_api.py tests/test_documents_gate.py tests/test_documents_settings.py tests/test_documents_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G16.1 | XLSX, PPTX, EML and HTML are not accepted (UI_UX_SPEC §6.13 lists them) | teams convert those files first | XLSX and PPTX reuse the DOCX reader's checks; HTML and EML use the standard library's parsers |
| G16.2 | No OCR: text in images is not read | a scan fails with `no_text`; a photographed ID inside a PDF is never masked | OCR, or mmore, behind `DocumentExtractor` |
| G16.3 | No "low-confidence spans first": detectors report no confidence | the reviewer reads every span | add a confidence to `MaskSpan` from the name model |
| G16.4 | A project rule added after approval reaches a document only at its next correction | an approved document can still name someone a later rule masks | re-mask approved documents, adding masks only, when a rule is added |
| G16.5 | The brief's source details know only papers: a `D` tag has no title in the brief | the source panel shows the passage without a document name | the brief lane adds documents to its `SourceCatalogue` (deciding whether a file name may be shown) |
| G16.6 | Caddy caps `/api/*` bodies at 1 MiB | uploads over 1 MiB fail at the compose stack's edge | a `request_body` limit for the upload path in the Caddyfile |
| G16.7 | No ClamAV compose service | production must set `malware_scanner=none` or run its own clamd | add `clamav` to `compose.yaml` with a volume for signatures |
| G16.8 | Postgres contract tests were not run in this wave (no database) | a SQL mistake would show only at integration | run them against the lane database |
| G16.9 | Deleting a project does not delete its files from the volume; they are unreadable (shredded) but take space | disk use | a worker sweep of deleted projects' files |
| G16.10 | No threat model document exists yet; `scanner=none` is recorded in `controls.md` | — | add it to the threat model when it is written (M14) |

## Glossary

| Term | Meaning |
| --- | --- |
| **D tag** | `D` and a document's number in its project (`D3`); how a brief cites it. Never reused. |
| **Masked version** | One masking of a document; 1 from the worker, one more per correction. An approval names one. |
| **Not personal** | An owner's correction removing a mask, with a reason; never for structured identifiers or project rules. |
| **Sealed** | Encrypted with the project's data key and bound to its table, column and row. |
| **EICAR** | The industry's harmless test file that every malware scanner reports. |
