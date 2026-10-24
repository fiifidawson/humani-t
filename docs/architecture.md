# Architecture

This document records the decisions that must be stable **before** feature work
begins. Everything here serves one rule:

> humani-t is a self-hosted web application. Data leaves the deployment only to
> destinations the operator has configured, and only after it has been masked.

[ADR-0001](./adr/0001-web-only-self-hosted.md) explains why there is one
deployment shape and no desktop version.

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

- The frontend never imports a database, an SDK, a filesystem path or a model
  name. It is untrusted. Every rule is enforced by the API.
- `domain/` imports nothing from providers, `api/` or any third-party SDK.
- `application/` depends on provider *interfaces*, never on a concrete
  implementation.
- Only provider packages and the composition root (`api/dependencies.py`) know
  that PostgreSQL, Valkey, an LLM vendor or an identity provider exist.

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
- The SPA and the API share one origin, so there is no CORS configuration to get
  wrong.
- Secrets reach containers as Docker secrets (`/run/secrets/*`), never as
  plain environment variables or files in the image.
- Outbound traffic is limited to the paper sources, PDF hosts, the configured
  LLM provider and the configured identity provider, and goes through
  `HttpFetcher` (§5.6).

File storage for uploaded documents, and malware scanning, are added with the
uploads feature, not before. Until then they would only add attack surface.

---

## 3. Provider interfaces

Each seam is a `Protocol` in `services/api/app/<area>/base.py`, with a default
implementation and a fake. The seams exist so that:

- the application is testable without a network;
- an organisation can choose its own model, identity provider and key store
  without touching a use case.

| Interface | Default implementation | Fake |
| --- | --- | --- |
| `InferenceProvider` | self-hostable model server (OpenAI-compatible API) | scripted responses |
| `StorageProvider` | `PostgresStorage` (SQLAlchemy 2, async) | in-memory repositories |
| `FileStorageProvider` | added with uploads | in-memory |
| `EmbeddingProvider` | local embedding model | deterministic hash vectors |
| `VectorStoreProvider` | Milvus (via mmore) | in-memory |
| `PaperSearchProvider` | OpenAlex, Europe PMC, arXiv | `FakePaperSearchProvider` |
| `ScreeningProvider` | LLM-assisted, via `InferenceProvider` | rule-based |
| `SanitizationProvider` | mmore privacy mode, local detection | pattern-based |
| `ToolProvider` | in-process tools | in-memory |
| `KeyProvider` | file-backed KEK (Docker secret) | fixed test key |
| `PasswordHasher` | Argon2id | fast deterministic hasher |
| `PasskeyVerifier` | WebAuthn (py_webauthn) | software authenticator |
| `TotpVerifier` | RFC 6238 (pyotp) | fixed clock |
| `IdentityProvider` | OIDC (optional) | local JWKS |
| `EmailProvider` | SMTP | records sent messages |
| `RateLimiter` | Valkey | in-memory |
| `HttpFetcher` | SSRF-guarded HTTP client | scripted resolver and responses |

### 3.1 InferenceProvider

```python
class InferenceProvider(Protocol):
    async def list_models(self) -> Sequence[ModelInfo]: ...
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]: ...
```

`CompletionRequest` carries messages, model id and generation parameters as
**domain types**, never a provider's wire format. Model ids are opaque strings
resolved by the provider, so a hosted model and a self-hosted one are
interchangeable to every layer above.

Every text in a `CompletionRequest` has already been through
`SanitizationProvider` (§3.9). That check belongs to the application layer, not
to the provider.

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
    syntheses: SynthesisRepository
    runs: RunManifestRepository
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

Constraint: no raw SQL and no SQLAlchemy types above `storage/`. Use cases are
tested against the in-memory implementation. A shared contract test suite runs
against both implementations, so the fake cannot drift from Postgres.

### 3.3 FileStorageProvider

