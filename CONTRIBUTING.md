# Contributing to humani-t

Thank you for helping. humani-t holds data about people in crises, so the bar for a change is that it is correct, tested, and keeps the two rules below. This guide gets you from a fresh machine to a merged pull request.

Before you start, read [`CLAUDE.md`](CLAUDE.md). Despite the name it is the project's working agreement for humans and coding agents alike: the layering rules, Python and TypeScript standards, comments, naming, errors and tests. [`docs/architecture.md`](docs/architecture.md) explains the design.

> **1.** Data leaves the deployment only to destinations the operator has configured, and only after it has been masked.
>
> **2.** A reviewer can reconstruct why a paper was included or excluded, six months from now, from what we stored.

Security problems are never reported in public issues: see [`SECURITY.md`](SECURITY.md). Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## 1. Install the tools

| Tool | Why | Check |
| --- | --- | --- |
| Git | | `git --version` |
| Docker with Compose v2 (Docker Desktop on Windows and macOS, Docker Engine on Linux) | Postgres, Valkey and Mailpit for development; the full stack | `docker compose version` |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Python, the API's virtual environment and its lockfile | `uv --version` |
| Python 3.13 | Only for the small scripts in `scripts/`; uv manages the API's own Python | `python --version` |
| [Node.js 24](https://nodejs.org/) | The web app | `node --version` |
| pnpm, through Corepack | The JavaScript workspace; Corepack installs the exact version in `package.json` | `corepack enable`, then `pnpm --version` |

Platform notes:

- **Windows.** Use PowerShell. Every command below works in Windows PowerShell 5.1; where a command differs from macOS and Linux, both are shown. Run `corepack enable` from an administrator PowerShell once. Docker Desktop must be running before any `docker` command. If Git converts line endings, the repository's `.gitattributes` already keeps them as LF; you do not need to change Git's settings.
- **macOS.** `brew install uv node` works; Docker Desktop or OrbStack both provide Compose.
- **Linux.** Install Docker Engine and the Compose plugin from Docker's own repository (distribution packages are often too old), and add yourself to the `docker` group or use rootless Docker.

## 2. Set up

From the repository root:

```powershell
python scripts/dev_secrets.py
docker compose -f compose.dev.yaml up -d
docker compose -f compose.dev.yaml ps
```

(On macOS and Linux the first line may need `python3`.) The script writes random development passwords into `secrets/` and a `services/api/.env` pointing the API at them; both are git-ignored and never overwritten on a re-run. Compose starts Postgres, Valkey and Mailpit, each listening on `127.0.0.1` only. All three should report `healthy`.

Then the API:

```powershell
cd services/api
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

Open http://127.0.0.1:8731/docs. Emails the API sends appear at http://127.0.0.1:58025.

And the web app, in a second terminal from the repository root:

```powershell
pnpm install
pnpm --filter @humani-t/web dev
```

[`docs/walkthroughs/00-setup.md`](docs/walkthroughs/00-setup.md) goes through all of this step by step, including the test database, the first administrator, Postman and troubleshooting.

Finally, install the pre-commit hooks once. They run ruff, pyright, import-linter, Biome, gitleaks and a lockfile check on every `git commit`, so problems show up in seconds rather than in CI:

```powershell
cd services/api
uv run pre-commit install
```

## 3. Work tests-first

In humani-t a test is the specification. The order is:

1. **Write the test that states the requirement**, named as a sentence: `test_a_reset_link_works_only_once`. Use the fakes in each seam (`FakePaperSearchProvider`, the in-memory storage), not mocks: a fake behaves like the real thing, so the test proves something.
2. **Run it and watch it fail** for the reason you expect. A test that passes before the code exists is testing nothing. If you open a draft pull request at this point, say that the failure is deliberate.
3. **Write the smallest code that makes it pass.**
4. **Run everything** (section 4), then tidy up.

Some rules are too important to leave as prose and are written as tests on purpose; [`docs/security/controls.md`](docs/security/controls.md) maps every security control to the test that proves it. If you change a control, its test must still fail when the control is removed.

## 4. Before you open a pull request

The API, from `services/api`:

```powershell
uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

The web app, from the repository root:

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm check:api
```

`pnpm check:api` fails if `services/api/openapi.json` or the generated TypeScript types are stale. After changing an endpoint, run `pnpm gen:api` and commit both files.

To include the Postgres and Valkey contract tests (they use the separate `humani_t_test` database and never touch your development data), follow [S0.6 and S0.8 in the setup walkthrough](docs/walkthroughs/00-setup.md#s06-migrate-the-test-database). CI always runs them.

The pull request template repeats the checklist from `CLAUDE.md`. CI runs the same commands, plus CodeQL, secret scanning, dependency and image scanning, a workflow linter and a smoke test of the full Compose stack ([`.github/workflows/`](.github/workflows/)).

## 5. Commits

Messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <description>`, for example `fix(auth): refuse a reused TOTP code`. The types and scopes are listed in [`docs/standards.md`](docs/standards.md).

- One idea per commit. `git add -p` lets you stage part of a file, which is how a session's work becomes several small commits.
- The body says **why**, not what: the diff already shows what.
- A commit that adds a dependency says why that one, and not an alternative.
- Never commit anything from `secrets/`, a `.env` file, or real data. gitleaks stops most accidents; it cannot stop all of them.

## 6. Pull requests

- Branch from `main`; one topic per branch.
- Keep pull requests small enough to review in one sitting. A large change is easier to review as a series.
- Every check must pass before review. If a check is wrong, fix the check in its own pull request rather than skipping it.
- Use fictional data in tests, examples, screenshots and issues. Never real names, questions or documents from a real review.

## 7. Licence

humani-t is licensed under the [Apache License 2.0](LICENSE). By contributing you agree that your contribution is licensed under the same terms (section 5 of the licence).
