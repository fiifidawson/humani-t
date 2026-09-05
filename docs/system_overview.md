# Privacy-First AI Application — Sequential Development Plan

## Goal

Build a privacy-first AI application in two deliberate versions:

- **Version 1:** Web application + server-based backend, deployed on Render.
- **Version 2:** Local-first desktop application + local backend, with optional remote inference and optional user-controlled remote backup/synchronization.

The core architectural rule is:

> **Build Version 1 so that the application domain, API contracts, and provider abstractions survive when the backend moves from the server to the user's machine.**

Version 2 should therefore be a new deployment/runtime of the same application, not a rewrite.

---

# Part 0 — Architecture Decisions to Lock Before Coding

Before implementing features, write down these decisions in `docs/architecture.md`.

## 0.1 Core principle

Separate the system into:

```text
Presentation
    ↓
Application API
    ↓
Application / Domain Logic
    ↓
Infrastructure
```

Do not allow the frontend to depend directly on PostgreSQL, Ollama, filesystem paths, provider-specific SDKs, etc.

## 0.2 Separate three concerns

Keep these independently replaceable:

```text
1. User data
2. Application/runtime
3. Model inference
```

This is what makes the following configurations possible later:

```text
Data: Local
Inference: Local
Backup: Disabled
```

```text
Data: Local
Inference: Remote GPU
Backup: Encrypted remote server
```

```text
Data: Local
Inference: User's own server
Backup: User's own server
```

## 0.3 Provider interfaces

Define interfaces/abstractions early for:

- `InferenceProvider`
- `StorageProvider`
- `FileStorageProvider`
- `EmbeddingProvider`
- `BackupProvider`
- `ToolProvider`

Version 1 will mostly contain remote implementations.

Version 2 adds local implementations.

Example:

```text
InferenceProvider
├── OpenAIProvider
├── AnthropicProvider
├── RemoteVLLMProvider
└── OllamaProvider          # V2

StorageProvider
├── PostgreSQLStorage       # V1
└── SQLiteStorage           # V2

FileStorageProvider
├── ObjectStorage           # V1
└── LocalFileStorage        # V2
```

---

# Part 1 — Create the Monorepo Structure

> **Superseded.** The layout below is an early sketch. The canonical
> structure is [`repo_structure.md`](./repo_structure.md), which uses
> `services/api/` instead of `backend/` and adds `crates/`.

This is the first implementation step.

Recommended initial structure:

```text
privacy-ai/
│
├── apps/
│   ├── web/
│   │   ├── src/
│   │   ├── public/
│   │   ├── package.json
│   │   └── ...
│   │
│   └── desktop/
│       └── README.md
│
├── packages/
│   ├── ui/
│   │   ├── src/
│   │   └── package.json
│   │
│   ├── api-client/
│   │   ├── src/
│   │   └── package.json
│   │
│   ├── schemas/
│   │   ├── openapi/
│   │   ├── generated/
│   │   └── ...
│   │
│   └── config/
│       ├── src/
│       └── package.json
│
├── backend/
│   ├── api/
│   ├── application/
│   ├── domain/
│   ├── infrastructure/
│   ├── inference/
│   ├── storage/
│   ├── agents/
│   ├── auth/
│   └── tests/
│
├── migrations/
│
├── infrastructure/
│   ├── docker/
│   ├── render/
│   └── local/
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── privacy.md
│   ├── security.md
│   └── development.md
│
├── scripts/
│
├── .env.example
├── .gitignore
├── README.md
└── package.json
```

### Why this structure?

The important separation is:

```text
apps/          → deployable applications
packages/      → code shared by applications
backend/       → server-side application logic
infrastructure/ → deployment/runtime configuration
```

Do **not** put business logic inside `apps/web`.

The web application should mostly be a UI that consumes the API.

---

# Part 2 — Set Up the Frontend Workspace

Choose a frontend stack. A practical choice is:

```text
React
TypeScript
Vite
```

Use a workspace/package manager such as:

```text
pnpm workspaces
```

or another monorepo tool you are comfortable with.

The frontend architecture should become:

```text
apps/web
    ↓
shared UI packages
    ↓
api-client
```

## 2.1 Shared UI

