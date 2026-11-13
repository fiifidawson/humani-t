# Architecture

This document records the decisions that must be stable **before** feature work begins. Everything here serves one rule:

> humani-t is a self-hosted web application. Data leaves the deployment only to destinations the operator has configured. Text sent to a model the deployment does not run itself is masked first, unless both the operator and the project's owner have chosen otherwise.

[ADR-0001](./adr/0001-web-only-self-hosted.md) explains why there is one deployment shape and no desktop version.

---

## 1. Layering

```text
Presentation          apps/web                       (React SPA, no business logic)
    |
Application API       services/api/app/api           (HTTP boundary, versioned)
    |
Application logic     services/api/app/application   (use cases, orchestration)
    |
Domain                services/api/app/domain        (entities, rules, no I/O)
    |
Providers             services/api/app/<seam>/       (Protocol + implementations)
```

Dependencies point **downward only**. Concretely:

- The frontend never imports a database, an SDK, a filesystem path or a model name. It is untrusted. Every rule is enforced by the API.
- `domain/` imports nothing from providers, `api/` or any third-party SDK.
- `application/` depends on provider *interfaces*, never on a concrete implementation.
- Only provider packages and the composition root (`api/dependencies.py`) know that PostgreSQL, Valkey, an LLM vendor or an identity provider exist.

The forbidden shape is a route that reaches straight through:

```text
Route -> SQL -> LLM SDK        # untestable, and impossible to audit
```

---

## 2. Deployment

One shape, run by the organisation on infrastructure it controls:

```text
                 internet
                    |
               [ caddy ]            HTTPS (automatic certificates), HSTS,
               /       \            serves the built SPA, proxies /api
     static SPA         [ api ]     FastAPI, uvicorn, non-root, read-only FS
                          |   \
                 [ postgres ] [ valkey ]      data            rate limits,
                          |                                  challenges, queue
                     [ worker ]     background jobs: discovery, masking,
                                    screening, indexing, expiry, audit seal
                     [ migrate ]    one-shot: Alembic as the schema owner role
```

- Only Caddy publishes a port. Every other service is on an internal network.
- The SPA and the API share one origin, so there is no CORS configuration to get wrong.
- Secrets reach containers as Docker secrets (`/run/secrets/*`), never as plain environment variables or files in the image.
- Outbound traffic is limited to the paper sources, PDF hosts, the configured LLM provider and the configured identity provider, and goes through `HttpFetcher` (§5.6).

Uploaded documents are kept sealed on a volume shared by the API and the worker ([ADR-0008](adr/0008-uploaded-files-on-a-sealed-volume.md)) and scanned by the operator's own ClamAV daemon, or explicitly not at all (`malware_scanner=none`); production refuses uploads until one of the two is chosen. A ClamAV compose service is not shipped yet.

---

## 3. Provider interfaces

Each seam is a `Protocol` in `services/api/app/<area>/base.py`, with a default implementation and a fake. The seams exist so that:

- the application is testable without a network;
- an organisation can choose its own model, identity provider and key store without touching a use case.

| Interface | Default implementation | Fake |
| --- | --- | --- |
| `InferenceProvider` | Ollama, run by the operator (native `/api/chat`, JSON-schema output); or, with `inference_api=openai`, any server speaking OpenAI's Chat Completions. A hosted one only with `allow_hosted_inference` (ADR 0010) | scripted answers, validated against the schema |
| `PromptGuard` | `LeakCheckPromptGuard`: the pattern detector over every outgoing message, plus the project's rules; any hit refuses the call | allows, refuses or fails on demand |
| `StorageProvider` | `PostgresStorage` (SQLAlchemy 2, async) | in-memory repositories |
| `FileStore` | a local volume, ciphertext only ([ADR-0008](adr/0008-uploaded-files-on-a-sealed-volume.md)) | in-memory |
| `MalwareScanner` | ClamAV's daemon over `INSTREAM`, or `none` by explicit choice | finds the EICAR test file |
| `DocumentExtractor` | standard library and pypdf in a child process ([ADR-0009](adr/0009-local-extraction-in-an-isolated-process.md)); mmore later | reads bytes as UTF-8, or fails as told |
| `PaperSearchProvider` | OpenAlex, Europe PMC, arXiv | `FakePaperSearchProvider` |
| `KeyProvider` | file-backed KEK (Docker secret) | fixed test key |
| `PasswordHasher` | Argon2id | fast deterministic hasher |
| `PasskeyVerifier` | WebAuthn (py_webauthn), in `auth/webauthn.py` | none needed: it makes no network call, so tests drive the real one with a software authenticator (`tests/webauthn_authenticator.py`) |
| `EmailProvider` | SMTP | records sent messages |
| `RateLimiter` | Valkey | in-memory |
| `HttpFetcher` | SSRF-guarded HTTP client | scripted resolver and responses |

TOTP is not a seam. RFC 6238 is a few lines of the standard library's `hmac`, so it lives in the domain (`domain/mfa.py`), next to the replay rule it must obey, and is tested against the RFC's own vectors.

### 3.1 InferenceProvider

```python
class InferenceProvider(Protocol):
    @property
    def model_id(self) -> str: ...
    async def chat(self, request: ChatRequest) -> ChatResult: ...

class PromptGuard(Protocol):
    async def inspect(self, request: ChatRequest) -> PromptVerdict: ...
```

`ChatRequest` carries messages, the JSON schema the answer must match and generation parameters as **domain types** (`domain/inference.py`), never a provider's wire format. Every call asks for structured output: free text cannot be checked, cited or stored as a decision. `ChatResult` carries the validated answer, the model id and the token usage, because a `RunManifest` must name the model that produced a decision.