```python
class FileStorageProvider(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes], content_type: str) -> FileRef: ...
    def get(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
```

There is no pre-signed URL method. Files are always streamed through an API
route that checks membership and writes an audit event. A URL that bypasses the
API would bypass both.

Stored bytes are encrypted with the project's data key (§5.4) before `put`.

### 3.4 EmbeddingProvider

```python
class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...
    @property
    def dimensions(self) -> int: ...
    async def embed(self, texts: Sequence[str]) -> Sequence[Vector]: ...
```

Stored vectors must record `model_id` and `dimensions` alongside them. Vectors
from different models are not comparable, and the model will change.

### 3.5 ToolProvider

```python
class ToolProvider(Protocol):
    def list_tools(self) -> Sequence[ToolSpec]: ...
    async def invoke(
        self, name: str, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> ToolResult: ...
```

`ToolContext` carries the caller's identity and project role. Every tool is
checked against it, because a tool invoked by the agent loop acts on behalf of
a user and must not see more than that user can.

### 3.6 VectorStoreProvider

mmore indexes into Milvus. Milvus must not leak above its provider package.

```python
class VectorStoreProvider(Protocol):
    async def upsert(self, collection: str, records: Sequence[VectorRecord]) -> None: ...
    async def search(
        self,
        collection: str,
        query: HybridQuery,
        k: int,
        filter: Mapping[str, Any] | None = None,
    ) -> Sequence[ScoredChunk]: ...
    async def delete(self, collection: str, ids: Sequence[str]) -> None: ...
```

`HybridQuery` carries both a dense vector and sparse terms, because mmore's
retrieval is hybrid. A provider that can only do dense search degrades explicitly
rather than silently ignoring the sparse half.

One collection per project. Projects never share an index (brief rule 7).

### 3.7 PaperSearchProvider

One interface over OpenAlex, Europe PMC and arXiv. Each has a different query
syntax, rate limit and metadata shape. None of that belongs in the application
layer.

```python
class PaperSearchProvider(Protocol):
    @property
    def source_id(self) -> str: ...
    async def search(self, query: BooleanQuery, limit: int) -> Sequence[PaperRecord]: ...
    async def fetch_fulltext(self, record: PaperRecord) -> Document | None: ...
```

`BooleanQuery` is a **domain type**, not a source-specific string. The provider
compiles it into that repository's syntax. This seam makes a search reproducible
across sources. It is the single most important interface in the system, because
everything downstream inherits its provenance.

`fetch_fulltext` returns `None` when the document is not openly available. That is
a normal outcome, not an error, and screening must handle metadata-only records.

### 3.8 ScreeningProvider

```python
class ScreeningProvider(Protocol):
    async def screen(
        self, paper: PaperRecord, criteria: Sequence[Criterion]
    ) -> ScreeningDecision: ...
```

`ScreeningDecision` records:

- the score, the priority and the rationale;
- **the provenance**: model id, prompt version and criteria version.

Auditability is a stated project commitment. A reviewer must be able to ask why a
paper was excluded and get an answer that names what produced the decision.

Screening never makes the final call. The interface returns an assessment; the
domain decides what a human still has to see.

### 3.9 SanitizationProvider

mmore ships a privacy mode (`src/mmore/privacy/`) that sanitises retrieved
context before it reaches the answer model. It has `humanitarian` as a
first-class domain profile. We use it behind our own interface, never by
shelling out to `mmore rag --privacy`.

```python
class SanitizationProvider(Protocol):
    async def sanitize(
        self, chunks: Sequence[Chunk], query: str, policy: PrivacyPolicy
    ) -> SanitizationResult: ...
```

The mmore pipeline runs
`analyzer -> detector -> sanitizer -> leakage_adversary -> HITL gate -> answer -> verifier -> report`.

`SanitizationResult` carries the sanitised chunks, the verifier warnings and the
`ReportRecord`, which feeds `RunManifest`.