Move reusable components into:

```text
packages/ui/
```

Examples:

```text
Button
ChatMessage
ChatInput
ConversationList
FileUploader
SettingsPanel
ModelSelector
```

The actual web/desktop entry points should compose these components.

---

# Part 3 — Design the API Contract Before Building Features

This is one of the most important steps for Version 2.

Define the API independently from the implementation.

For example:

```text
POST   /api/v1/chat
GET    /api/v1/conversations
POST   /api/v1/conversations
GET    /api/v1/conversations/:id
DELETE /api/v1/conversations/:id

POST   /api/v1/files
GET    /api/v1/files
DELETE /api/v1/files/:id

GET    /api/v1/models
GET    /api/v1/health
```

Define request/response schemas using OpenAPI or JSON Schema.

For example:

```json
{
  "message": "Summarize this document.",
  "conversation_id": "...",
  "model": "..."
}
```

The exact transport can later change, but the conceptual API should remain stable.

Version 1:

```text
Frontend → HTTPS → Remote API
```

Version 2:

```text
Frontend → localhost → Local API
```

The frontend should not need to know how the API is implemented.

---

# Part 4 — Build the API Client Abstraction

Do not scatter raw `fetch()` calls throughout the frontend.

Create:

```text
packages/api-client/
```

Define an interface such as:

```typescript
interface AppAPI {
  chat(...)
  getConversations(...)
  createConversation(...)
  deleteConversation(...)
  uploadFile(...)
  listFiles(...)
  getModels(...)
}
```

Then implement:

```text
RemoteAPIClient
```

for Version 1.

Later add:

```text
LocalAPIClient
```

for Version 2.

The frontend depends only on:

```text
AppAPI
```

not the implementation.

---

# Part 5 — Build the Python Backend Skeleton

Use FastAPI for the V1 backend.

Recommended structure:

```text
backend/
├── api/
│   ├── routes/
│   │   ├── chat.py
│   │   ├── conversations.py
│   │   ├── files.py
│   │   └── models.py
│   └── dependencies.py
│
├── application/
│   ├── chat_service.py
│   ├── conversation_service.py
│   ├── file_service.py
│   └── model_service.py
│
├── domain/
│   ├── conversation.py
│   ├── message.py
│   ├── document.py
│   └── user.py
│
├── inference/
│   ├── base.py
│   ├── openai.py
│   ├── anthropic.py
│   └── vllm.py
│
├── storage/
│   ├── base.py
│   ├── postgres.py
│   └── repositories/
│
├── agents/
├── auth/
└── main.py
```

The important pattern is:

```text
Route
  ↓
Application service
  ↓
Domain
  ↓
Repository/provider
```

Avoid:

```text
Route
  ↓
SQL
  ↓
OpenAI SDK
```

because that makes Version 2 migration painful.

---

# Part 6 — Implement Database Models and Persistence

Use PostgreSQL for Version 1.

Start with the minimum domain:

```text
User
Conversation
Message
File
ModelConfiguration
```

Potential relationship:

```text
User
 └── Conversations
      └── Messages

User
 └── Files
```

Use migrations from the beginning.

Recommended:

```text
Alembic
```

Do not rely on manually modifying production databases.

---

# Part 7 — Add Authentication and User Isolation

Before adding advanced AI functionality, make sure users cannot access another user's data.

Implement:

```text
Authentication
Authorization
Tenant/user isolation
```

Every data query should ultimately be scoped to the authenticated user.

For example:

```text
get_conversation(conversation_id, user_id)
```

rather than:

```text
get_conversation(conversation_id)
```

This matters even more when you later introduce backup/sync.

---

# Part 8 — Implement the Inference Abstraction

Define:

```python
class InferenceProvider:
    async def generate(...):
        ...
```

Then implement at least one V1 provider.

For example:

```text
inference/
├── base.py
├── openai.py
└── vllm.py
```

The application service should depend on:

```text
InferenceProvider
```

not an OpenAI/Anthropic-specific SDK.

Flow:

```text
POST /chat
   ↓
ChatService
   ↓
InferenceProvider
   ↓
Model
```

---

# Part 9 — Add Streaming

