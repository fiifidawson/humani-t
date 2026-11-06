# humani-t

Privacy-preserving evidence synthesis for humanitarian decisions.

A reviewer states a research question. humani-t searches academic repositories, screens the results against review criteria, and synthesises them with citations. It does this reproducibly, and without sending sensitive material anywhere it should not go.

humani-t is a **self-hosted web application**. An organisation runs it on infrastructure it controls, so questions, documents and results stay within that deployment. Personal data is masked before any text reaches a language model, and by default that model runs inside the deployment as well.

> **Status: early development.** The security core (accounts, MFA, projects, encryption at rest, a tamper-evident audit log) is in place and the review workflow is being built on top of it. **Do not use it with real data yet:** it has not had an independent security review.

## How it works

1. **Ask a question.** Refine the search plan with the model, or bring your own strict vocabulary.
2. **Discover** papers in OpenAlex, Europe PMC and arXiv.
3. **Mask** personal data in papers and your own documents, locally, with no LLM.
4. **Screen** each paper against the criteria. Every decision keeps its evidence and provenance.
5. **Review, export and ask.** Relabel, export a dataset and a PRISMA report, and ask follow-up questions answered with cited sources.

See [`docs/WEB_APP_BRIEF.md`](docs/WEB_APP_BRIEF.md) for the full workflow and the security rules the app keeps.

## Self-hosting quick start

You need a Linux server (or any machine with Docker, to try it) with Docker Engine and the Compose plugin, Python 3 for one setup script, and, for a public deployment, a domain name pointing at the server with ports 80 and 443 open.

```powershell
git clone https://github.com/fiifidawson/humani-t.git
cd humani-t
python infrastructure/generate_secrets.py
copy .env.example .env
```

(`python3` and `cp` on Linux and macOS.) Edit `.env`: set `HUMANI_T_DOMAIN`, `HUMANI_T_PUBLIC_ORIGIN` and your mail server. To try it on one machine, leave the domain as `localhost` and use the bundled Mailpit inbox and Ollama model server, as the comments in `.env.example` explain. Then:

```powershell
docker compose up -d --build
docker compose ps
```

The services should be `healthy` (`worker` shows `running`, and the one-shot `migrate` job `exited (0)`). Create the first administrator; the password is read from standard input only, and must be at least 15 characters. On Linux or macOS (`read -rs` prompts without echoing):

```bash
read -rs PASSWORD; printf '%s\n' "$PASSWORD" | docker compose exec -T api python -m app.cli create-admin --email admin@example.org --password-stdin; unset PASSWORD
```

In PowerShell, typing your own password in place of the example:

```powershell
"an-example-passphrase-of-15-or-more" | docker compose exec -T api python -m app.cli create-admin --email admin@example.org --password-stdin
```

Open `https://<your domain>/` and sign in.

Before anything real goes in, read [`docs/deploy/self-hosting.md`](docs/deploy/self-hosting.md): backing up the database **and** the key-encryption key (separately), what deleting a project does and does not erase from backups, upgrades, and the limits of the current release. To run the language model on a separate machine, see [`docs/deploy/remote-model.md`](docs/deploy/remote-model.md).

## Development

Requirements: Docker, Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js 24 with pnpm (through Corepack).

```powershell
python scripts/dev_secrets.py
docker compose -f compose.dev.yaml up -d
cd services/api
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

The API docs are at http://127.0.0.1:8731/docs. [`CONTRIBUTING.md`](CONTRIBUTING.md) covers setup on Windows, macOS and Linux, the tests-first workflow, the checks to run, and commit conventions.

## Documentation

| Document | What it covers |
| --- | --- |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Setting up, working tests-first, commits and pull requests |
| [`CLAUDE.md`](CLAUDE.md) | Code standards and the working agreement, for people and coding agents |
| [`docs/deploy/`](docs/deploy/) | Self-hosting, backups, key custody, upgrades; a model on another machine |
| [`docs/architecture.md`](docs/architecture.md) | Layers, provider seams, domain model, security model |
| [`docs/security/controls.md`](docs/security/controls.md) | Every security control, the test that proves it, and the known gaps |
| [`docs/api.md`](docs/api.md) | HTTP API reference |
| [`docs/UI_UX_SPEC.md`](docs/UI_UX_SPEC.md) | The web interface: layout, chat, decision brief, history, design system |
| [`docs/walkthroughs/`](docs/walkthroughs/) | A guided tour of each phase, with manual tests |
| [`docs/standards.md`](docs/standards.md) | Commit messages and git workflow |
| [`docs/adr/`](docs/adr/) | Architecture decision records |

## Security

Report vulnerabilities privately, as described in [`SECURITY.md`](SECURITY.md). Never in a public issue.

## Licence

[Apache License 2.0](LICENSE). Contributors follow the [Code of Conduct](CODE_OF_CONDUCT.md).