Use cases never hold a bare provider. They get `GuardedInference` (`application/inference.py`), which runs the `PromptGuard` over the exact request first and fails closed: a refusal or an error in the guard means nothing is sent. Every text has already been masked (§3.9); the guard is the last check on what leaves (§3.9.1).

The default implementation calls an Ollama server the operator runs, through `HttpFetcher` with an allowlist entry for that one host. Settings refuse known hosted model APIs, and Ollama `*-cloud` models, unless the operator sets `allow_hosted_inference`. Streaming and model listing are not in the interface until a feature needs them.

### 3.2 StorageProvider

Persistence is exposed as repositories behind a unit of work, not as a connection.

```python
class StorageProvider(Protocol):
    def unit_of_work(self) -> AsyncContextManager[UnitOfWork]: ...

class UnitOfWork(Protocol):
    users: UserRepository
    sessions: SessionRepository
    projects: ProjectRepository
    audit: AuditRepository
    reviews: ReviewRepository
    vocabularies: VocabularyRepository
    papers: PaperRecordRepository
    screenings: ScreeningDecisionRepository
    briefs: DecisionBriefRepository
    runs: RunManifestRepository
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

Constraint: no raw SQL and no SQLAlchemy types above `storage/`. Use cases are tested against the in-memory implementation. A shared contract test suite runs against both implementations, so the fake cannot drift from Postgres.

### 3.3 FileStorageProvider

```python
class FileStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...   # never overwrites
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> bool: ...
```

Built as `FileStore` (`app/files/`), with whole sealed files rather than streams: files are at most `upload_max_bytes` (20 MiB by default), and a key is `<project id>/<document id>`, nothing else ([ADR-0008](adr/0008-uploaded-files-on-a-sealed-volume.md)).

There is no pre-signed URL method. Files are always streamed through an API route that checks membership and writes an audit event. A URL that bypasses the API would bypass both.

Stored bytes are encrypted with the project's data key (§5.4) before `put`, bound to the document's id.

### 3.4 Planned, not built

These seams are designed but have no code yet. Each will get a `Protocol` and a fake when a feature needs it.

- `EmbeddingProvider`: a local embedding model. Stored vectors record the model id and dimensions, because vectors from different models are not comparable.
- `VectorStoreProvider`: Milvus through mmore, one collection per project. The brief uses BM25 per search instead ([ADR-0007](adr/0007-bm25-retrieval-for-the-brief.md)).
- `ScreeningProvider`: not needed so far. Screening is the `screen` stage calling `InferenceProvider` (§3.8).
- `SanitizationProvider`: mmore's privacy mode. Masking uses `Detector` and `Sanitizer` in `app/privacy/` instead (§3.9).
- `ToolProvider`: tools for the agent loop, each checked against the caller's identity and project role.
- `IdentityProvider`: OIDC sign-in, optional.

Later sections keep their numbers, which other documents cite.

### 3.7 PaperSearchProvider

One interface over OpenAlex, Europe PMC and arXiv. Each has a different query syntax, rate limit and metadata shape. None of that belongs in the application layer.

```python
class PaperSearchProvider(Protocol):
    @property
    def source_id(self) -> str: ...
    async def search(self, query: BooleanQuery, limit: int) -> Sequence[PaperRecord]: ...
```

`BooleanQuery` is a **domain type**, not a source-specific string. The provider compiles it into that repository's syntax. This seam makes a search reproducible across sources. It is the single most important interface in the system, because everything downstream inherits its provenance.

### 3.8 Screening

The `screen` stage asks the model through `GuardedInference` (§3.1). There is no separate screening seam (§4.2).

`ScreeningDecision` records:

- the score, the priority and the rationale;
- **the provenance**: model id, prompt version and criteria version.

Auditability is a stated project commitment. A reviewer must be able to ask why a paper was excluded and get an answer that names what produced the decision.

Screening never makes the final call. The stage records an assessment; the domain decides what a human still has to see.

### 3.9 Masking

mmore ships a privacy mode (`src/mmore/privacy/`) with `humanitarian` as a domain profile. It is not used yet (§3.4); if it is, it sits behind our own interface, never `mmore rag --privacy`, with these choices.

**Locked configuration choices:**

| Key | Value | Reason |
| --- | --- | --- |
| `domain` | `humanitarian` | Built for exactly this setting |
| `detection.engine` | `presidio` or `gliner` | Both run locally. **Never `openai_filter`**: it sends unsanitised text to OpenAI, which defeats the purpose |
| `sanitization.strategy` | `entity_replacement` or `synthetic_rewrite` | `token_masking` degrades screening and the decision brief |
| `answer.llm` | our `InferenceProvider` default | Never a third-party default |
| `abort_on_exhaustion` | `true` | Fail closed. Keep the default |
| `verifier.checks` | `residual_leakage`, `faithfulness` | Both |

**Masking happens at ingest as well as at query time.** mmore's pipeline sanitises retrieved chunks; it does not change what is stored. The brief's rules 1 to 3 therefore require our own masking stage between extraction and indexing. Without it, raw text would sit in the index and the database:

- The question, clarifying answers and change requests are masked before any LLM call.
- Uploaded documents are masked with the broad entity list, and their masking cannot be turned off document by document.
- Raw text stays in a separate store, encrypted with the project's data key, and has a retention period.
- All of this holds while the project's masking applies. Masking is a project's choice within the operator's policy ([ADR-0014](adr/0014-masking-is-a-project-choice.md), §5.8): with it off, only the project's rules mask text.

#### 3.9.1 Masking is derived, reviewable and re-runnable

Detection misses things (brief rule 5), so the design assumes it will be wrong and makes every result checkable and correctable. For each document we store:

```text
raw text      encrypted with the project's data key; never modified
mask spans    (start, end, entity type, source) over the raw text, where source is
              a detector and its version ("presidio@2.3") or a user ("user:<id>")