For an AI application, streaming should be designed into the API rather than bolted on later.

Possible options:

```text
Server-Sent Events
WebSocket
```

Start with SSE if your interaction model is primarily server-to-client token streaming.

Conceptually:

```text
Browser
  ↓
POST /chat
  ↓
Backend
  ↓
LLM
  ↓
stream tokens
  ↓
Browser
```

This same concept can work with a local backend in V2.

---

# Part 10 — Add File Handling

Implement a file pipeline:

```text
Upload
  ↓
Validation
  ↓
Storage
  ↓
Extraction
  ↓
Processing
  ↓
Optional indexing
```

Keep storage abstract.

V1:

```text
FileStorageProvider
       ↓
S3-compatible/object storage
```

V2:

```text
FileStorageProvider
       ↓
Local filesystem
```

Do not make the rest of the application depend on S3 APIs.

---

# Part 11 — Add Document Processing

For example:

```text
PDF
DOCX
TXT
CSV
```

Build a document-processing layer:

```text
backend/
└── document_processing/
    ├── extractors/
    ├── chunking/
    ├── metadata/
    └── indexing/
```

Keep this independent from where the original file lives.

Later:

```text
V1: remote file → processing
V2: local file → local processing
```

---

# Part 12 — Add RAG Only After Basic Chat Works

Once basic chat is stable:

```text
Files
  ↓
Text extraction
  ↓
Chunking
  ↓
Embeddings
  ↓
Vector search
  ↓
Context builder
  ↓
LLM
```

Again create provider abstractions:

```text
EmbeddingProvider
VectorStore
```

This lets you later replace remote infrastructure with local implementations.

---

# Part 13 — Introduce the Agent/Tool Layer Carefully

Only after the basic chat pipeline works.

Create:

```text
agents/
├── runtime.py
├── tools/
│   ├── registry.py
│   ├── filesystem.py
│   ├── web.py
│   └── ...
├── permissions/
└── context.py
```

Think of an agent as:

```text
Agent
 ├── Model
 ├── Context
 ├── Tools
 ├── Permissions
 └── Execution
```

For Version 1, keep tools server-side and tightly controlled.

Do not give the server arbitrary shell access just to prototype.

---

# Part 14 — Build Privacy Into Version 1

Even though the web version is server-based, design it as a privacy-preserving application.

Document:

```text
What data is stored?
What data is sent to an LLM provider?
How long is it retained?
Who can access it?
What logs contain user data?
What can administrators see?
Can users delete their data?
```

Implement:

```text
Encryption in transit
Encryption at rest
Secure secrets
User-level authorization
Data deletion
Minimal logging
Provider-specific data controls
```

Do not log complete conversations by default.

Prefer:

```text
request_id
user_id
timestamp
latency
model
token counts
status
```

over raw prompt/response content.

---

# Part 15 — Build a Data Export/Delete System

Before V1 is considered mature, users should be able to:

```text
Export my data
Delete my data
```

Export could produce:

```text
ZIP
├── conversations.json
├── settings.json
└── metadata.json
```

This will become especially useful in V2.

Your V2 local application can use the same conceptual export format for backup.

---

# Part 16 — Add Remote Backup as a Concept, But Do Not Implement V2 Yet

Even in V1, define what a backup is.

A useful conceptual model:

```text
Application data
      ↓
Export/snapshot format
      ↓
Encryption
      ↓
Remote storage
```

Do not tightly couple backups to the live application database.

Create a portable application data format that can eventually be used by both versions.

For example:

```text
backup/
├── manifest.json
├── conversations/
├── files/
└── metadata/
```

The goal is:

```text
V2 local application
       ↓
encrypted backup
       ↓
remote server
```

to be straightforward later.

---

# Part 17 — Containerize the V1 Backend

Create:

```text
infrastructure/docker/backend.Dockerfile
```

Make the backend run correctly inside Docker locally before deploying it.

The objective is:

```text
docker compose up
```

and you have:

```text
Backend
PostgreSQL
Optional Redis
```

Avoid adding Redis/Celery unless you have a real asynchronous workload that needs them.

---

# Part 18 — Design the Render Deployment