**Locked configuration choices:**

| Key | Value | Reason |
| --- | --- | --- |
| `domain` | `humanitarian` | Built for exactly this setting |
| `detection.engine` | `presidio` or `gliner` | Both run locally. **Never `openai_filter`**: it sends unsanitised text to OpenAI, which defeats the purpose |
| `sanitization.strategy` | `entity_replacement` or `synthetic_rewrite` | `token_masking` degrades synthesis quality |
| `answer.llm` | our `InferenceProvider` default | Never a third-party default |
| `abort_on_exhaustion` | `true` | Fail closed. Keep the default |
| `verifier.checks` | `residual_leakage`, `faithfulness` | Both |

**Masking happens at ingest as well as at query time.** mmore's pipeline
sanitises retrieved chunks; it does not change what is stored. The brief's rules
1 to 3 therefore require our own masking stage between extraction and indexing.
Without it, raw text would sit in the index and the database:

- The question, clarifying answers and change requests are masked before any LLM
  call.
- Uploaded documents are always masked, with the broad entity list. This cannot
  be switched off.
- Raw text stays in a separate store, encrypted with the project's data key, and
  has a retention period.

#### 3.9.1 Masking is derived, reviewable and re-runnable

Detection misses things (brief rule 5), so the design assumes it will be wrong
and makes every result checkable and correctable. For each document we store:

```text
raw text      encrypted with the project's data key; never modified
mask spans    (start, end, entity type, source) over the raw text, where source is
              a detector and its version ("presidio@2.3") or a user ("user:<id>")
masked text   raw text with the spans applied; rebuilt whenever the spans change
```

Masking is a function of the raw text and the spans, never an edit. That gives:

- **Several detectors, combined.** Pattern rules for structured identifiers
  (medical record, phone and ID numbers), a local NER model (Presidio or GLiNER)
  for names and places, and mmore's leakage check as a second pass. A span found
  by any of them is masked: over-masking costs some quality, under-masking leaks.
- **Re-runs.** When a detector is upgraded, it runs again over the raw text and
  only new spans are proposed. Spans added by people are kept, because they point
  into text that never changes.
- **Project rules.** "Always mask this term in this project" (`MaskingRule`)
  applies to every current and future document, such as a list of staff names.
- **A last check on what leaves.** Immediately before any `InferenceProvider`
  call, the exact outgoing text is scanned again. A hit stops the call: fail
  closed.
- **Provenance.** Every span names what produced it, so "why was this masked, and
  by what?" has an answer.

**Human review before use.** An uploaded document is not indexed and not sent to
any model until a person approves its masking. A correction made after text
reached a model cannot be recalled, so the check must come first. The review
screen:

- shows the extracted text with masked spans highlighted by type;
- lists the detector's low-confidence spans first, so review is short;
- lets the reviewer select missed text and mask it as a chosen type, or turn it
  into a project rule;
- lets the owner mark a false positive as "not personal". This is audited and
  never allowed for structured identifiers. Rule 3 means masking itself cannot be
  switched off for documents; this is a narrow correction, not an off switch;
- re-masks and re-indexes only the changed document after a correction.

**Who sees raw text.** Only the document's owner, and only in the review screen.
Reviewers and viewers see masked text. Each view of raw text writes an
`AuditEvent`. The screen reviews *extracted text*, not the rendered file: text
visible only in an image (a scanned signature, a photographed ID) is masked only
if OCR extracted it, and the screen says so.

> **OPEN WORK — still to settle before building the masking stage.**
>
> - **What breaks retrieval?** Masked text embeds differently from raw text.
>   Masking author names and institutions may degrade search over an academic
>   corpus in ways that matter.
> - **How broad for published papers?** The corpus is largely public papers with
>   little PII; the review gate above applies to the user's own documents. Papers
>   may need only the narrow entity list and no human review.
> - **Where does it run?** Reuse mmore's detector and sanitiser agents outside
>   the RAG pipeline, or write a standalone stage in the isolated worker.