masked text   raw text with the spans applied; rebuilt whenever the spans change
```

Masking is a function of the raw text and the spans, never an edit. That gives:

- **Several detectors, combined.** Pattern rules for structured identifiers (medical record, phone and ID numbers), a local NER model (Presidio or GLiNER) for names and places, and mmore's leakage check as a second pass. A span found by any of them is masked: over-masking costs some quality, under-masking leaks.
- **Re-runs.** When a detector is upgraded, it runs again over the raw text and only new spans are proposed. Spans added by people are kept, because they point into text that never changes.
- **Project rules.** "Always mask this term in this project" (`MaskingRule`) applies to every current and future document, such as a list of staff names.
- **A last check on what leaves.** Immediately before any `InferenceProvider` call, the exact outgoing text is scanned again. A hit stops the call: fail closed.
- **Provenance.** Every span names what produced it, so "why was this masked, and by what?" has an answer.

**Human review before use.** An uploaded document is not indexed and not sent to any model until a person approves its masking. A correction made after text reached a model cannot be recalled, so the check must come first. The review screen:

- shows the extracted text with masked spans highlighted by type;
- lists the detector's low-confidence spans first, so review is short;
- lets the reviewer select missed text and mask it as a chosen type, or turn it into a project rule;
- lets the owner mark a false positive as "not personal". This is audited and never allowed for structured identifiers. Rule 3 means masking cannot be switched off for one document; this is a narrow correction, not an off switch. Only the project's own masking switch turns masking off, for every text of the project at once (ADR 0014);
- re-masks and re-indexes only the changed document after a correction.

**Who sees raw text.** Only the document's owner, and only in the review screen. Reviewers and viewers see masked text. Each view of raw text writes an `AuditEvent`. The screen reviews *extracted text*, not the rendered file: text visible only in an image (a scanned signature, a photographed ID) is masked only if OCR extracted it, and the screen says so.

> **Decided for P1** (walkthrough [10](walkthroughs/10-masking.md)):
>
> - **Where it runs:** a standalone `mask` stage in the worker (`application/masking.py`), not mmore's agents. Detectors sit behind `Detector` and `Sanitizer` in `app/privacy/`: a standard-library pattern detector, always on, and spaCy's `en_core_web_sm` for names, places and dates, an optional `ner` extra, loaded only from this machine. GLiNER stays a possible second NER detector behind the same Protocol.
> - **How broad for papers:** the narrow list, structured identifiers only, and no human review. Names and places in published abstracts are evidence; masking "Marib" would break screening against a setting criterion. Questions add names and places; uploaded documents (P2) add dates too.
> - **The last check:** `LeakCheckPromptGuard` runs the pattern detector over every outgoing message and the schema; placeholders pass, anything else refuses the call with types and counts only. `GuardedInference.with_rules` adds a project's rules. It does not run the name model, which would refuse the author and place names that papers legitimately contain.
> - **Provenance:** a span's source is `detector@version`, `user:<id>` or `rule:<id>`. A paper's stored masking records the detectors and rules it was made with, and a re-run masks again only when they changed.
>
> **Still open:** whether mmore's leakage check runs as a second pass.
>
> **Decided for P2** (walkthrough [16](walkthroughs/16-documents.md)): uploaded documents are masked by the worker with the broad list (patterns, a standard-library date detector, the name model when installed, and project rules) and wait for an owner. Corrections make a new masked version; an approval names the version it approves, and a later correction withdraws it. Approved documents are added to every run's evidence after the included papers, as passages tagged `D<number>` with a page or character-range locator; BM25 searches them as written, placeholders included. Deciding the last waiting document approves the project's open `privacy_review` gates. Low-confidence spans first is not built: detectors report no confidence yet.

**The HITL gate shapes the API contract.** A gated request pauses until a human approves, which a plain request/response cannot express. See §4.4.

---

## 4. Domain model

### 4.1 Access and accountability

These entities exist before any review does, and every other entity hangs off them:

```text
User            an account: email, password hash, status, instance role
TotpCredential  an authenticator app: its secret, encrypted under the user's
                own data key, and the last time step used (the replay guard)
Passkey         a WebAuthn credential: id, public key, signature counter, name
RecoveryCode    a single-use fallback code, stored only as an HMAC keyed by a
                subkey of the user's data key
WebAuthnChallenge  a challenge issued to one session for one ceremony;
                spent by its first answer, gone after 5 minutes
Identity        a link to an external OIDC account, keyed by (issuer, subject)
Session         a signed-in browser: token hash, assurance level (aal 1 | 2),
                whether it is pending a second factor, expiry
Project         the unit of ownership, sharing, encryption and deletion;
                owns one or more Reviews. Its name is stored only sealed
                under its own data key; archived and deleted are dates, and
                a deleted project stays as a tombstone row. is_masking_on is
                the owner's masking switch (ADR-0014)
MaskingPolicy   the operator's side of the masking choice, built from
                settings: whether the model is hosted, and whether owners may
                send it unmasked text. Not stored
Membership      (User, Project, Role) where Role is owner | reviewer | viewer;
                removed when the project is deleted