Render supports monorepos and can deploy individual applications/services from a repository. You can configure a service's root directory and build filters so unrelated changes do not automatically trigger every deployment. citeturn460032search0turn460032search1

For V1, start simple:

```text
Render
├── Web/API service
└── PostgreSQL
```

Add background workers only when you have actual long-running jobs. Render supports background workers for asynchronous tasks and queue-driven workloads. citeturn460032search3turn460032search4

### Suggested V1 deployment

```text
Internet
   ↓
Render Web Service
   ↓
FastAPI
   ↓
Render PostgreSQL
```

For heavy model workloads, consider a separate inference service rather than running the model inside the main API service.

Render supports Docker-based services, so Docker is a reasonable choice when you want consistent runtime packaging. citeturn460032search2

---

# Part 19 — Create Render Infrastructure as Code

Create:

```text
infrastructure/render/render.yaml
```

Keep deployment configuration in Git.

Conceptually:

```text
Render Web Service
        │
        ├── DATABASE_URL
        ├── SECRET_KEY
        ├── MODEL_PROVIDER
        └── PROVIDER_API_KEY
```

Never commit secrets.

Use Render environment variables/secrets.

Use a health endpoint:

```text
GET /health
```

and make deployment depend on it.

For paid Render services, a pre-deploy command can be used for database migrations and similar deployment tasks. citeturn460032search8

---

# Part 20 — Deploy V1 to Render

Deploy the backend first.

Verify:

```text
/health
/auth
/conversations
/chat
/files
/models
```

Then deploy the frontend.

If the frontend is static, it can be deployed separately as a Render Static Site; if it requires server-side execution, use a Web Service. Render documents both service types. citeturn460032search1turn460032search4

Recommended initial setup:

```text
Render
│
├── web-frontend
│
├── api
│
└── postgres
```

Keep frontend and API independently deployable.

---

# Part 21 — V1 Testing

Before beginning Version 2, test:

## Functional

```text
Create account
Login
Create conversation
Send message
Stream response
Upload file
Process file
RAG/search
Delete conversation
Delete file
Export data
Delete account/data
```

## Security

```text
User A cannot access User B's conversation
User A cannot access User B's files
Expired sessions are rejected
Invalid IDs don't leak information
Secrets never appear in client code
Logs do not contain sensitive content
```

## Reliability

```text
Model unavailable
Database unavailable
File upload fails
LLM timeout
Streaming disconnects
Malformed request
Large file
Provider rate limit
```

---

# Part 22 — Freeze the V1 Domain/API

This is the transition checkpoint.

Before starting V2, document:

```text
Domain models
API contracts
Provider interfaces
Data export format
Authentication assumptions
File format
Conversation format
```

At this point, treat the following as stable interfaces:

```text
AppAPI
InferenceProvider
StorageProvider
FileStorageProvider
EmbeddingProvider
BackupProvider
```

This is what prevents V2 from becoming a rewrite.

---

# VERSION 2 — Local-First Desktop Application

Now begin the second version.

The V2 goal is:

```text
Tauri desktop application
        ↓
Local backend
        ↓
Local data
        ↓
Local or remote inference
```

---

# Part 23 — Add Tauri Without Rebuilding the Frontend

Create:

```text
apps/desktop/
```

The desktop app should reuse:

```text
packages/ui
packages/api-client
packages/schemas
```

The desktop entry point should look conceptually like:

```text
apps/desktop
       ↓
shared frontend
       ↓
local API client
```

Do not duplicate the UI.

---

# Part 24 — Make the API Client Deployment-Agnostic

At this point the frontend should be able to run against:

```text
RemoteAPIClient
```

or:

```text
LocalAPIClient
```

Configuration becomes:

```text
WEB
→ https://api.example.com

DESKTOP
→ http://127.0.0.1:<port>
```

The UI remains unchanged.

---

# Part 25 — Move the Backend Into a Local Process

Reuse the Python backend created for V1.

The first V2 architecture should be:

```text
Tauri
 ├── Frontend
 │
 └── Python/FastAPI local process
         │
         ├── SQLite
         ├── local filesystem
         └── inference
```

Do not rewrite the application logic.

Replace infrastructure implementations.

---

