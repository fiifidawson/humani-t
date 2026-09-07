# Architecture

This document records the decisions that must be stable **before** feature work
begins. Everything here exists to serve one rule:

> Build Version 1 so that the application domain, API contracts, and provider
> abstractions survive when the backend moves from the server to the user's
> machine.

Version 2 is a new *deployment* of this application, not a rewrite.

---

## 1. Layering

```text
Presentation          apps/web, apps/desktop
    |
Application API       services/api/app/api          (HTTP boundary, versioned)
    |
Application logic     services/api/app/application  (use cases, orchestration)
    |
Domain                services/api/app/domain       (entities, rules, no I/O)
    |
Infrastructure        services/api/app/{storage,inference,infrastructure}
```

Dependencies point **downward only**. Concretely:

- The frontend never imports a database, an SDK, a filesystem path, or a model name.
- `domain/` imports nothing from `infrastructure/`, `api/`, or any third-party SDK.
- `application/` depends on provider *interfaces*, never on a concrete implementation.
- Only `infrastructure/` and the composition root (`main.py`) know that PostgreSQL,
  S3, OpenAI, or Ollama exist.

The forbidden shape is a route that reaches straight through:

```text
Route -> SQL -> OpenAI SDK        # this is what makes V2 a rewrite
```

---

## 2. Three separable concerns

These three must be independently replaceable, because V2 recombines them:

```text
1. User data        where conversations, messages, and files live
2. Application      where the API process runs
3. Model inference  where tokens are generated
```

Supported combinations, present and future:

| Mode | Data | Application | Inference | Backup |
| --- | --- | --- | --- | --- |
| V1 web | Server (Postgres + object storage) | Render | Remote API | Server-side |
| V2 fully local | User's machine (SQLite + FS) | User's machine | Local (Ollama) | Disabled |
| V2 hybrid | User's machine | User's machine | Remote GPU | Encrypted remote |
| V2 self-hosted | User's machine | User's machine | User's own server | User's own server |

If a V1 decision makes any row above impossible, the decision is wrong.

---

## 3. Provider interfaces

The ten seams below are the mechanism that makes section 2 true. Each is defined
as a Protocol in `services/api/app/<area>/base.py`. V1 ships remote
implementations; V2 adds local ones behind the same interface.

| Interface | V1 implementation | V2 adds |
| --- | --- | --- |
| `InferenceProvider` | `OpenAIProvider`, `AnthropicProvider` | `OllamaProvider`, `RemoteVLLMProvider` |
| `StorageProvider` | `PostgresStorage` | `SQLiteStorage` |
| `FileStorageProvider` | `ObjectStorage` (S3-compatible) | `LocalFileStorage` |
| `EmbeddingProvider` | remote embeddings API | local embedding model |
| `VectorStoreProvider` | `MilvusStore` (via mmore) | `MilvusLiteStore` / `SqliteVecStore` |
| `PaperSearchProvider` | arXiv, OpenAlex, Europe PMC, Google Scholar | cached/offline corpus |
| `ScreeningProvider` | LLM-assisted, via `InferenceProvider` | local model |
| `SanitizationProvider` | mmore privacy mode, `interactive: api` | same pipeline, `interactive: local` |
| `BackupProvider` | server-side (implicit) | `EncryptedRemoteBackup`, user-configured target |
| `ToolProvider` | in-process tools | same, plus OS-level tools |

### 3.1 InferenceProvider

```python
class InferenceProvider(Protocol):
    async def list_models(self) -> Sequence[ModelInfo]: ...
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]: ...
```

`CompletionRequest` carries messages, model id, and generation parameters as
**domain types**, never a provider's wire format. Model ids are opaque strings
resolved by the provider, so a hosted model and a local one are interchangeable
to every layer above.

### 3.2 StorageProvider

Persistence is exposed as repositories behind a unit of work, not as a connection.