AuditEvent      who did what to which object, when; hash-chained
```

A **Project** is what the brief calls a project. It is the boundary for three things:

- **access control:** every request checks membership;
- **encryption:** one data key per project;
- **deletion:** destroying the key makes the project's data unreadable.

The fixed review vocabulary below (`Review`, `Vocabulary`, …) lives inside a Project.

A project's status (planning, running, needs you, brief ready, …) is not stored: `project_status` in the domain derives it from the newest review and its latest run each time a project is read ([UI_UX_SPEC.md](UI_UX_SPEC.md) §11.1).

Whether a project's text is masked is never read from storage alone. `is_masking_applied(is_masking_on, policy)` decides it each time text is about to leave: masked when the switch is on, or when the model is hosted and the operator has not allowed unmasked text ([ADR-0014](adr/0014-masking-is-a-project-choice.md)).

Role permissions are a pure domain function, `can(role, action)`. It is tested as a table, so a change to who may do what is a visible diff. The actions follow [UI_UX_SPEC.md](UI_UX_SPEC.md) §3.3: viewers read and may leave; reviewers also search, run, confirm gates, relabel and ask; only owners rename, archive, manage members and documents, see raw text and delete. Being an instance administrator grants nothing inside a project.

Membership changes on one project take the project's row lock first, so the last-owner rule holds under concurrent requests ([ADR-0002](adr/0002-project-row-lock-for-membership-changes.md)).

### 4.2 Reviews

The spine of the application is a reproducible review run:

```text
Review          a question, a Vocabulary, and a set of Criteria
  Vocabulary    curated concepts with synonyms, grouped into themes
  BooleanQuery  generated from the Vocabulary, compiled per source
  PaperRecord   bibliographic metadata + source + dedup identity
  Document      a file the team uploaded to the project: number (its tag, D3),
                sealed name, type read from its bytes, status (uploaded,
                scanning, extracting, masking, awaiting_review, approved,
                rejected, failed); a row that is also its processing job
    DocumentText  its extracted raw text, sealed, and where each page starts;
                read only by masking and the owner's review
    DocumentMask  one masked version: masked text and its detail (spans in
                force, masks removed with who and why, page starts), sealed
                and bound to the version
    DocumentReview  who approved or rejected which masked version, and when;
                written once, outlives the document
  MaskSpan      a masked range of a Document's raw text: type and source
  MaskingRule   a project-wide term that is always masked
  PaperMask     a paper's masked text and spans, sealed, with the detectors
                and rules they were made with; the raw text is kept apart
  Criterion     an inclusion/exclusion rule
  ReviewPlan    one numbered version of a review's Vocabulary and Criteria,
                with its author (the model or a person), the clarifying
                answers and the ChangeRequest it came from; accepted once,
                then frozen
    PlanDiff    what one version added or removed, and who wrote it
  PlanConversation  the clarifying questions being answered, and a
                ChangeRequest while the model drafts from it
  ChangeRequest what a person asked the model to change, and the version
                they were looking at
  PrivacyReceipt  what one model call received of the people's text:
                masked text and counts by entity type, and the plan
                version and change request the call was drafting
  ScreeningDecision  score, priority, rationale, provenance
  DecisionBrief the agent loop's output for one run: options, trade-offs,
                conflicts, considerations, gaps; one version per run
    Claim       a statement in the brief, marked evidence or hypothesis
    Citation    a Claim bound to one retrieved passage, from a paper
                (PaperRecord) or from a team's uploaded Document
  AgentStep     one step of the loop that wrote a brief: round, role, status,
                timings, tokens; a brief's AgentSteps are its trace
  RunManifest   everything needed to reproduce or diff a run
  Run           one background execution of some of a Review's stages; its
                row is also its job in the queue (§4.4)
    RunEvent    one entry in a run's log: an id counting from 1 within the
                run, a name and an encrypted payload
    Gate        a pause in a run until a person decides: vocabulary or
                privacy_review; resolved once, the first decision wins
```

**The decision brief** is how humani-t presents its evidence synthesis. It compares the options a decision-maker faces instead of summarising the literature. Its fields follow the CHITCHAT prototype (`src/chitchat/agents/schemas.py`):

- **Options:** 2 to 4. Each has benefits and risks as Claims, and an evidence strength: `strong`, `moderate`, `weak` or `insufficient`.
- **Trade-offs:** one row per criterion, with one assessment per option. The relevant humanitarian principles are always among the criteria.
- **Conflicts:** where sources disagree, typed as `hierarchy` (stronger vs weaker designs), `context` (different settings) or `noise` (small, old or unclear studies).
- **Considerations:** "if this matters most, prefer that option, because …".
- **Gaps:** what the evidence doesn't cover.
- **Bottom line** (2 or 3 sentences), a fixed caveat, the model, the rounds and why the loop stopped.

Claims are either `evidence`, citing passages the loop retrieved, or `hypothesis`, the model's reasoning with no citation. Code, not the model, checks that every cited passage was retrieved. A `Citation` records whether its passage came from a paper or from one of the team's own documents, so the interface can open either and say which it is. Citations to team documents point at masked text only. A brief never names a winning option. [UI_UX_SPEC.md](UI_UX_SPEC.md) §6.9 specifies how it is shown.

How the loop runs and what it stores ([walkthrough 12](walkthroughs/12-decision-brief.md), [ADR-0007](adr/0007-bm25-retrieval-for-the-brief.md)):

- **The passages** are the pipeline's `Passage` (`domain/screening.py`): masked text tagged `P<n>`. The Retriever is BM25 in process behind a `Retriever` Protocol; the vector store of §3.6 can replace it. A `RetrievedPassage` records the search that found it and the round.
- **Stopping.** `stopped_because` is `accepted` (the Critic accepted a draft the checks pass), `max_rounds` (a setting, default 3), `invalid_output` (a role answered invalidly twice in a row) or `no_progress` (a round did no better than the kept draft and the Critic's searches found nothing new). The kept draft is the accepted one, else the best: no failing checks first, then the Critic's scores, then the latest.
- **What the brief keeps.** Besides its body: the checks still failing on the kept draft (`warnings`, each a `Problem` with a code and a claim path), the Critic's six scores (`CriticScores`, 0 to 2 each, never summed), the evidence the Writer saw with what was known of each source (`SourceDetails`), and its `AgentStep`s. It is one `decision_briefs` row per run, with three columns sealed under the project's key.

`RunManifest` is what makes reproducibility real. It records the vocabulary version, compiled queries, sources queried, model ids, prompt versions and timestamps. Two runs are comparable only if both recorded one.

**How a run's papers are kept** (walkthrough 11). Each stage records its own manifest facts under `"manifest"` in its `stage.finished` summary; the manifest is assembled from those and the run's dates, and never reconstructed. Versions are kept as lists, so a run that mixed two vocabulary versions says so.

```text
PaperRecord (stored)  one per review, found again by an HMAC of its identity
                      under a project subkey; the record itself sealed