# Part 26 — Add Rust/Tauri Process Management

Rust should be responsible for:

```text
Start local Python service
Stop local Python service
Check health
Pass configuration
Manage desktop lifecycle
Provide native OS integration
```

Package the Python backend as a sidecar/executable rather than requiring users to install Python.

Tauri supports bundling external binaries as sidecars, which is directly relevant to this design.

---

# Part 27 — Replace PostgreSQL With SQLite

Implement:

```text
SQLiteStorageProvider
```

while preserving the same repository/application interfaces.

Your architecture should become:

```text
Application services
       ↓
StorageProvider
       ↓
SQLite
```

instead of:

```text
Application services
       ↓
PostgreSQL
```

The domain model should remain the same wherever possible.

---

# Part 28 — Replace Remote File Storage With Local Files

Implement:

```text
LocalFileStorageProvider
```

Example:

```text
App data directory/
├── database/
├── files/
├── indexes/
├── cache/
└── config/
```

Do not hard-code paths.

Use Tauri/Rust/OS APIs to determine appropriate application data directories.

---

# Part 29 — Add Local Inference

Implement:

```text
OllamaProvider
```

first because it gives you a relatively simple local model-server interface.

Then consider:

```text
llama.cpp
other local runtimes
```

The application continues to use:

```text
InferenceProvider
```

so no major frontend changes are necessary.

---

# Part 30 — Add Remote Inference to V2

This is where the local-first architecture becomes powerful.

Add a configuration such as:

```text
Inference mode:

[ Local ]
[ Remote ]
```

Remote could point to:

```text
Your managed inference server
User's own server
Custom OpenAI-compatible endpoint
```

Architecture:

```text
                  Local Application
                         │
                  InferenceProvider
                    /            \
                   /              \
          Local model          Remote server
```

The user can therefore keep the data locally while borrowing remote GPU compute.

---

# Part 31 — Add User-Controlled Remote Backup

Implement:

```text
BackupProvider
```

The backup workflow should be explicit:

```text
Local data
   ↓
Create snapshot
   ↓
Encrypt locally
   ↓
Upload ciphertext
   ↓
Remote server
```

Important:

> The remote backup server should not need access to the user's plaintext application database merely to store a backup.

Support destinations such as:

```text
Your managed server
User's personal server
Compatible object storage
```

---

# Part 32 — Add Server Terms/Policies for Remote Storage

When the user selects a remote service, clearly show:

```text
What is uploaded
Who operates the server
Encryption used
Retention period
Whether the operator can decrypt data
Deletion policy
Terms of service
Privacy policy
```

The application should require explicit user consent before the first upload.

Do not describe remote storage as absolutely private or "100% secure."

Instead define measurable guarantees:

```text
Encrypted in transit
Encrypted before upload
Access controlled
Retention documented
Deletion supported
```

The exact guarantees depend on the server implementation.

---

# Part 33 — Add Restore

Users should be able to:

```text
Choose backup
      ↓
Download encrypted snapshot
      ↓
Decrypt locally
      ↓
Validate manifest
      ↓
Restore database/files
```

Test restoring a backup before calling the backup system complete.

A backup that cannot be restored is not a useful backup.

---

# Part 34 — Only Then Consider Synchronization

Do not jump from backup directly into full sync.

First:

```text
Local → Remote backup
Remote → Local restore
```

Then, only if required:

```text
Device A
   ↕
Remote
   ↕
Device B
```

Synchronization introduces:

```text
versioning
conflicts
deletions
concurrent edits
offline changes
```

Treat this as a separate feature.

---

# Part 35 — Add Desktop Security Boundaries

This becomes especially important if the application supports agents/tools.

Define permissions for:

```text
Filesystem
Terminal
Network
Browser
External processes
Secrets
```

Avoid giving an agent unrestricted access to the user's computer by default.

A useful model is:

```text
Tool
 ↓
Permission
 ↓
User approval / policy
 ↓
Execution
```

For example:

```text
Read file        → allowed
Write file       → ask
Delete file      → ask
Run shell command→ ask
Network request  → policy
```

---

# Part 36 — Build V2 Packaging

Eventually package:

```text
Windows
macOS
Linux
```