```python
class StorageProvider(Protocol):
    def unit_of_work(self) -> AsyncContextManager[UnitOfWork]: ...

class UnitOfWork(Protocol):
    users: UserRepository
    reviews: ReviewRepository
    vocabularies: VocabularyRepository
    papers: PaperRecordRepository
    screenings: ScreeningDecisionRepository
    syntheses: SynthesisRepository
    runs: RunManifestRepository
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

Constraint: no raw SQL and no SQLAlchemy types above `storage/`. This is what
lets Postgres become SQLite in V2 without touching a use case.

### 3.3 FileStorageProvider

```python
class FileStorageProvider(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes], content_type: str) -> FileRef: ...
    def get(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def url_for(self, key: str, expires_in: timedelta) -> str | None: ...
```

`url_for` returns `None` when the backend cannot mint a URL (V2 local files).
Callers must handle `None` by streaming through the API instead of redirecting.
Designing for that on day one is what keeps V2 from needing new endpoints.

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
from different models are not comparable, and V2 will change the model.

### 3.5 BackupProvider

Defined in V1, implemented in V2. Declaring it now keeps export shaped correctly.

```python
class BackupProvider(Protocol):
    async def create(self, scope: BackupScope) -> BackupManifest: ...
    async def list(self) -> Sequence[BackupManifest]: ...
    async def restore(self, manifest_id: str) -> RestoreReport: ...
```

Backups are encrypted client-side before transport. The provider handles opaque
ciphertext and never holds the key.

### 3.6 ToolProvider

```python
class ToolProvider(Protocol):
    def list_tools(self) -> Sequence[ToolSpec]: ...
    async def invoke(
        self, name: str, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> ToolResult: ...
```

`ToolContext` carries the caller's identity and permissions. Tools are always
checked against it, because in V2 a tool may touch the user's real filesystem.

### 3.7 VectorStoreProvider

mmore indexes into Milvus. Milvus must not leak above `storage/`, or V2's move to
Milvus Lite becomes a rewrite rather than a swap.

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

### 3.8 PaperSearchProvider

One interface over arXiv, OpenAlex, Europe PMC and Google Scholar. Each has a
different query syntax, rate limit, and metadata shape; none of that belongs in
the application layer.

```python
class PaperSearchProvider(Protocol):
    @property
    def source_id(self) -> str: ...
    async def search(self, query: BooleanQuery, limit: int) -> Sequence[PaperRecord]: ...
    async def fetch_fulltext(self, record: PaperRecord) -> Document | None: ...
```

`BooleanQuery` is a **domain type**, not a source-specific string. The provider
compiles it into that repository's syntax. This is the seam that makes a search
reproducible across sources, and it is the single most important interface in the
system — everything downstream inherits its provenance.

`fetch_fulltext` returns `None` when the document is not openly available. That is
a normal outcome, not an error, and screening must handle metadata-only records.

### 3.9 ScreeningProvider

```python
class ScreeningProvider(Protocol):
    async def screen(
        self, paper: PaperRecord, criteria: Sequence[Criterion]
    ) -> ScreeningDecision: ...
```

`ScreeningDecision` records the score, the priority, the rationale, **and the
provenance**: model id, prompt version, and criteria version. Auditability is a
stated project commitment — a reviewer must be able to ask why a paper was
excluded and get an answer that names what produced the decision.

Screening never makes the final call. The interface returns an assessment; the
domain decides what a human still has to see.

### 3.10 SanitizationProvider

mmore ships a privacy mode (`src/mmore/privacy/`) that sanitises retrieved context
before it reaches the answer model, and it has `humanitarian` as a first-class
domain profile. We use it, but behind our own interface — never by shelling out to
`mmore rag --privacy`.

```python
class SanitizationProvider(Protocol):
    async def sanitize(
        self, chunks: Sequence[Chunk], query: str, policy: PrivacyPolicy
    ) -> SanitizationResult: ...
```

The mmore pipeline runs
`analyzer -> detector -> sanitizer -> leakage_adversary -> HITL gate -> answer -> verifier -> report`.

`SanitizationResult` carries the sanitised chunks, the verifier warnings, and the
`ReportRecord`, which feeds `RunManifest`.

**Locked configuration choices:**

| Key | Value | Reason |
| --- | --- | --- |
| `domain` | `humanitarian` | Built for exactly this setting |
| `detection.engine` | `presidio` or `gliner` | Both run locally. **Never `openai_filter`** — it sends unsanitised text to OpenAI, inverting the purpose |
| `sanitization.strategy` | `entity_replacement` or `synthetic_rewrite` | `token_masking` degrades synthesis quality |
| `answer.llm` | our `InferenceProvider` default | Never a third-party default |
| `abort_on_exhaustion` | `true` | Fail closed. Keep the default |
| `verifier.checks` | `residual_leakage`, `faithfulness` | Both |

**Known limitation — this is query-time only.** The pipeline sanitises retrieved
chunks; it does not alter what is stored. Raw text remains in the vector store and
the database. In V2 that is acceptable, because storage is the user's own machine.
In V1 it means unsanitised material sits on our server, which the privacy claim
does not cover. Closing that gap requires sanitising at ingest as well, which mmore
does not do — it would be our own pre-index stage.

> **OPEN WORK — ingest-time sanitisation.**
>
> A stage between extraction and indexing that sanitises text *before* it is
> written to the vector store or the database, so nothing unsanitised is ever
> persisted server-side.
>
> Not yet designed. Questions to settle before building it:
>
> - **Reversible or destructive?** `sanitization.encryption_key` allows reversible
>   encryption, which preserves the ability to re-screen later but means the key
>   becomes the thing that must be protected. Destructive masking is safer and
>   loses information permanently.
> - **What breaks retrieval?** Sanitised text embeds differently from raw text.
>   Masking author names and institutions may degrade the searchability of an
>   academic corpus in ways that matter.
> - **Is it needed at all for published literature?** The corpus is largely public
>   papers, which carry little PII. The exposure is more likely in the *user's*
>   question and any uploaded documents. Sanitising those two inputs may cover the
>   real risk at a fraction of the cost.
> - **Where does it run?** Reusing mmore's detector and sanitiser agents outside
>   the RAG pipeline, versus writing a standalone stage.
>
> Deferred deliberately: the third question may make the whole stage unnecessary,
> and answering it costs an afternoon of looking at real data. Do that first.

**The HITL gate shapes the API contract.** A gated request pauses awaiting human
approval, which a plain request/response cannot express. See §4.2.

---

## 4. Domain model

The core entities are **not** `Conversation` and `Message`. The spine of the
application is a reproducible review run:

```text
Review          a question, a Vocabulary, and a set of Criteria
  Vocabulary    curated concepts with synonyms, grouped into themes
  BooleanQuery  generated from the Vocabulary, compiled per source
  PaperRecord   bibliographic metadata + source + dedup identity
  Document      extracted full text, when openly available
  Criterion     an inclusion/exclusion rule
  ScreeningDecision  score, priority, rationale, provenance
  Synthesis     agentic output, grounded in Citations
  Citation      a claim bound to a PaperRecord
  RunManifest   everything needed to reproduce or diff a run
```

`RunManifest` is what makes reproducibility real: vocabulary version, compiled
queries, sources queried, model ids, prompt versions, timestamps. Two runs are
comparable only if both recorded one.

Conversations may still exist — the agentic loop produces a transcript, and a user
may ask follow-up questions — but they are an *artefact of* a Review, not the
organising entity. Designing around `Conversation` first would put the least
durable concept at the centre.

### 4.1 How a Vocabulary is acquired

A `Vocabulary` enters the system by one of two routes, and they have different
rules:

**Route A — LLM-assisted, multi-turn.** The user asks a question. The model
proposes concepts with synonyms and near-synonyms, grouped into themes. The user
reviews, edits, adds, removes, and confirms. This iterates until the user accepts.

**Route B — user-supplied, strict.** The user uploads their own vocabulary. The
pipeline uses it **exactly as given**. No LLM expansion, no synonym enrichment, no
silent normalisation beyond documented parsing. A researcher who brings a
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
   Search results reference the version they were produced from, or reproducibility
   is lost.
2. **`origin` is recorded and surfaced.** A reviewer must be able to tell whether a
   term came from a model or a person.
3. **Route B never routes through the model.** Strict means strict — this is a
   correctness property, not a preference, and it deserves a test.

The multi-turn confirmation in Route A is a conversation, but the *product* of that
conversation is a `Vocabulary`, not a `Conversation`. The transcript is an
artefact; the vocabulary is the entity.

### 4.2 Gated requests

Two flows pause mid-execution for a human: vocabulary confirmation (§4.1) and the
privacy HITL gate (§3.10). Neither fits request/response, and both must be in the
API contract before it is frozen.

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

## 5. Dependencies and prior art

humani-t is a rebuild of an existing prototype, not a greenfield project. See
[`dependencies.md`](./dependencies.md) for the full analysis and open decisions.

Summary: **CHITCHAT is this project's first prototype**, so its
[overview](./CHITCHAT-OVERVIEW.md) functions as the requirements document and its
six pipeline stages map onto the build order. `mmore` is the one real external
dependency (`uv add mmore`). AMMORE is a **pattern we reimplement**, not a package
— we take its Planner/Retriever/Critic/Writer loop and reject its OpenAI default,
its Next.js frontend, and its silent web-search fallback.

---

## 6. Version 1 vs Version 2

|                               | **Version 1 - Web Application**                | **Version 2 - Local Application**                                                                         |
| ----------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| **UI**                        | Web                                            | Tauri desktop app                                                                                         |
| **Frontend**                  | Shared frontend                                | Same shared frontend                                                                                      |
| **API**                       | Remote API                                     | Local API                                                                                                 |
| **Backend**                   | Server-based                                   | User's machine                                                                                            |
| **Database**                  | PostgreSQL                                     | SQLite                                                                                                    |
| **Files**                     | Remote/object storage                          | Local filesystem                                                                                          |
| **LLM / Inference**           | Remote LLM/API                                 | Local LLM **or** remote LLM                                                                               |
| **Network**                   | Required                                       | Optional                                                                                                  |
| **Privacy model**             | Privacy-preserving server architecture         | **Local-first / privacy by default**                                                                      |
| **User data location**        | Primarily on the service's infrastructure      | **Primarily on the user's machine**                                                                       |
| **Remote backup**             | Built into server infrastructure               | **Optional** - user can send selected/all local data to a remote server                                   |
| **Remote server**             | Your application's server                      | **Your server or a server the user configures/controls**                                                  |
| **Remote data transfer**      | Normal operation                               | **Explicitly initiated/consented to by the user**                                                         |
| **Protection of remote data** | Server security + encryption + access controls | **Encryption in transit, encryption at rest, access controls, and clearly defined retention/usage terms** |
| **Rust**                      | None/minimal                                   | Tauri runtime + native OS integration                                                                     |
| **Python**                    | Server-side backend                            | Local backend/sidecar                                                                                     |
| **Primary goal**              | Accessible anywhere                            | **Maximum user control and privacy**                                                                      |

---

## 7. The test for every V1 decision

> Will this still make sense when the backend is running on the user's laptop
> instead of Render?

If no, introduce the abstraction before proceeding:

```text
PostgreSQL       -> StorageProvider
S3               -> FileStorageProvider
OpenAI           -> InferenceProvider
Milvus           -> VectorStoreProvider
arXiv / OpenAlex -> PaperSearchProvider
Redis            -> QueueProvider
Remote API       -> API contract
Cloud backup     -> BackupProvider
```

There is a second test specific to this project:

> Could a reviewer reconstruct why this paper was included or excluded, six
> months from now, from what we stored?

If no, the decision is not auditable, and auditability is not a feature that can
be added later — it depends on provenance being captured at the moment the
decision was made.