**The HITL gate shapes the API contract.** A gated request pauses until a human
approves, which a plain request/response cannot express. See §4.4.

---

## 4. Domain model

### 4.1 Access and accountability

These entities exist before any review does, and every other entity hangs off
them:

```text
User            an account: email, password hash, status, instance role
Credential      a second factor: passkey, TOTP secret (encrypted) or recovery code
Identity        a link to an external OIDC account, keyed by (issuer, subject)
Session         a signed-in browser: token hash, assurance level, expiry
Project         the unit of ownership, sharing, encryption and deletion;
                owns one or more Reviews
Membership      (User, Project, Role) where Role is owner | reviewer | viewer
AuditEvent      who did what to which object, when; hash-chained
```

A **Project** is what the brief calls a project. It is the boundary for three
things:

- **access control:** every request checks membership;
- **encryption:** one data key per project;
- **deletion:** destroying the key makes the project's data unreadable.

The fixed review vocabulary below (`Review`, `Vocabulary`, …) lives inside a
Project.

Role permissions are a pure domain function, `can(role, action)`. It is tested
as a table, so a change to who may do what is a visible diff.

### 4.2 Reviews

The spine of the application is a reproducible review run:

```text
Review          a question, a Vocabulary, and a set of Criteria
  Vocabulary    curated concepts with synonyms, grouped into themes
  BooleanQuery  generated from the Vocabulary, compiled per source
  PaperRecord   bibliographic metadata + source + dedup identity
  Document      extracted full text, when openly available
  MaskSpan      a masked range of a Document's raw text: type and source
  MaskingRule   a project-wide term that is always masked
  Criterion     an inclusion/exclusion rule
  ScreeningDecision  score, priority, rationale, provenance
  Synthesis     agentic output, grounded in Citations
  Citation      a claim bound to a PaperRecord
  RunManifest   everything needed to reproduce or diff a run
```

`RunManifest` is what makes reproducibility real. It records the vocabulary
version, compiled queries, sources queried, model ids, prompt versions and
timestamps. Two runs are comparable only if both recorded one.

Conversations may still exist: the agentic loop produces a transcript, and a user
may ask follow-up questions. But they are an *artefact of* a Review, not the
organising entity.

### 4.3 How a Vocabulary is acquired

A `Vocabulary` enters the system by one of two routes, and they have different
rules:

**Route A — LLM-assisted, multi-turn.** The user asks a question. The model
proposes concepts with synonyms and near-synonyms, grouped into themes. The user
reviews, edits, adds, removes and confirms. This repeats until the user accepts.

**Route B — user-supplied, strict.** The user uploads their own vocabulary. The
pipeline uses it **exactly as given**: no LLM expansion, no synonym enrichment,
no silent normalisation beyond documented parsing. A researcher who brings a
validated term list must get precisely that list back in the compiled queries.

Both routes converge on the same frozen artefact:

```text
Vocabulary
  origin:     llm_assisted | user_supplied
  version:    incremented on every confirmed edit
  frozen_at:  timestamp of confirmation
  concepts:   [ Concept(term, synonyms[], theme) ]
```

Three rules follow:

1. **A Vocabulary is immutable once confirmed.** Editing produces a new version.
   Search results reference the version they were produced from, or
   reproducibility is lost.
2. **`origin` is recorded and surfaced.** A reviewer must be able to tell whether a
   term came from a model or a person.
3. **Route B never routes through the model.** Strict means strict. This is a
   correctness property, not a preference, and it deserves a test.

### 4.4 Gated requests

Two flows pause partway through for a human: vocabulary confirmation (§4.3) and
the privacy HITL gate (§3.9). Neither fits request/response, and both must be in
the API contract before it is frozen.

The shape:

```text
POST /api/v1/reviews/{id}/synthesis      -> SSE stream
    event: progress
    event: gate_required   { gate_id, kind, payload }   <- stream pauses
POST /api/v1/gates/{gate_id}             { decision, revisions }
    -> original stream resumes
```

Retrofitting a pause into a synchronous endpoint is a breaking change. Designing
for it now costs nothing.

---

## 5. Security model

The brief's security rules (`WEB_APP_BRIEF.md`, rules 1–18) are the
requirements. This section records how they are met. Each control is mapped to
the test that proves it in `docs/security/asvs-l2.md`. The target is OWASP ASVS
5.0 Level 2 and NIST SP 800-63B rev 4 at AAL2.

### 5.1 Sign-in

- **Local accounts.**
  - Passwords are at least 15 characters, NFKC-normalised, and checked against a
    bundled blocklist. There are no composition rules and no forced rotation.
  - Hashing uses Argon2id. A hash is upgraded at the next login when the
    parameters change.
- **MFA is required.**
  - Passkeys (WebAuthn, user verification required) come first, with TOTP as the
    fallback. There is no SMS.
  - Recovery codes are single-use and stored as HMACs.
  - The last authenticator cannot be removed.
- **Optional OIDC SSO.**
  - Uses PKCE, `state` and `nonce`, and validates `iss`, `aud` and `exp`.
  - An external identity links to a user by `(issuer, subject)` only, never by
    matching email.
- **Registration is invite-only.** The first administrator is created from the
  command line, not through a public endpoint.
- **Responses do not reveal whether an account exists.** Unknown users take the
  same path, including a dummy hash.
- **Throttling, not lockout.** Limits apply per account, per IP and per email.

### 5.2 Sessions

- **The token.** An opaque 256-bit random token. Only its SHA-256 is stored, in
  Postgres, so sessions can be listed, revoked and audited. There are no JWTs: a
  JWT cannot be revoked before it expires.
- **The cookie.** `__Host-session`, with `Secure; HttpOnly; Path=/; SameSite=Lax`.
- **Rotation.** The token is rotated on login and on step-up.
- **Expiry.** 1 hour idle, 24 hours absolute.
- **Assurance level.** Each session has one. A session that has passed the
  password but not MFA can reach only the MFA endpoints.
- **Recent sign-in for sensitive actions.** Changing a password or an
  authenticator, deleting a project, or changing members requires a sign-in
  within the last 15 minutes.
- **Password reset.** A reset changes the password only. MFA is still required,
  every session is revoked, and the user is emailed.

### 5.3 Request integrity

- **CSRF defence:**
  - reject cross-site state-changing requests using `Sec-Fetch-Site` and
    `Origin`;
  - a double-submit token tied to the session (`__Host-csrf` cookie, echoed in
    `X-CSRF-Token`);
  - JSON-only bodies.
- **Security headers:** strict CSP with Trusted Types, HSTS,
  `frame-ancestors 'none'`, COOP/CORP, `Referrer-Policy: no-referrer`, and
  `Cache-Control: no-store` on authenticated responses.
- **Error responses** are RFC 9457 problem details with no stack traces.
- **Logs** are structured, carry a request id, and redact tokens, cookies,
  passwords and email addresses.

### 5.4 Encryption

- **In transit.** HTTPS only, terminated by Caddy, with HSTS.
- **At rest.** Envelope encryption with AES-256-GCM:
  - every project has its own data key, wrapped by a key-encryption key (KEK)
    from `KeyProvider`;
  - the associated data binds each ciphertext to its project, table, column and
    row, so it cannot be moved elsewhere and still decrypt.
- **Deletion is crypto-shredding.** Deleting a project destroys its data key.
  Copies in database backups become unreadable once the backup retention window
  has passed, and the deployment guide says so.
- **Key rotation.** Rotating the KEK rewraps the data keys. Existing ciphertext is
  not re-encrypted.

### 5.5 Authorisation and audit