The resulting application should contain:

```text
Tauri executable
Frontend assets
Python backend executable
Required runtime components
Configuration
```

The user should not need to manually set up:

```text
Python
Node
FastAPI
SQLite
```

for normal usage.

---

# Part 37 — V2 Testing Matrix

Test at least these modes:

### Local everything

```text
UI
 ↓
Local API
 ↓
SQLite
 ↓
Local LLM
```

### Local data + remote inference

```text
UI
 ↓
Local API
 ↓
Remote inference
```

### Local data + encrypted remote backup

```text
Local API
 ↓
Encrypted backup
 ↓
Remote server
```

### Local data + user's own inference server

```text
Local application
 ↓
User-configured endpoint
```

### Restore

```text
Remote backup
 ↓
Local decryption
 ↓
SQLite/files
```

---

# Final Target Architecture

When both versions are complete:

```text
                         SHARED FRONTEND
                               │
                         SHARED API CLIENT
                               │
                    ┌──────────┴──────────┐
                    │                     │
                 VERSION 1             VERSION 2
                    │                     │
                 Browser               Tauri
                    │                     │
                 HTTPS                 localhost
                    │                     │
                    ▼                     ▼
              Remote API              Local API
                    │                     │
           ┌────────┴────────┐     ┌──────┴─────────┐
           │                 │     │                │
       PostgreSQL       Object     SQLite       Local Files
                         Storage
           │
           ▼
     InferenceProvider
           │
     ┌─────┴────────────┐
     │                  │
 Remote LLM/API     Remote GPU


                     VERSION 2
                         │
                InferenceProvider
                   /           \
                  /             \
             Local LLM       Remote LLM
```

And for backup:

```text
V2 Local Data
      │
      ▼
Local Encryption
      │
      ▼
Encrypted Backup
      │
      ├── Your server
      ├── User's server
      └── Compatible storage
```

---

# Recommended Build Order — Condensed Checklist

Use this as the actual implementation order:

```text
[ ] 1. Create monorepo
[ ] 2. Create shared frontend package
[ ] 3. Create web application
[ ] 4. Create API schemas
[ ] 5. Create API client abstraction
[ ] 6. Create FastAPI backend
[ ] 7. Create domain/application layers
[ ] 8. Add PostgreSQL + migrations
[ ] 9. Add authentication/authorization
[ ] 10. Add inference provider abstraction
[ ] 11. Add first LLM provider
[ ] 12. Add streaming
[ ] 13. Add conversations
[ ] 14. Add file storage abstraction
[ ] 15. Add document processing
[ ] 16. Add RAG
[ ] 17. Add tools/agents
[ ] 18. Add privacy/security controls
[ ] 19. Add export/delete
[ ] 20. Dockerize backend
[ ] 21. Create Render configuration
[ ] 22. Deploy V1 to Render
[ ] 23. Test V1 thoroughly
[ ] 24. Freeze API/domain contracts
────────────────────────────────────────
[ ] 25. Create Tauri application
[ ] 26. Reuse shared frontend
[ ] 27. Add local API client
[ ] 28. Run Python backend locally
[ ] 29. Package Python as Tauri sidecar
[ ] 30. Replace PostgreSQL with SQLite
[ ] 31. Replace object storage with local filesystem
[ ] 32. Add local inference provider
[ ] 33. Add remote inference configuration
[ ] 34. Add encrypted backup
[ ] 35. Add user-selected remote server
[ ] 36. Add restore
[ ] 37. Add desktop permissions/sandboxing
[ ] 38. Package desktop application
[ ] 39. Test all deployment modes
[ ] 40. Consider sync as a separate future feature
```

# The Most Important Rule Throughout

Whenever you are about to make an implementation decision in V1, ask:

> **"Will this still make sense when the backend is running on the user's laptop instead of Render?"**

If the answer is no, introduce an abstraction before proceeding.

That means:

```text
PostgreSQL          → StorageProvider
S3                  → FileStorageProvider
OpenAI              → InferenceProvider
Redis               → QueueProvider
Remote API          → API contract
Cloud backup        → BackupProvider
```

This is the single biggest thing that will determine whether the transition from V1 to V2 is smooth.
