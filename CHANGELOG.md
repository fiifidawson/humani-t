# Changelog

All notable changes to humani-t are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0, a minor version may change the API or the database in ways that need the upgrade notes in [`docs/deploy/self-hosting.md`](docs/deploy/self-hosting.md).

## [Unreleased]

### Changed

- **How this brief was made** shows the agents as a timeline: every round in order, each step a sentence saying what the agent did, what it found and how long it took, with its details on demand. It replaces the loop diagram and round picker.

## [0.1.0] - 2026-11-24

The first tagged release: the whole review workflow, from question to decision brief, on a self-hosted security core. It has not yet had an independent security review.

### Added

- **Review workflow:** clarifying questions, a search plan to check and accept, discovery in OpenAlex, Europe PMC and arXiv with duplicates merged, screening with reasons and evidence for every label, a PRISMA flow diagram, and a coverage estimate of how much relevant work the search may have missed.
- **Your own documents:** PDF, Word and text uploads, reviewed by an owner before any model reads them.
- **Decision brief:** a Planner, Retriever, Writer and Critic loop that sets out the options with every claim cited, records disagreements and gaps, and shows how each option sits against the humanitarian principles; follow-up questions answered from the project's sources only.
- **Privacy:** personal data masked locally before text reaches a model the deployment does not run itself; data leaves only for destinations the operator configures.
- **Security core:** invite-only accounts with passkeys or an authenticator app required, recovery codes, sessions that end after 8 hours idle or 24 hours, project roles, envelope encryption with a key per project (deleting a project makes it unreadable), and a tamper-evident audit log.
- **Operator commands:** `generate-kek`, `rewrap-keys`, `verify-audit`, `purge-expired`, `create-admin` and `reset-mfa`.
- **Web app:** LiGHT's brand, light and dark themes, low data mode, an offline copy of the brief, and automated accessibility checks.
- **Deployment:** Docker Compose with Caddy, Postgres and Valkey, and a guide to self-hosting, backups and key custody.

[Unreleased]: https://github.com/fiifidawson/humani-t/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fiifidawson/humani-t/releases/tag/v0.1.0