- **Deny by default.** Every use case checks `can(role, action)` against the
  caller's membership. Non-members get 404, not 403, so project IDs cannot be
  probed.
- **The last owner** of a project cannot leave or be demoted.
- **The audit log is append-only and hash-chained.** Each event includes the
  hash of the previous one, written under a lock. The application's database
  role has no `UPDATE` or `DELETE` on it.
- **Audit rows hold ids only.** Any detail is encrypted with the project's key,
  so a shredded project's history stays verifiable without staying readable.

### 5.6 Outbound requests

All outbound HTTP goes through `HttpFetcher`:

- the address is checked at **connect time**, so DNS rebinding cannot get round
  the check;
- private, loopback, link-local, CGNAT and metadata addresses are refused,
  including IPv4-mapped IPv6 forms;
- redirects are followed by hand and re-checked;
- each purpose has a host allowlist;
- response size and time are capped.

PDF links come from paper metadata, which is attacker-controllable. That is why
this is a seam and not a convenience wrapper.

---

### 5.7 Concurrency and resources

Feature tests send one request at a time; production does not. These rules keep
the system correct and available under concurrent load, and each has a test in
`tests/test_concurrency_and_resources.py` or the storage contract suite.

1. **Never hold a database connection across slow work.** Password hashing,
   email and outbound HTTP happen between transactions, never inside one. A use
   case is: short read → slow work → short write → side effects.
2. **Side effects happen after commit.** An email is sent once the change it
   describes is durable, and never while a lock is held.
3. **The audit event is the last write in a transaction.** Appending takes the
   chain-head lock; taking it last keeps it held briefly, and taking it in the
   same position everywhere rules out deadlocks.
4. **No blind overwrites of shared records.** A write another request could race
   with is either conditional (`claim`, `revoke`, `touch`: "only if still
   unused / unrevoked") and reports whether it won, or version-checked
   (optimistic locking on `users.version`). A lost update raises
   `ConcurrentUpdateError`; it is never silently merged.
5. **Single-use means atomically single-use.** Tokens are spent with one
   conditional `UPDATE`; of two simultaneous uses, exactly one succeeds.
6. **Count before the slow check.** Rate limits count an attempt atomically
   before the password is verified, so parallel attempts cannot all pass a
   check-then-count gap.
7. **Bounded pools that fail fast.** Each API process has a fixed-size pool with
   no overflow and a short wait; when it is exhausted the request gets `503`
   with `Retry-After`, not a hang. Postgres cancels statements over 15 s and
   ends transactions left idle over 30 s.
8. **Bounded memory.** In-process structures expire their entries; request
   bodies over 1 MiB are refused before being read; ended sessions and spent
   links are purged (`app.cli purge-expired`, scheduled by the worker).
9. **Everything opened is closed.** `Providers.close()` releases every pool and
   client, at shutdown and at the end of every CLI command.

---

## 6. Dependencies and prior art

humani-t is a rebuild of an existing prototype, not a greenfield project. See
[`dependencies.md`](./dependencies.md) for the full analysis and open decisions.

In short:

- **CHITCHAT is this project's first prototype.** Its
  [overview](./CHITCHAT-OVERVIEW.md) serves as the requirements document, and its
  six pipeline stages map onto the build order.
- **`mmore` is the one real external dependency** (`uv add mmore`).
- **AMMORE is a pattern we reimplement, not a package.** We take its
  Planner/Retriever/Critic/Writer loop and reject its OpenAI default, its Next.js
  frontend and its silent web-search fallback.

---

## 7. The test for every decision

> Does this send anything — a question, a document, a log line, a request —
> somewhere the operator did not choose?

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

> Could a reviewer reconstruct why this paper was included or excluded, six
> months from now, from what we stored?

If no, the decision is not auditable. Auditability is not a feature that can
be added later: it depends on provenance being captured at the moment the
decision was made.