PaperSet        which run's discovery a run works from: its own, or (for a
                run started without Discover) the review's newest earlier one
RunPaper        a paper in a run's merged list, at its place (1, 2, …) and
                with the sources that returned it; the place is its tag, P1…
RunSource       one source a run asked: searched with a count, or failed;
                with the plan and vocabulary versions and the sealed query
PaperText       the text kept for masking and screening (title and abstract
                in P1), sealed; read only by the mask stage
ScreeningDecision  per run and paper: label (include | exclude | uncertain),
                priority (high | medium | low), score, rationale (sealed),
                criteria matched, exclusion reasons, model id, prompt version,
                criteria version (the plan version), what was read, when;
                never rewritten
  CriterionAnswer  one criterion's answer for one paper (yes | partly | no
                | unclear) and a quote, checked to be words of the masked
                text the model read; sealed with its decision
LabelRules      how answers become a label: inclusion criteria Possibly
                relevant needs, and what unclear counts as (met | not_met
                | uncertain); one LabelRulesVersion per apply, never rewritten
Passage         a masked excerpt the brief may cite, tagged by the paper's place
```

The UI's labels (Highly relevant, Relevant, Possibly relevant, Excluded, Uncertain) are a `ScreeningTier` derived from label and priority, not stored. Under a review's `LabelRules`, the label and priority shown are computed on read from a decision's answers and the latest version; the decision itself never changes ([ADR 0013](adr/0013-label-rules-applied-on-read.md)). As §3.8 says, there is no `ScreeningProvider` seam: screening is a use case (the `screen` stage) that calls the model through `GuardedInference` with a versioned prompt, because a second seam in front of the model would be a place to skip the guard.

Conversations may still exist: the agentic loop produces a transcript, and a user may ask follow-up questions. But they are an *artefact of* a Review, not the organising entity.

Follow-up questions are a `Thread` of `Question`s inside a Review ([walkthrough 15](walkthroughs/15-exports-and-questions.md)). A question is masked before it is stored; it is answered in the background, under a lease, from the review's evidence by the brief's own machinery (the same retriever, Writer, checks and Critic, with an answer's prompts), and its answer is claims checked like the brief's. A thread answers one question at a time, and a follow-up carries the thread's last three turns, masked. Exports (`…/exports/{name}`) are generated on request from what the screens read and are never stored.

### 4.3 How a Vocabulary is acquired

A `Vocabulary` enters the system by one of two routes, and they have different rules:

**Route A — LLM-assisted, multi-turn.** The user asks a question. The model proposes concepts with synonyms and near-synonyms, grouped into themes. The user reviews, edits, adds, removes and confirms. This repeats until the user accepts.

**Route B — user-supplied, strict.** The user uploads their own vocabulary. The pipeline uses it **exactly as given**: no LLM expansion, no synonym enrichment, no silent normalisation beyond documented parsing. A researcher who brings a validated term list must get precisely that list back in the compiled queries.

Both routes converge on the same frozen artefact:

```text
Vocabulary
  origin:     llm_assisted | user_supplied
  version:    incremented on every confirmed edit
  frozen_at:  timestamp of confirmation
  concepts:   [ Concept(term, synonyms[], theme) ]
```

Three rules follow:

1. **A Vocabulary is immutable once confirmed.** Editing produces a new version. Search results reference the version they were produced from, or reproducibility is lost.
2. **`origin` is recorded and surfaced.** A reviewer must be able to tell whether a term came from a model or a person.
3. **Route B never routes through the model.** Strict means strict. This is a correctness property, not a preference, and it deserves a test.

**How this is built** ([walkthrough 09](walkthroughs/09-plan-and-discover.md#planning-conversation)). A `ReviewPlan` holds the Vocabulary and the Criteria together, and its version is the version of both: a run records one number and can be reproduced from it. Each version records its author; per-term origin is derived from the recorded authors of the versions that introduced each term, never guessed from text. The `queries` stage runs only an accepted version; without one it asks for the `vocabulary` gate (§4.4), and an approval goes through the same conditional accept as the API's. Planning is refused while a run is going, so a run sees one plan from start to end. Route B's terms are parsed by code (two documented formats) and refused, never repaired, when a term is empty or repeated; the schema itself refuses a user-supplied version written by the model.

### 4.4 Runs, live events and gates

Two flows pause partway through for a human: vocabulary confirmation (§4.3) and the privacy review of uploaded documents (§3.9.1). Neither fits request/response, and both must be in the API contract before it is frozen.

So a run is a background job. The request that starts it returns at once, the worker executes the stages, and the browser follows along on one Server-Sent Events stream. A gate is an event on that stream:

```text
POST /api/v1/reviews/{id}/runs           { stages }  -> 202 { run_id }
GET  /api/v1/runs/{run_id}/events        -> text/event-stream
    event: stage.started    { stage, position, total, at }
    event: stage.progress   { stage, done, total, item }
    event: stage.finished   { stage, seconds, summary }
    event: agent.step       { round, role, status, ... }       one AgentStep
    event: gate.required    { gate_id, kind, payload }         <- run pauses
    event: gate.resolved    { gate_id, by, decision }          -> run resumes
    event: job.finished     { outcome, seconds, error }
