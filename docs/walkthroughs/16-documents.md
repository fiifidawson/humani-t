# 16 — Documents

> **Milestones:** UI_UX_SPEC §15 P2 (documents, privacy review) · **Status:** in progress (API done; on the web, upload, the list, the masked text, the owner's masking review with corrections, approve or reject, delete, the chat's gate card and the project's masking rules are built) · **Previous:** [15 — Exports and follow-up questions](15-exports-and-questions.md) · **Next:** none yet

Before this phase a team could only bring papers from the paper sources. Now an owner uploads the team's own files to a project (PDF, Word, plain text, Markdown). Each one is scanned for malware, read in a separate process that can do nothing but read, and masked with the broad list: names and places (with the name model installed), dates, contacts and identifiers, plus the project's rules. An owner then reviews the masked result: sees the original and every mask with what made it, masks what was missed, removes what is not personal with a reason, and approves or rejects the exact masked version they saw. Only an approved document becomes evidence, for the decision brief and follow-up questions, cited as `D1`, `D2`, … with a page or character range. A run that reaches the mask stage while documents wait pauses at the `privacy_review` gate, and deciding the last one resumes it. Everything is sealed under the project's key, and deleting the project shreds it. This serves the brief's rules 2, 3, 12, 13 and 17, [`architecture.md` §3.3 and §3.9.1](../architecture.md#391-masking-is-derived-reviewable-and-re-runnable) and UI_UX_SPEC [§6.6](../UI_UX_SPEC.md#66-gates-vocabulary-and-privacy-review) and [§6.13](../UI_UX_SPEC.md#613-documents). **Masking is not anonymity:** text that is only in an image is not read, and so not masked. With the project's masking off ([ADR 0014](../adr/0014-masking-is-a-project-choice.md), [10 — Masking](10-masking.md) D10.6), a document is used once scanned and read, as written apart from the project's rules, with no review and no gate.

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
- **Migration** `0013`, after `0012`.
- **The web screens** at `/p/:project/documents` ([`features/documents/Documents.tsx`](../../apps/web/src/features/documents/Documents.tsx)), opened from the Documents link in the project's header: upload by choosing or dropping a file (owners); the list with tag, name, size, who uploaded it and the status in words, fetched again every 3 seconds while a file is being processed; the masked text for any member; the masking review for owners, with the original beside the masked text, "Not personal" with a reason, masking a selection, "Always mask in this project", then approve or reject the version shown; delete, after a confirmation (owners). The chat's privacy review card lists the documents waiting and links owners to each review (D16.12). Project settings → Privacy lists, adds and removes the project's masking rules ([`features/projects/MaskingRules.tsx`](../../apps/web/src/features/projects/MaskingRules.tsx)).
- **ClamAV in the compose stack:** the `clamav` and `freshclam` services, in the opt-in profile `malware-scan` ([ADR-0012](../adr/0012-clamav-in-the-stack.md)), and a `documents` volume that the API and the worker share, so uploads work in the stack.

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
- **Why:** the brief's rule 12. The stack's own ClamAV is something the operator turns on (D16.10), because its updater talks to ClamAV's CDN. The choice not to scan is visible on every file and in [`controls.md`](../security/controls.md).
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

### D16.10 ClamAV in the stack, opt-in, split from its updater

- **Decision:** two services from the official `clamav/clamav:1.5.4-debian` image, pinned by digest, in the profile `malware-scan`. `clamav` runs clamd alone, on `scan`, an internal network only the worker joins. `freshclam` runs the updater alone, on `signatures`, a network of its own with a route out. Both run as `1000:1000` with a read-only filesystem and no capabilities; the image's root `/init` is bypassed, and our config is mounted read-only from [`infrastructure/clamav/`](../../infrastructure/clamav/). clamd reads the signature volume read-only and picks up freshclam's updates every 10 minutes. The operator sets `COMPOSE_PROFILES=malware-scan`, `HUMANI_T_MALWARE_SCANNER=clamd` and `HUMANI_T_CLAMD_ADDRESS=tcp://clamav:3310`.
- **Why:** clamd reads hostile files, so it gets no route anywhere and only the worker can reach it; the API, which the internet talks to, cannot. freshclam needs the internet but never sees a file. Opt-in, because freshclam's requests go to a destination the operator has not otherwise chosen, and clamd needs about 1 GiB of memory.
- **Rejected:** the image's `/init` as root (one container both reading files and talking to the internet); `NotifyClamd` (freshclam would need a route to clamd); the Alpine image (amd64 only); the `_base` tag (no signatures until the first download). See [ADR-0012](../adr/0012-clamav-in-the-stack.md), which leaves the default open for the owner.
- **Costs:** about 1.5 GB more memory; signatures from the CDN every two hours; uploads in production stay off until the operator chooses a scanner.
- **Where:** [`compose.yaml`](../../compose.yaml), [`infrastructure/clamav/`](../../infrastructure/clamav/), [`services/api/Dockerfile`](../../services/api/Dockerfile) (the documents directory), [`docs/deploy/self-hosting.md` §3.7](../deploy/self-hosting.md#37-scan-uploads-for-malware).
- **Proven by:** the compose smoke test ([`.github/workflows/compose.yml`](../../.github/workflows/compose.yml)): clamd is hardened like every container, the worker's real client gets `Eicar-Test-Signature` back, the API cannot connect to clamd, and clamd cannot resolve an internet name.

### D16.11 Deleting a document from the web app

- **Decision:** owners see **Delete** on every row, whatever the document's status, so a failed or rejected file can be removed and uploaded again. A dialog asks first: "This deletes the file and its text. A brief that already cites D1 keeps the passages it quoted. This can't be undone." The request is `DELETE …/documents/{document_id}` with the session's CSRF token, and needs no recent sign-in, as the API does not ask for one. Afterwards the list is fetched again and the page says "D1 report.txt is deleted." A `404` means another owner deleted it first, so the page says "… was already deleted." and refreshes; any other refusal shows in the dialog in the API's words.
- **Why:** UI_UX_SPEC §6.13 ("Deleting a document asks for confirmation and says the brief may cite it"). Two failure messages ("Delete the file and upload it again") already sent people to a delete the screen did not have.
- **Rejected:** asking for the password first, which the API does not require for this; typing the name to confirm, as for a project, since one document is far less to lose.
- **Where:** [`features/documents/Documents.tsx`](../../apps/web/src/features/documents/Documents.tsx) (`DeleteDialog`), [`test/fakeDocuments.ts`](../../apps/web/src/test/fakeDocuments.ts).
- **Proven by:** `Documents.test.tsx` › "deleting a document".

### D16.12 The masking review in the browser

- **Decision:** an owner who opens a document waiting for review gets `GET …/review`: the original text with every mask in force marked, the masked text beside it, and a list of masks. Each mask says what it hides, its type and what made it ("Found automatically", "Masked by an owner", "Project rule"). **Not personal** appears only where the API's `can_remove` says so, and asks for a one-line reason before `POST …/unmask`. Missed text is masked by selecting it in the original: the page reads the selection's offsets from the runs of text it drew, offers **Mask as** a type, and sends `POST …/masks`. **Always mask in this project** adds a masking rule, then masks that range, because a new rule reaches a document only at its next correction; that correction masks every other occurrence before approval. Approve and reject send the `mask_version` on screen. Members, and owners reading a document that is not waiting, get `…/masked` only. The chat's gate card reads the gate's `payload.documents` (ids and counts), takes names from the documents listing, and links owners to `/p/:project/documents?document=<id>`.
- **Why:** UI_UX_SPEC §6.6 and §5.3.6. The API audits every fetch of raw text and every listing of rules, so both queries are fetched once per opening, never on focus or reconnect, kept outside the project's query keys (renaming the project or refreshing the list must not fetch them again), and dropped from memory as soon as nothing shows them (`gcTime: 0`). A correction's response replaces the review in the cache rather than fetching it again.
- **Rejected:** typing the missed text into a field and searching for it in the browser, which would have to guess which occurrence was meant; a pop-up menu at the selection, which is harder to reach by keyboard than a panel that stays below the text. Selecting text needs a mouse or caret browsing (F7); the list of masks works by keyboard alone.
- **Where:** [`features/documents/MaskingReview.tsx`](../../apps/web/src/features/documents/MaskingReview.tsx), [`features/documents/Documents.tsx`](../../apps/web/src/features/documents/Documents.tsx) (`MaskedReview`), [`features/reviews/PrivacyGateCard.tsx`](../../apps/web/src/features/reviews/PrivacyGateCard.tsx), [`app/documentQueries.ts`](../../apps/web/src/app/documentQueries.ts).
- **Proven by:** `Documents.test.tsx` › "the owner's privacy review", `PrivacyGate.test.tsx`, `MaskingRules.test.tsx`.

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

Beyond [00 — Setup](00-setup.md): give uploads a directory, choose not to scan (development only), migrate, and run a worker. Create the folder, then add two lines to `services\api\.env`, as [S0.13b](00-setup.md#s013b-real-mode-on-this-machine-a-real-model-and-real-paper-sources) says, with forward slashes in the path (uv's `.env` reader rejects backslashes):

```text
HUMANI_T_DOCUMENT_STORAGE_DIR=C:/Users/<you>/humani-t-documents
HUMANI_T_MALWARE_SCANNER=none
```

```powershell
New-Item -ItemType Directory -Force $HOME\humani-t-documents | Out-Null
cd services\api
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In a second PowerShell window:

```powershell
cd services\api
uv run --env-file .env python -m app.worker
```

For the web screens, run `pnpm --filter @humani-t/web dev` from the repository root in a third window and open `http://localhost:5173`. Have a project `{{project}}` with Amara (owner), Kofi (reviewer) and Maya (viewer), as in [07](07-projects-and-roles.md). Create a file `report.txt` holding: `Interview on 12 March 2024 with amina.yusuf@example.org. Kofi Mensah leads the team. MRN 448-1207 was referred.`

### Troubleshooting

- **`503` "the operator has not set a storage directory"**: `HUMANI_T_DOCUMENT_STORAGE_DIR` is not in `services\api\.env`, or a window still holds an old `$env:` value (`Remove-Item Env:HUMANI_T_* -ErrorAction SilentlyContinue`).
- **A document stays `uploaded`**: no worker is running, or it was started before the two lines were added. In the compose stack, the worker cannot reach clamd: see [self-hosting §9](../deploy/self-hosting.md#9-troubleshooting).

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

### T16.7 Deleting from the web app

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.7.1 | As Amara, on the Documents page, click **Delete** on D1's row | A dialog "Delete D1 report.txt?" saying a brief that already cites D1 keeps the passages it quoted, and that this can't be undone | D16.11 | |
| T16.7.2 | Click **Cancel** | The dialog closes; D1 is still listed; no `DELETE` in the API log | D16.11 | |
| T16.7.3 | Open it again and click **Delete** | The dialog closes, "D1 report.txt is deleted." shows above the list, D1 leaves it; the API logs `204`; the `.sealed` file is gone from the documents folder | D16.11, T16.6.3 | a `403` means Amara is not an owner of this project |
| T16.7.4 | As Kofi (reviewer), open the Documents page | No **Delete** button on any row | roles | the API would refuse with `403` anyway |

### T16.8 ClamAV in the compose stack

With the stack running as in [self-hosting §3.7](../deploy/self-hosting.md#37-scan-uploads-for-malware) (profile `malware-scan`, `HUMANI_T_MALWARE_SCANNER=clamd`, `HUMANI_T_CLAMD_ADDRESS=tcp://clamav:3310`). The EICAR test string is built in Python from the app's own constant, so no test file sits on your disk for a desktop virus scanner to quarantine.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.8.1 | `docker compose ps` | `clamav` and `freshclam` `healthy` (clamav after a minute or two) | D16.10 | `docker compose logs clamav` |
| T16.8.2 | `docker compose exec -T worker python -c "import asyncio; from app.malware.clamd import ClamdAddress, ClamdScanner; from app.malware.fake import EICAR; print(asyncio.run(ClamdScanner(ClamdAddress.parse('tcp://clamav:3310')).scan(EICAR)))"` | `ScanVerdict(outcome=<ScanOutcome.INFECTED: 'infected'>, scanner='clamd', signature='Eicar-Test-Signature')` | the worker reaches clamd, which finds the test file | |
| T16.8.3 | The same, with `.scan(b'An ordinary report.')` | `outcome=<ScanOutcome.CLEAN: 'clean'>` | | |
| T16.8.4 | The same command in `api` instead of `worker` | `MalwareScanUnavailableError: clamd could not be reached`, never a verdict | only the worker reaches clamd | check the `api` service's `networks` |
| T16.8.5 | `docker compose stop clamav`, upload `report.txt`, wait a minute, then `docker compose start clamav` | The document stays at "Checking for viruses" while clamd is down (the worker logs `malware_scanner_unavailable`), then moves on once clamd is healthy | fails closed | |

### T16.9 The masking review on the web

Start with a fresh `report.txt` uploaded as Amara and waiting for review, a review whose run has paused at the privacy review (as in T16.5), and the web app open.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T16.9.1 | As Amara, open the review's chat | The "Needs you · Privacy review" card lists "D1 report.txt · N masked" and a **Review masking** link | D16.12 | the card says "A document": the listing failed; check the network tab |
| T16.9.2 | Click **Review masking** | The Documents page opens at `?document=<id>` with "Privacy review: D1 report.txt", the notice "You're seeing original text. This view is recorded…", **Original text** with the masked parts highlighted, and **What the model will see** beside it | D16.12 | |
| T16.9.3 | pgAdmin: `SELECT action FROM audit_events ORDER BY sequence DESC LIMIT 1`; then switch to another browser tab and back, and run it again | `document.raw_text_viewed`, and no new row after switching tabs | audited once per opening | a new row after switching means the query refetches on focus |
| T16.9.4 | In the Masks list, look at the email's row and the date's row | The email has no **Not personal** button; the date has one | `can_remove` | |
| T16.9.5 | Click **Not personal** on the date, type `Date of a public meeting`, click **Remove mask** | "“12 March 2024” is no longer masked."; the date is back in the masked text; the API logs `POST …/unmask` `200` | D16.7 | a `422` names the reason or the range |
| T16.9.6 | Select `the team` in the original with the mouse, choose **Mask as** Person, click **Mask it** | "“the team” is masked."; a new placeholder in the masked text; `POST …/masks` `200` | D16.7 | |
| T16.9.7 | On a person's row click **Always mask in this project** | "“…” is now masked everywhere in this project."; `POST …/masking-rules` `201` then `POST …/masks` `200`; the row says "Project rule" | D16.12 | `409` means the rule already exists |
| T16.9.8 | Click **Approve** | "Approved. The brief can now cite it as D1."; the row says Ready; the paused run continues; `last_review.mask_version` is the version after your corrections | D16.7, D16.9 | `409`: someone corrected it meanwhile; click **Try again** on the message |
| T16.9.9 | As Kofi (reviewer), open the run's chat and the Documents page | The card lists the document with "Only owners can do this." and no link; **See masked text** shows no original; no `…/review` request in the network tab | roles | |
| T16.9.10 | As Amara, open Project settings → **Privacy** | The rule from T16.9.7 is listed; the rest is T10.4 | D16.12 | |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T16.1 | ☐ | |
| T16.2 | ☐ | |
| T16.3 | ☐ | |
| T16.4 | ☐ | |
| T16.5 | ☐ | |
| T16.6 | ☐ | |
| T16.7 | ☐ | |
| T16.8 | ☐ | |
| T16.9 | ☐ | |

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
| The web screens: upload, status, the masking review and its corrections, delete (owners only, confirmed), and no accessibility violations | `apps/web/src/features/documents/Documents.test.tsx` |
| The chat's privacy review card lists the waiting documents and links owners to each review | `apps/web/src/features/reviews/PrivacyGate.test.tsx` |
| Project settings → Privacy: list, add and remove masking rules, owners only | `apps/web/src/features/projects/MaskingRules.test.tsx` |
| clamd in the stack is hardened, reachable from the worker only, finds EICAR, and reaches nothing | `.github/workflows/compose.yml` (compose smoke test) |

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
| G16.6 | Closed: the Caddyfile gives the upload path its own limit (21 MiB) | — | — |
| G16.7 | Closed: `clamav` and `freshclam` are in `compose.yaml`, profile `malware-scan` (D16.10) | — | — |
| G16.8 | Postgres contract tests were not run in this wave (no database) | a SQL mistake would show only at integration | run them against the lane database |
| G16.9 | Deleting a project does not delete its files from the volume; they are unreadable (shredded) but take space | disk use | a worker sweep of deleted projects' files |
| G16.10 | No threat model document exists yet; `scanner=none` is recorded in `controls.md` | — | add it to the threat model when it is written (M14) |
| G16.11 | Closed: the masking review, its corrections and the gate card are on the web screens (D16.12, T16.9) | — | — |
| G16.12 | ClamAV in the stack is opt-in | a new production deployment refuses uploads until the operator chooses a scanner | the owner's decision, open in ADR-0012 |
| G16.13 | `FakeMalwareScanner` finds EICAR anywhere in a file; clamd finds it only as the whole file or at its start, as the EICAR standard defines | a test that passes with the fake can need the bare string against real clamd (T16.8 uses it) | make the fake match clamd, and the tests that put text before EICAR send it bare |

## Glossary

| Term | Meaning |
| --- | --- |
| **D tag** | `D` and a document's number in its project (`D3`); how a brief cites it. Never reused. |
| **Masked version** | One masking of a document; 1 from the worker, one more per correction. An approval names one. |
| **Not personal** | An owner's correction removing a mask, with a reason; never for structured identifiers or project rules. |
| **Sealed** | Encrypted with the project's data key and bound to its table, column and row. |
| **EICAR** | The industry's harmless test file that every malware scanner reports. |
