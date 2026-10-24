# ADR-0001: Web application only, self-hosted with Docker Compose

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

humani-t was planned in two versions. V1 was a web application on a hosted
server (Render). V2 was a local-first desktop application (Tauri), with SQLite,
local inference and an optional encrypted remote backup.

Keeping V2 possible shaped almost every V1 decision:

- the rule "will this still make sense running on someone's laptop?";
- a `BackupProvider` that V1 never used;
- a `url_for` method that returns `None` for local files;
- an `apps/desktop` shell and a `crates/` directory;
- an API client package with a `LocalAPIClient`.

The project's real commitment is privacy and security for humanitarian teams. A
desktop app is one way to get there, but it doubles the surface to build,
secure, test and package. It also delays the security work a server deployment
needs anyway: accounts, sessions, MFA, access control, encryption at rest and an
audit log.

## Decision

humani-t is a **web application only**. It is **open source and self-hosted**.
An organisation runs the whole stack on infrastructure it controls, using one
Docker Compose file: Caddy for HTTPS, the API, a worker, Postgres and Valkey.

- V2 (the desktop app, local mode and user-configured backup) is dropped, not
  postponed.
- Render is dropped as the default target. Compose runs anywhere Docker does.
- Provider seams (`Protocol`s) stay, but for a different reason. They keep the
  application testable with fakes, and let an organisation choose its own LLM,
  storage and identity provider. They are not there for a future local runtime.

## Consequences

**Easier**

- One deployment shape to secure, document and test.
- Security can be designed for a server from the start: server-side sessions,
  per-project encryption keys and a tamper-evident audit log.
- The frontend can call the API directly. There is no need for an API client
  abstraction that hides whether the backend is remote or local.

**Harder**

- Sensitive data now always lives on a server. The privacy claim rests on:
  - masking before any LLM call;
  - encryption at rest with keys the operator holds;
  - strict access control;
  - the organisation self-hosting rather than trusting a third party.
- A user who wants nothing to leave their machine has to run the stack
  locally with Docker. This is supported, but it is not a native app.

**Removed**

- `BackupProvider` and `url_for`. Files are always streamed through an
  authorised API route, so every read is checked and audited.
- `apps/desktop`, `crates/`, `packages/ui`, `packages/api-client` and
  `packages/config`.
- `docs/system_overview.md` and the V1-to-V2 diagram.
- Render configuration.