POST /api/v1/gates/{gate_id}             { decision, revisions }
POST /api/v1/runs/{run_id}/cancel
```

**Events.**

- **Names** follow the CHITCHAT prototype's job events (`WEB_APP_REFERENCE.md` §2): `stage.started`, `stage.progress`, `stage.finished`, `agent.step` and `job.finished`. humani-t adds the two gate events. [UI_UX_SPEC.md](UI_UX_SPEC.md) §11.2 says what the interface does with each.
- **`stage`** is a stage id from the prototype's pipeline, in run order, as [UI_UX_SPEC.md](UI_UX_SPEC.md) §6.7 shows them (Queries, Discover, Ingest, Mask, Screen, Export, then the brief stage, whose id is `answer`). `position` and `total` place the stage within this run, because a run may cover only some stages.
- **`stage.progress`** can fire once per paper. The server sends every one, because replay needs them; the interface throttles its redraws.
- **`agent.step`** carries the fields of one `AgentStep`: `round` (0 for the Planner), `role` (`planner`, `retriever`, `writer`, `checks`, `critic`), `status` (`ok`, `refused`, `invalid`, `error`), and per role the seconds, token usage, output, error, queries and passage counts, or the checks' problems.
- **`job.finished`** is sent exactly once and is always a run's last event. Its `outcome` is `done`, `failed` or `cancelled`. A failure keeps finished stages, and `error` is a plain-language message, never raw text or a stack trace.

**Gates.**

- **Kinds:** `vocabulary` (§4.3) and `privacy_review` (§3.9.1). `payload` carries what the person needs to decide: the draft vocabulary, or the documents waiting for a masking check.
- **Waiting costs nothing.** When a run reaches a gate it stores the gate, emits `gate.required` and stops. It holds no database connection and no worker slot while a person thinks (§5.7). Resolving the gate queues the rest of the run.
- **Resolving is a write like any other.** `POST /api/v1/gates/{gate_id}` needs the session, the CSRF token and a role that allows the action (§5). It is a conditional update: the first decision wins and emits `gate.resolved`, and a second, concurrent one gets 409. The interface then shows the decision that won ([UI_UX_SPEC.md](UI_UX_SPEC.md) §11.2).
- **Everyone sees it.** `gate.resolved` goes to every open stream for that run, so other tabs and other members see it. Every resolution is also an `AuditEvent`.

**Reconnecting with `Last-Event-ID`.**

- **Every event is stored before it is sent,** in the run's event log, with an `id` that increases by one within the run. The wire format is plain SSE: `id:`, `event:` and a JSON `data:` line.
- **The browser resumes on its own.** `EventSource` reconnects after a drop and sends the last id it saw in the `Last-Event-ID` header. The server replays every stored event after that id, then continues live.
- **A new `EventSource` can say where it was.** When the browser gives up and the interface opens a new `EventSource`, it cannot set `Last-Event-ID`, so it passes the last id it applied as `?last_event_id=`. The same rules apply; when both are present, the header wins.
- **An id the server doesn't recognise** (malformed, or from another run) replays from the start. A client can then see an event twice but never misses one, so the interface applies events idempotently by id. Because ids count from 1 in every run, "from another run" can only be recognised when the id is beyond this run's latest event; a smaller id from another run is indistinguishable from one of this run's, and resumes after it. `EventSource` only ever sends an id it received on the same URL, so only a misbehaving client meets this.
- **Any API instance can serve a reconnect.** Events come from the log, not from the process running the job, so a restart loses nothing. The stream doesn't end when the browser disconnects, and a run doesn't stop when no one is watching.
- **Finished runs replay too.** After `job.finished` the server closes the stream. A later request replays the log and closes, which is how a finished run's history is shown.
- **Proxies keep the connection open.** While a run is quiet, the server sends a comment line every 15 seconds, so Caddy and other proxies don't close an idle connection. A `retry:` field sets the browser's reconnect delay.

**Access.**

- **The stream is checked like any other request.** It is a `GET` carrying the session cookie, checked against the run's project membership on the first connect and on every reconnect. A non-member gets 404 (§5).
- **A long-lived stream doesn't outlive its session.** The server re-checks the session at least once a minute and ends the stream if the session was revoked or expired.
- **Events carry counts, labels and masked text only.** They never carry a document's raw text or an unmasked prompt. The event log is project data: it is encrypted with the project's data key, so deleting a project shreds it with everything else.

**Why SSE.** Events flow one way: the server tells the browser what happened. Every action the browser takes is an ordinary `POST` with the usual session and CSRF checks. `EventSource` handles reconnecting and `Last-Event-ID` natively, and SSE runs over plain HTTP through Caddy.
- WebSockets were rejected: they would add a second authentication path and a cross-site hijacking risk to defend, for traffic that only flows one way.
- Polling was rejected: it either lags or loads the API, and the browser would have to rebuild its own replay logic.

Retrofitting a pause into a synchronous endpoint is a breaking change. Designing for it now costs nothing.

---

## 5. Security model

The brief's security rules (`WEB_APP_BRIEF.md`, rules 1–18) are the requirements. This section records how they are met. Each control is mapped to the test that proves it in [`docs/security/controls.md`](security/controls.md). The target is OWASP ASVS 5.0 Level 2 and NIST SP 800-63B rev 4 at AAL2.

### 5.1 Sign-in

- **Local accounts.**
  - Passwords are at least 15 characters, NFKC-normalised, and checked against a bundled blocklist. There are no composition rules and no forced rotation.
  - Hashing uses Argon2id. A hash is upgraded at the next login when the parameters change.
- **MFA is required.**
  - Passkeys (WebAuthn, user verification required) come first, with TOTP as the fallback. There is no SMS.
  - Sign-in has two steps. A correct password on an account with a factor yields a **pending** session (aal 1, 5 minutes) that reaches only `/auth/mfa/*` and sign-out. A second factor replaces it with a new aal 2 session under a new token.
  - An account with no factor signs in at aal 1 and reaches only its account basics and enrolment until it adds one. The policy is a `build_providers` keyword (`MfaPolicy`), required by default; it is not an operator setting.
  - The first factor an account adds issues recovery codes, raises that browser to aal 2 under a new token, and ends every other session of the account.
  - TOTP follows RFC 6238 (SHA-1, 6 digits, 30 s, one step of drift). Each time step is accepted once; storage advances the last used step with a conditional update, so two requests carrying one code have one winner. The secret is encrypted under the user's own data key, created at first enrolment.
  - Recovery codes are 10 × 80 bits, shown once, single-use (a conditional claim), and stored as HMACs keyed by an HKDF subkey of the user's data key, so shredding that key also ends them.
  - Passkey ceremonies are verified against the deployment's `public_origin` (origin and relying-party id). Challenges are single-use and short-lived, and a signature counter that fails to advance is refused.
  - Second-factor attempts are throttled per account like passwords.
  - The last authenticator cannot be removed. Changes take the user's row lock first (a version-checked save), so concurrent removals cannot leave none. Adding or removing one needs a recent sign-in, emails the user, and is audited without secrets.
- **Optional OIDC SSO.**
  - Uses PKCE, `state` and `nonce`, and validates `iss`, `aud` and `exp`.
  - An external identity links to a user by `(issuer, subject)` only, never by matching email.
- **Registration is invite-only.** The first administrator is created from the command line, not through a public endpoint.
- **Responses do not reveal whether an account exists.** Unknown users take the same path, including a dummy hash.
- **Throttling, not lockout.** Limits apply per account, per IP and per email. When the limiter cannot be reached, sign-in fails closed: `503` with `Retry-After`, never an attempt let through uncounted.
- **The client address is the connecting peer.** `X-Forwarded-For` counts only from an address in the operator's `trusted_proxies` setting, which is empty by default; in the compose deployment it is Caddy's fixed address alone. A client cannot choose the address its per-IP limit counts.

### 5.2 Sessions

- **The token.** An opaque 256-bit random token. Only its SHA-256 is stored, in Postgres, so sessions can be listed, revoked and audited. There are no JWTs: a JWT cannot be revoked before it expires.
- **The cookie.** `__Host-session`, with `Secure; HttpOnly; Path=/; SameSite=Lax`.
- **Rotation.** The token is rotated on login and on step-up (re-authentication). A rotated session keeps its assurance level and its creation time, so a password alone neither raises the level nor restarts the 24-hour limit.
- **Expiry.** 1 hour idle, 24 hours absolute; 5 minutes absolute for a session pending its second factor.
- **Assurance level.** Each session has one (aal 1 or 2) and a pending flag. A session that has passed the password but not MFA can reach only the MFA endpoints. Changing the password rotates the session and keeps its level.
- **Recent sign-in for sensitive actions.** Changing a password or an authenticator, deleting a project, changing members, or turning a project's masking off requires a sign-in within the last 15 minutes.
- **Password reset.** A reset changes the password only. MFA is still required, every session is revoked, and the user is emailed.

### 5.3 Request integrity

- **CSRF defence:**
  - reject cross-site state-changing requests using `Sec-Fetch-Site` and `Origin`;
  - a double-submit token tied to the session (`__Host-csrf` cookie, echoed in `X-CSRF-Token`);
  - JSON-only bodies.
- **Security headers:** strict CSP with Trusted Types, HSTS, `frame-ancestors 'none'`, COOP/CORP, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on authenticated responses.
- **Error responses** are RFC 9457 problem details with no stack traces. An unhandled exception is answered inside the response middleware, so a `500` carries the security headers and the request id, and its log line the same id. An unreachable paper source or model server is a `502` that names no host.
- **Logs** are structured, carry a request id, and redact tokens, cookies, passwords and email addresses.

### 5.4 Encryption

- **In transit.** HTTPS only, terminated by Caddy, with HSTS.
- **At rest.** Envelope encryption with AES-256-GCM:
  - every project has its own data key, wrapped by a key-encryption key (KEK) from `KeyProvider`;
  - the associated data binds each ciphertext to its project, table, column and row, so it cannot be moved elsewhere and still decrypt.
- **Deletion is crypto-shredding.** Deleting a project destroys its data key. Copies in database backups become unreadable once the backup retention window has passed, and the deployment guide says so.
- **Key rotation.** Rotating the KEK rewraps the data keys. Existing ciphertext is not re-encrypted.

### 5.5 Authorisation and audit

- **Deny by default.** Every use case checks `can(role, action)` against the caller's membership. Non-members get 404, not 403, so project IDs cannot be probed.
- **The last owner** of a project cannot leave or be demoted, even when owners act at the same moment ([ADR-0002](adr/0002-project-row-lock-for-membership-changes.md)).
- **The last enabled administrator** cannot be disabled, and no administrator can disable their own account. Disabling locks every enabled administrator's row first (`UserRepository.lock_enabled_admins`, `SELECT … FOR UPDATE` in id order), so admins disabling one another at once are serialised. Re-enabling an account needs a recent sign-in and is audited.
- **Every route is checked.** A test walks the app's route table: every route outside an explicit public allowlist refuses an anonymous request, and every route inside a project refuses a non-member with the same `404` as a missing project.
- **The audit log is append-only and hash-chained.** Each event includes the hash of the previous one, written under a lock. The application's database role has no `UPDATE` or `DELETE` on it.
- **Audit rows hold ids only.** Any detail is encrypted with the project's key, so a shredded project's history stays verifiable without staying readable.

### 5.6 Outbound requests

All outbound HTTP goes through `HttpFetcher`:

- the address is checked at **connect time**, so DNS rebinding cannot get round the check;
- private, loopback, link-local, CGNAT and metadata addresses are refused, including IPv4-mapped IPv6 forms;
- redirects are followed by hand and re-checked;
- each purpose has a host allowlist;
- response size and time are capped.

PDF links come from paper metadata, which is attacker-controllable. That is why this is a seam and not a convenience wrapper.

---

### 5.7 Concurrency and resources

Feature tests send one request at a time; production does not. These rules keep the system correct and available under concurrent load, and each has a test in `tests/test_concurrency_and_resources.py` or the storage contract suite.

1. **Never hold a database connection across slow work.** Password hashing, email and outbound HTTP happen between transactions, never inside one. A use case is: short read → slow work → short write → side effects.
2. **Side effects happen after commit.** An email is sent once the change it describes is durable, and never while a lock is held.
3. **The audit event is the last write in a transaction.** Appending takes the chain-head lock; taking it last keeps it held briefly, and taking it in the same position everywhere rules out deadlocks.
4. **No blind overwrites of shared records.** A write another request could race with is either conditional (`claim`, `revoke`, `touch`: "only if still unused / unrevoked") and reports whether it won, or version-checked (optimistic locking on `users.version`). A lost update raises `ConcurrentUpdateError`; it is never silently merged.
5. **Single-use means atomically single-use.** Tokens are spent with one conditional `UPDATE`; of two simultaneous uses, exactly one succeeds.
6. **Count before the slow check.** Rate limits count an attempt atomically before the password is verified, so parallel attempts cannot all pass a check-then-count gap.
7. **Bounded pools that fail fast.** Each API process has a fixed-size pool with no overflow and a short wait; when it is exhausted the request gets `503` with `Retry-After`, not a hang. Postgres cancels statements over 15 s and ends transactions left idle over 30 s.
8. **Bounded memory.** In-process structures expire their entries; request bodies over 1 MiB are refused before being read; ended sessions and spent links are purged (`app.cli purge-expired`, scheduled by the worker).
9. **Everything opened is closed.** `Providers.close()` releases every pool and client, at shutdown and at the end of every CLI command.

---

### 5.8 Masking as a project's choice

The one rule (`CLAUDE.md`) lets text reach a model unmasked only when the deployment runs the model itself, or when both the operator and the project's owner have chosen it ([ADR-0014](adr/0014-masking-is-a-project-choice.md)):

- **The owner's switch.** `Project.is_masking_on`, changed by owners only (`PUT …/masking`), audited as `project.masking_changed`. Turning it off needs a recent sign-in.
- **The operator's policy.** `MaskingPolicy`, built by `operator_masking_policy` in `api/dependencies.py` from `Settings.is_inference_hosted` (the model server is outside the deployment, or an Ollama `*-cloud` model) and `allow_unmasked_hosted_inference` (refused unless `allow_hosted_inference` is set; not passed through by compose).
- **Decided when sent.** `MaskingChoice` reads the switch fresh and applies `is_masking_applied` for every sanitiser call (`mask_as_chosen`), every guarded model call (`GuardedInference.for_project`), every read of a paper's masked text, and every read of a document's passages. Text kept with masking off is withheld once masking applies again.
- **What off changes.** The detectors and the identifier check of the prompt guard are skipped, documents need no privacy review, and papers keep their text. The project's rules still mask their terms everywhere, and the guard still refuses them.
- **Provenance.** Receipts, follow-up questions, every stage that masks or sends text, the run manifest (`masking`) and the brief's trace record whether the text was masked.

## 6. Dependencies and prior art

humani-t is a rebuild of an existing prototype, not a greenfield project. See [`dependencies.md`](./dependencies.md) for the full analysis and open decisions.

In short:

- **CHITCHAT is this project's first prototype.** Its [overview](./CHITCHAT-OVERVIEW.md) serves as the requirements document, and its six pipeline stages map onto the build order.
- **`mmore` is the one real external dependency** (`uv add mmore`).
- **AMMORE is a pattern we reimplement, not a package.** We take its Planner/Retriever/Critic/Writer loop and reject its OpenAI default, its Next.js frontend and its silent web-search fallback.

---

## 7. The test for every decision

> Does this send anything — a question, a document, a log line, a request — somewhere the operator did not choose?

If yes, or if you're not sure, introduce the seam before proceeding:

```text
PostgreSQL         -> StorageProvider
LLM vendor         -> InferenceProvider
Milvus             -> VectorStoreProvider
OpenAlex / arXiv   -> PaperSearchProvider
SMTP               -> EmailProvider
Identity provider  -> IdentityProvider
KMS / key file     -> KeyProvider
Any outbound URL   -> HttpFetcher
```

There is a second test specific to this project:

> Could a reviewer reconstruct why this paper was included or excluded, six months from now, from what we stored?

If no, the decision is not auditable. Auditability is not a feature that can be added later: it depends on provenance being captured at the moment the decision was made.
