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

The six seams below are the mechanism that makes section 2 true. Each is defined
as a Protocol in `services/api/app/<area>/base.py`. V1 ships remote
implementations; V2 adds local ones behind the same interface.

| Interface | V1 implementation | V2 adds |
| --- | --- | --- |
| `InferenceProvider` | `OpenAIProvider`, `AnthropicProvider` | `OllamaProvider`, `RemoteVLLMProvider` |
| `StorageProvider` | `PostgresStorage` | `SQLiteStorage` |
| `FileStorageProvider` | `ObjectStorage` (S3-compatible) | `LocalFileStorage` |
| `EmbeddingProvider` | remote embeddings API | local embedding model |
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
    conversations: ConversationRepository
    messages: MessageRepository
    documents: DocumentRepository
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

---

## 4. Version 1 vs Version 2

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

## 5. The test for every V1 decision

> Will this still make sense when the backend is running on the user's laptop
> instead of Render?

If no, introduce the abstraction before proceeding:

```text
PostgreSQL   -> StorageProvider
S3           -> FileStorageProvider
OpenAI       -> InferenceProvider
Redis        -> QueueProvider
Remote API   -> API contract
Cloud backup -> BackupProvider
```
