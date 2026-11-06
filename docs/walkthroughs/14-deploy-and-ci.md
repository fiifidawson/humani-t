# 14 — Deploy and CI

> **Milestones:** M3, M4, and the deployment guide from M14 · **Status:** in progress · **Previous:** `13-web-ui` (planned) · **Next:** none yet

Until this phase humani-t ran only on a developer's machine: the API under `uv run`, with Postgres, Valkey and Mailpit in containers beside it. At the end of it, an organisation can run the whole system on a server it controls with one command, behind HTTPS with automatic certificates, with every container locked down; and every pull request is checked by CI — tests against a real Postgres and Valkey, type checks, the layering rules, the web app's checks, secret scanning, dependency and image scanning, static analysis, a linter for the workflows themselves, and a smoke test that starts the full stack and checks it from the outside. The project also gains the files an open-source contributor looks for first. It implements [`architecture.md` §2](../architecture.md#2-deployment) and the deployment half of [§5.3](../architecture.md#53-request-integrity) and [§5.4](../architecture.md#54-encryption), and brief rules 15 and 17 (encryption in transit and at rest, with keys the operator holds).

## What this phase delivers

- **A self-hosted stack** in [`compose.yaml`](../../compose.yaml): Caddy, the API, the worker, a one-shot `migrate` job, Postgres and Valkey, plus optional Ollama (`local-model`) and Mailpit (`local-mail`) profiles.
- **Two images built from this repository:** the API (also the worker and the migrate job) and the web image (Caddy with the built single-page app).
- **A Caddy configuration** that serves the app and the API on one HTTPS origin, with a strict Content Security Policy, Trusted Types, HSTS, COOP and CORP, `no-referrer`, request size limits, unbuffered Server-Sent Events, and no `Server` header.
- **Development services** moved to [`compose.dev.yaml`](../../compose.dev.yaml), with the same container and volume names, ports and data as before, now hardened like production.
- **An operator command** that creates every secret the stack needs: `python infrastructure/generate_secrets.py`.
- **Five workflows** in [`.github/workflows/`](../../.github/workflows/): `api`, `web`, `security`, `codeql`, `compose`; and Dependabot for uv, npm, GitHub Actions, Dockerfiles and Compose.
- **Two deployment guides:** [self-hosting](../deploy/self-hosting.md) and [running the model on another machine](../deploy/remote-model.md).
- **Open-source files:** [`README.md`](../../README.md) with a self-hosting quick start, [`CONTRIBUTING.md`](../../CONTRIBUTING.md), [`SECURITY.md`](../../SECURITY.md), [`CODE_OF_CONDUCT.md`](../../CODE_OF_CONDUCT.md), issue forms and a pull request template.
- No endpoints, tables or migrations. One settings-adjacent change: [`01-roles.sh`](../../infrastructure/postgres/init/01-roles.sh) creates `humani_t_test` only when `CREATE_TEST_DATABASE=true` (development and CI), not in production.

## Architecture choices and why

### D14.1 One API image, three roles

- **Decision:** [`services/api/Dockerfile`](../../services/api/Dockerfile) builds one image. Compose runs it as `api` (the default command, uvicorn), `worker` (`python -m app.worker`) and `migrate` (`alembic upgrade head`).
- **Why:** the three must run exactly the same code against the same schema. One image makes a mismatch impossible, and there is one thing to scan and update.
- **Rejected:** three images (three builds that can drift); running migrations in the API's start-up (every API process would race to migrate, and the API would need the schema owner's credentials).
- **Costs:** the worker and migrate job carry uvicorn, which they do not use.
- **Where:** `services/api/Dockerfile`, `compose.yaml` (`x-api-image`).
- **Proven by:** `compose.yml` › *Start the stack* (all three start from it).

### D14.2 A multi-stage uv build on a pinned slim Python

- **Decision:** a `build` stage installs dependencies with `uv sync --locked --no-dev --no-install-project`, then copies the code and compiles bytecode. The `runtime` stage is a fresh `python:3.13.18-slim-trixie`, pinned by digest, with the virtual environment copied in, `pip` uninstalled, and a numeric non-root user (`10001`). `HUMANI_T_ENVIRONMENT=production` is the image default.
- **Why:** `--locked` fails the build if `uv.lock` disagrees with `pyproject.toml`, so the image contains exactly what CI tested. The runtime stage has no uv, no pip and no compiler; every wheel the API uses ships pre-built for Python 3.13, so none is needed. A digest, unlike a tag, cannot be moved to different contents. Defaulting to production means a misconfigured container fails closed (it refuses to start without its secrets) instead of quietly running with in-memory storage.
- **Rejected:** a single stage (uv and its cache in the final image); Alpine (musl breaks or slows several wheels); distroless (no Python 3.13 image from Google, and no shell for the health check or for `docker compose exec`).
- **Costs:** about 360 MB. Bytecode is compiled at build time because the root filesystem is read-only at run time.
- **Where:** `services/api/Dockerfile`, `services/api/.dockerignore`.
- **Proven by:** T14.2; `security.yml` › trivy (api image).

### D14.3 Build contexts are allowlists

- **Decision:** both `.dockerignore` files ([`services/api/.dockerignore`](../../services/api/.dockerignore), [root `.dockerignore`](../../.dockerignore)) start with `*` and let back in only what each image needs.
- **Why:** a denylist misses the file nobody thought of. The root context contains `secrets/`, `.env` files and `node_modules`; with an allowlist none of them can reach a build, even by accident.
- **Rejected:** a denylist of known-bad paths.
- **Costs:** a new file the build needs must be added to the list, or the build fails (loudly, which is the point).
- **Where:** the two files above.
- **Proven by:** T14.2 (`docker run --rm humani-t-api:local ls -a /app`).

### D14.4 Caddy compiled from source, with current Go and patched dependencies

- **Decision:** [`apps/web/Dockerfile`](../../apps/web/Dockerfile) compiles Caddy `v2.11.7` with `golang:1.26.10-alpine` from [`infrastructure/caddy/main.go`](../../infrastructure/caddy/main.go) (Caddy's own entry point), raising two dependencies above what Caddy's `go.mod` asks for (`DEPENDENCY_FIXES`: `golang.org/x/net` v0.60.0, `go-jose` v4.1.6). The binary replaces the one in the official `caddy:2.11.7-alpine` image, which still provides the CA certificates and layout; `curl` is removed from that image.
- **Why:** Caddy faces the internet. When this phase was built, Trivy found in the official image: four HIGH vulnerabilities in Go 1.26.8's TLS, HTTP/2 and X.509 code (fixed in Go 1.26.10), two HIGH in Caddy's dependencies (fixed in the versions above), and one CRITICAL and two HIGH in `curl` and `brotli`, which nothing here uses. Compiling Caddy ourselves means each fix reaches us when it ships, not when the official image is next rebuilt. Go checks every downloaded module against its public checksum database, so the build cannot silently take a tampered dependency.
- **Rejected:** waiting for the official image (days of known, fixable vulnerabilities on the public edge); suppressing the findings in Trivy; `apk upgrade` in the image (makes the build depend on the day it runs).
- **Costs:** a slower first build (several minutes on Docker Desktop). `CADDY_VERSION` and `DEPENDENCY_FIXES` are build arguments Dependabot does not update: `CADDY_VERSION` must follow the runtime image's tag, and each fix is removed once Caddy's own `go.mod` includes it (comments beside them say so).
- **Where:** `apps/web/Dockerfile`, `infrastructure/caddy/main.go`.
- **Proven by:** `security.yml` › trivy (web image); T14.2 (`caddy version`, `caddy build-info`).

### D14.5 One origin; the API's headers win, the app gets its own strict policy

- **Decision:** [`infrastructure/caddy/Caddyfile`](../../infrastructure/caddy/Caddyfile) serves the app from `/srv` and proxies `/api/*` to `api:8000`. Common headers are set with `?` (only if absent) and `defer`, so the API's own stricter values (`default-src 'none'`, `no-store`) pass through unchanged, and the static files get HSTS, nosniff, `no-referrer`, COOP, CORP, `X-Frame-Options` and a Permissions-Policy. The app's CSP is `default-src 'none'` with `script-src 'self'`, `style-src 'self'`, no `unsafe-inline`, `require-trusted-types-for 'script'; trusted-types 'none'`, and `frame-ancestors 'none'`. `Server` and `Via` are removed. Static responses are compressed; API responses are not.
- **Why:** one origin means no CORS to misconfigure and cookies that stay `SameSite`. No `unsafe-inline` means injected markup cannot run script; Trusted Types means code that does run cannot turn a string into markup through `innerHTML` and similar sinks. Vite emits only external, hashed files, and React 19 never assigns to those sinks unless `dangerouslySetInnerHTML` is used, so the app needs nothing looser. Not compressing API responses avoids BREACH, which recovers secrets from compressed responses that also reflect attacker-chosen text (recovery codes and TOTP set-up are such responses).
- **Rejected:** setting all headers in Caddy (it would duplicate or overwrite the API's); a nonce-based CSP (needs server-side rendering of `index.html`); a separate API host name (CORS, and cookies across sites).
- **Costs:** any future inline script or style, or a library that writes `innerHTML`, is blocked by the browser; the fix is to change that code, not the policy. The policy is checked as headers in CI, not yet in a browser running the app (G14.2).
- **Where:** `infrastructure/caddy/Caddyfile`; the API's headers in `services/api/app/api/middleware.py`.
- **Proven by:** `compose.yml` › *Security headers* (`.github/scripts/check-security-headers.sh`); T14.5, T14.6.

### D14.6 Limits at the edge, and Server-Sent Events unbuffered

- **Decision:** request bodies to `/api/*` are capped at 1 MiB in Caddy (the API's own limit too); static routes accept no body. `GET /api/v1/runs/{run_id}/events` is proxied with `flush_interval -1`.
- **Why:** an oversized body is refused before it occupies an API worker. Server-Sent Events must reach the browser as each event is written; a buffering proxy holds them until the buffer fills, and the UI appears frozen.
- **Rejected:** relying on the API's limit alone.
- **Costs:** the events route is matched by a regular expression; a renamed route needs the Caddyfile changed too. It is not yet tested end to end (G14.1).
- **Where:** `infrastructure/caddy/Caddyfile`.
- **Proven by:** `compose.yml` › *Oversized request bodies are refused at the edge*; T14.9.

### D14.7 Two Compose files with different project names

- **Decision:** `compose.yaml` is the self-hosted stack, project `humani-t-stack`. `compose.dev.yaml` holds the development services, project `humani-t`, with the same service names, volume (`humani-t_postgres-data`), and `127.0.0.1` ports (55432, 56379, 51025, 58025) as before.
- **Why:** an operator expects `docker compose up` to start the product. Developers' existing data, `.env` and Postgres connections keep working because nothing they depend on was renamed. Different project names mean the two stacks can never share a container or a volume, even on one machine.
- **Rejected:** one file with profiles (development's published database ports would sit one flag away from production); an override file merged on top (`compose.override.yaml` is applied automatically, which is exactly the surprise to avoid in production).
- **Costs:** development commands need `-f compose.dev.yaml`. [`00-setup.md`](00-setup.md), walkthroughs 02 and 05, and `CLAUDE.md` were updated. A forgotten `-f` starts nothing harmful: `compose.yaml` refuses to start without `HUMANI_T_SMTP_HOST`.
- **Where:** `compose.yaml`, `compose.dev.yaml`.
- **Proven by:** T14.1; `compose.yml` › *Development images match the self-hosted stack*.

### D14.8 Every container locked down the same way

- **Decision:** every long-running service merges `x-hardened`: `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges:true`, restart unless stopped, and log rotation (10 MB × 5). Each also has a non-root `user`, a tmpfs for whatever it must write, CPU, memory and process limits, and a health check. The worker's is `python -m app.worker --check-health`, which passes only while the heartbeat file the worker touches at least every 5 seconds (in `/tmp`, its tmpfs) is under 15 seconds old.
- **Why:** these limit what an attacker can do with code execution in any one container: no writing to the image, no privileged kernel operations, no gaining privileges through setuid binaries, no exhausting the host. Postgres and Valkey run as their images' own users from the start (`70`, `999`), so their entry points never need the capabilities to switch user.
- **Rejected:** hardening only the API (the database is the more valuable target); user namespaces (`userns-remap` is a daemon-wide setting that Compose cannot set and many hosts do not enable).
- **Costs:** a new service must declare where it writes. The Ollama volume needs a one-shot `ollama-volume` job (capability `CHOWN` only, no network) to hand its top directory to the non-root user.
- **Where:** `compose.yaml`, `compose.dev.yaml`.
- **Proven by:** `compose.yml` › *Every container runs as a non-root user*, › *Every container is hardened and publishes only what it should*, › *Start the stack* (`--wait` needs every health check to pass, the worker's included); T14.3, T14.11.

### D14.9 Four networks, and only Caddy publishes

- **Decision:** `public` (Caddy's ports and its route to Let's Encrypt), `backend` (internal: Caddy and the API only), `data` (internal: the API, worker and migrate job to Postgres and Valkey), and `egress` (the API and worker out to paper sources, mail and the model; Ollama and Mailpit sit here too).
- **Why:** Postgres and Valkey have no route to the internet and no published port, so a compromised database cannot call out, and nothing outside can call in. The worker is not on `backend`, because nothing should call it. Docker-published ports bypass host firewalls such as `ufw`, so the only defence for an unpublished service is not publishing it.
- **Rejected:** one flat network (every container reaches every other, and the internet).
- **Costs:** Ollama on `egress` can reach the internet, which it needs to download models.
- **Where:** `compose.yaml` (`networks:`).
- **Proven by:** `compose.yml` › *Every container is hardened and publishes only what it should*; T14.12, T14.13.

### D14.10 Secrets are files named after the settings they fill

- **Decision:** every credential is a file in `HUMANI_T_SECRETS_DIR` (default `./secrets`), mounted by Compose at `/run/secrets/humani_t_<field>`, which `app/settings.py` reads as that field. Connection URLs are secrets too, because they contain passwords. [`infrastructure/generate_secrets.py`](../../infrastructure/generate_secrets.py) creates them all, write-once, building the URLs from the passwords, in a `0700` directory with `0644` files. Empty optional files (`smtp_password`, `inference_token`, `openalex_api_key`) mean "not configured".
- **Why:** environment variables leak into `docker inspect`, crash reports and child processes; files under `/run/secrets` do not. Each container lists only the secrets it needs. Generating URLs from the same passwords means they cannot disagree. Write-once makes re-running safe and reuses development passwords if they exist.
- **Rejected:** `environment:` entries; a `.env` with passwords; splitting URLs into host, user and password settings (an application change; the URLs work today).
- **Costs:** a password is stored twice (the password file for Postgres, the URL for the API); rotating it means editing both, and changing the role's password in Postgres.
- **Where:** `compose.yaml` (`secrets:`), `infrastructure/generate_secrets.py`, `services/api/app/settings.py`.
- **Proven by:** `compose.yml` › *Each container holds only its own secrets*; T14.14, T14.15.

### D14.11 The schema owner's credential lives only in the migrate job

- **Decision:** `migrate` receives only `humani_t_migration_database_url`. It runs before `api` and `worker`, which start only if it exits 0 (`service_completed_successfully`). It sets `HUMANI_T_ENVIRONMENT=development`.
- **Why:** the running application cannot alter or drop tables, because it never holds the role that can ([D2.6](02-configuration-and-persistence.md#d26-two-database-roles-migrations-and-a-separate-test-database)). The environment override exists because `migrations/env.py` validates the whole `Settings`, and in production that demands the API's secrets, which this job must not hold.
- **Rejected:** migrating from the API on start-up; giving `migrate` the API's secrets to satisfy validation.
- **Costs:** the override is a workaround in a security-relevant place (G14.4).
- **Where:** `compose.yaml` (`migrate`).
- **Proven by:** `compose.yml` › *Each container holds only its own secrets*; T14.14, T14.17.

### D14.12 Valkey's password never appears in a process list

- **Decision:** Valkey reads its configuration from standard input (`valkey-server -`), fed by a here-document that reads the secret file; the command is `exec`'d so Valkey is the process that receives signals. The health check passes the password in `REDISCLI_AUTH`, not `-a`. `maxmemory 192mb` with `noeviction`.
- **Why:** command-line arguments are visible to every user of the Docker host through `ps`; the previous development command put the password there. `noeviction` makes a full Valkey refuse writes, rather than silently drop the throttling counters and hand an attacker fresh attempts.
- **Rejected:** `--requirepass` on the command line; a config file in the image (the password would be in the image).
- **Costs:** a slightly unusual start command, explained beside it.
- **Where:** `compose.yaml`, `compose.dev.yaml` (`valkey`).
- **Proven by:** T14.16.

### D14.13 Client addresses: trust the proxy, and only the proxy

- **Decision:** the API decides the client address itself. `HUMANI_T_TRUSTED_PROXIES` is `172.30.231.2`, Caddy's fixed address on `backend` (subnet `172.30.231.0/28`), so `ClientAddressMiddleware` believes `X-Forwarded-For` from Caddy alone ([05](05-accounts-and-sessions.md) D5.6). Uvicorn runs with `--no-proxy-headers` (the image's `CMD`), so it passes the connecting peer through unchanged. Caddy replaces any `X-Forwarded-For` a client sends with the real peer address.
- **Why:** per-IP throttling needs the client's address, not Caddy's, and only Caddy may name it. One setting, enforced and tested in the app, is easier to get right than two trust lists that must agree; uvicorn's own handling would rewrite the address before the API saw the peer, so it is turned off.
- **Rejected:** `--proxy-headers` with `FORWARDED_ALLOW_IPS="*"` (any service later added to `backend` could forge client addresses and dodge throttling); `FORWARDED_ALLOW_IPS` set to Caddy's address alongside the app setting (two lists to keep in step, and uvicorn would decide first).
- **Costs:** the subnet is fixed, so it can collide with one of the operator's networks; change it, Caddy's `ipv4_address` and `HUMANI_T_TRUSTED_PROXIES` together. On Docker Desktop all traffic arrives from Docker's gateway, so per-IP limits apply to everyone together (G14.5).
- **Where:** `compose.yaml` (`api.environment`, `caddy.networks`, `networks.backend`), `services/api/Dockerfile` (`CMD`), `services/api/app/api/middleware.py` (`ClientAddressMiddleware`).
- **Proven by:** T14.10 (a forged `X-Forwarded-For` is ignored); `tests/test_auth_api.py::TestClientAddress`.

### D14.14 The model stays with the operator

- **Decision:** the default `HUMANI_T_INFERENCE_URL` is `http://ollama:11434`, the optional in-stack Ollama. A model on another machine is reached through a private tunnel or through Caddy with TLS, a bearer token (`inference_token` secret) and an IP allowlist ([remote-model.md](../deploy/remote-model.md)). `HUMANI_T_ALLOW_HOSTED_INFERENCE` is deliberately not passed through by `compose.yaml`.
- **Why:** CLAUDE.md: "Default any model call to a hosted API" is forbidden, and data leaves only to destinations the operator chose. Ollama has no authentication, so it is never published. Sending text to a third-party model API should take an edit to `compose.yaml`, not one stray line in `.env`.
- **Rejected:** a hosted model by default; Ollama published on a port "for convenience".
- **Costs:** the bundled model needs a large server (about 8 GB of RAM for the default `llama3.1:8b`).
- **Where:** `compose.yaml` (`ollama`, `x-app-environment`), `docs/deploy/remote-model.md`; the API's own checks in `services/api/app/settings.py` (`_inference_stays_with_the_operator`).
- **Proven by:** the remote guide's section 5 tests (manual); the settings checks have their own unit tests.

### D14.15 Workflows: pinned, least-privilege, cancellable

- **Decision:** every action is pinned to a full commit SHA with its version in a comment; images in `docker://` steps are pinned by digest. Each workflow starts with `permissions: {}` and each job asks for what it needs (`contents: read`; CodeQL also `security-events: write`). Checkouts use `persist-credentials: false`. A concurrency group per workflow and pull request cancels superseded runs. Triggers are `pull_request` and `push` to `main`, never `pull_request_target`.
- **Why:** a tag can be moved to malicious code by whoever controls the action's repository; in March 2026 that happened to `aquasecurity/trivy-action` (its re-published `v0.35.0` release notes describe it), as it did to `tj-actions/changed-files` in 2025. A SHA cannot be moved. Minimal permissions and no persisted token limit what a compromised step can do with the repository.
- **Rejected:** version tags (`@v7`).
- **Costs:** updates arrive as Dependabot pull requests rather than automatically.
- **Where:** `.github/workflows/*.yml`.
- **Proven by:** `security.yml` › zizmor (fails on unpinned actions, excessive permissions, template injection, persisted credentials) and actionlint.

### D14.16 CI tests against the development stack, not `services:` containers

- **Decision:** the `api` workflow's test job runs `scripts/dev_secrets.py` and `docker compose -f compose.dev.yaml up --wait postgres valkey` under project `humani-t-ci`, migrates `humani_t_test` with Alembic as the schema owner, then runs `pytest --cov` with `TEST_DATABASE_URL` and `TEST_VALKEY_URL` set.
- **Why:** this is exactly how a contributor sets up, so CI proves the setup instructions, the init script (roles, grants, `humani_t_test`) and the hardened development containers on every change. GitHub's `services:` containers start before the checkout, so they cannot run the repository's init script or mount its secrets; CI would need a second, drifting copy of the role setup.
- **Rejected:** `services:` with hand-written SQL in the workflow.
- **Costs:** the job pulls three images and takes a little longer to start.
- **Where:** `.github/workflows/api.yml`.
- **Proven by:** the `api` workflow itself; the contract tests skip without `TEST_DATABASE_URL`, and `-rs` prints any skip reason in the log.

### D14.17 One scanner per question

- **Decision:** gitleaks (the whole history) for secrets; osv-scanner for `uv.lock` and `pnpm-lock.yaml`; Trivy for HIGH and CRITICAL fixable vulnerabilities in both images, and for Dockerfile misconfigurations; CodeQL (`security-extended`) for Python, TypeScript and the workflows; zizmor and actionlint for the workflows. All also run weekly.
- **Why:** each answers a different question, and each fails the build. gitleaks runs from its image because `gitleaks-action`'s licence requires a paid key for organisation-owned repositories. osv-scanner covers both ecosystems from the OSV database, which aggregates the PyPI and GitHub advisories `pip-audit` would use. CodeQL follows untrusted input across functions, which ruff's per-line bandit rules cannot.
- **Rejected:** Semgrep (with CodeQL, ruff's `S` rules and zizmor in place, its community rules mostly duplicate them, and its registry rules are fetched at run time); `pip-audit` plus `pnpm audit` (two tools for what one does).
- **Costs:** a new advisory against a dependency turns CI red until the dependency is updated, even with no code change. That is intended; the weekly schedule finds it before a pull request does.
- **Where:** `.github/workflows/security.yml`, `.github/workflows/codeql.yml`.
- **Proven by:** the workflows; T14.22.

### D14.18 The smoke test checks the stack from outside

- **Decision:** the `compose` workflow generates throwaway secrets, builds both images, starts `caddy`, `worker` and `mailpit` (and with them `api`, `migrate`, `postgres`, `valkey`) under project `humani-t-smoke` on ports 58080 and 58443, then checks: health and readiness through Caddy over HTTPS, the HTTP redirect, every security header, the 1 MiB edge limit, non-root users, read-only and capability-free containers, published ports, secret isolation, digest pinning, and that development uses the same images. It always removes the stack.
- **Why:** unit tests cannot see a Caddyfile, a Compose file or a Dockerfile. This is the only test that fails if one of them stops doing its job.
- **Rejected:** checking the files with linters only (they cannot tell whether the headers actually arrive).
- **Costs:** about five minutes per run; Ollama is left out (several gigabytes, and nothing calls a model yet).
- **Where:** `.github/workflows/compose.yml`, `.github/scripts/check-security-headers.sh`.
- **Proven by:** the workflow; the same checks by hand in T14.4 to T14.17.

### D14.19 Dependabot: grouped, delayed, and kept off major database upgrades

- **Decision:** weekly updates for uv, npm (pnpm), GitHub Actions, the two Dockerfiles and Compose; minor and patch updates grouped per ecosystem; a 7-day cooldown (30 for major versions); Postgres and Valkey majors, Python minors and Node majors ignored.
- **Why:** grouping makes a week of updates one review. The cooldown keeps a freshly hijacked release out until it has had time to be noticed and pulled; security updates ignore it. A Postgres major version cannot open the previous one's data directory, so it needs the dump-and-restore in the self-hosting guide, not a merge button.
- **Rejected:** Renovate (removed from the project in M1); daily updates (noise).
- **Costs:** updates arrive up to a week after release. `compose.dev.yaml` may not be scanned by Dependabot; the smoke test's image-match check catches it falling behind.
- **Where:** `.github/dependabot.yml`.
- **Proven by:** Insights › Dependency graph › Dependabot, once pushed (T14.23).

### D14.20 Open-source files written for contributors and for reporters

- **Decision:** `CONTRIBUTING.md` covers setup on Windows, macOS and Linux, the tests-first order, the checks, and Conventional Commits. `SECURITY.md` routes reports to GitHub private vulnerability reporting, with response targets and scope. `CODE_OF_CONDUCT.md` is the Contributor Covenant 2.1, fetched from its source repository (`EthicalSource/contributor_covenant`, `content/version/2/1/code_of_conduct.md`) with only the front matter removed. Issue forms ask for fictional data only and for the privacy impact of a feature; blank issues are off. The pull request template is the `CLAUDE.md` "Before saying it is done" list.
- **Why:** the first thing a would-be contributor or a security researcher reads decides whether they come back. Asking for fictional data in every form protects the people this project is for.
- **Rejected:** retyping the Code of Conduct (it must be the exact text to be the Contributor Covenant).
- **Costs:** the Code of Conduct still contains `[INSERT CONTACT METHOD]` until the maintainer chooses a contact (G14.6).
- **Where:** the files above, and `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`.
- **Proven by:** reading them; T14.23 for the GitHub settings they rely on.

### Deliberately not used

| What | Why not |
| --- | --- |
| Kubernetes, Helm | One server is the target ([ADR-0001](../adr/0001-web-only-self-hosted.md)); Compose is what an organisation can operate. |
| nginx, Traefik | Caddy obtains and renews certificates itself, and its configuration is a page, not a project. |
| Published images on a registry | Not yet: they would need signing and provenance (cosign, SLSA) to be trustworthy. Operators build from source. |
| Semgrep | D14.17. |
| `gitleaks-action` | D14.17 (licence). |
| GitHub `services:` containers | D14.16. |

## Libraries

These are tools and images rather than Python or npm packages. Versions are as pinned in this phase.

### Docker Compose (v2.24 or newer)

- **What it does:** describes the containers, networks, volumes and secrets, and starts them in dependency order.
- **Why this one:** the deployment target in ADR-0001; every Docker installation has it.
- **Alternatives:** Podman Compose (close, but `service_completed_successfully` and secrets behave differently); Kubernetes.
- **Trade-offs:** file-based secrets are bind mounts, so the host's file permissions decide who in the container can read them (hence `0644` files in a `0700` directory).
- **What we gave up:** multi-host scheduling and rolling updates.

### Caddy 2.11.7 (built with Go 1.26.10)

- **What it does:** HTTPS with automatic certificates (Let's Encrypt, or its own authority for `localhost`), static files, reverse proxy, headers.
- **Why this one:** automatic HTTPS with secure defaults (TLS 1.2+, HTTP/2 and HTTP/3, OCSP stapling) and a short configuration.
- **Alternatives:** nginx with certbot; Traefik.
- **Trade-offs:** we compile it (D14.4). The admin API is turned off (`admin off`), so configuration changes need a restart.
- **What we gave up:** live reconfiguration.

### python:3.13.18-slim-trixie, ghcr.io/astral-sh/uv:0.13.6

- **What they do:** the API's base image, and the uv binary copied into the build stage.
- **Why these:** the Python version CLAUDE.md targets, on current Debian; uv is what the project already uses.
- **Trade-offs:** CI and the image use uv 0.13.6; contributors may run an older uv, which still reads the same `uv.lock`.

### node:24.22.2-alpine, pnpm 12.3.4 (through Corepack)

- **What they do:** build the web app in the image's first stages.
- **Why these:** Node 24 is the active LTS line; Corepack installs the exact pnpm named in `package.json`.
- **Trade-offs:** the build stage is discarded; only `dist/` reaches the runtime image.

### postgres:18.6-alpine, valkey/valkey:8.1.11-alpine, axllent/mailpit:v1.27.11, ollama/ollama:0.40.13

- **What they do:** the database, the rate-limit and queue store, the development mail catcher, the optional model server.
- **Why these:** the versions development already used, moved to exact tags and digests.

### GitHub Actions and scanners

| Tool | Pinned | Used for |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 `3d3c42e5…` | every job |
| `astral-sh/setup-uv` | v10.3.3 `68559e99…` | api jobs, `check:api` |
| `pnpm/action-setup` | v6.1.0 `ea17c68d…` | web jobs |
| `actions/setup-node` | v7.1.0 `949feb24…` | web jobs |
| `github/codeql-action` | v4.39.1 `9b325408…` | CodeQL |
| `aquasecurity/trivy-action` | v0.36.0 `ed142fd0…`, Trivy v0.76.1 | image and Dockerfile scanning |
| `zizmorcore/zizmor-action` | v0.8.0 `53290bad…`, zizmor 1.31.1 | workflow security |
| `ghcr.io/gitleaks/gitleaks` | v8.30.1, by digest | secrets in history |
| `ghcr.io/google/osv-scanner` | v2.7.0, by digest | vulnerable dependencies |
| `rhysd/actionlint` | 1.7.12, by digest | workflow correctness, shellcheck |

## Files to read, in order

From the outside in: the path a request takes from the internet, then what builds and checks it.

1. [`compose.yaml`](../../compose.yaml) — the shape of the deployment: `x-hardened`, the four networks, which secrets each service gets, `depends_on` with `service_completed_successfully`.
2. [`infrastructure/caddy/Caddyfile`](../../infrastructure/caddy/Caddyfile) — `?` and `defer` on the shared headers, the SPA's CSP, the events route, why `/api` is not compressed.
3. [`apps/web/Dockerfile`](../../apps/web/Dockerfile) and [`infrastructure/caddy/main.go`](../../infrastructure/caddy/main.go) — the pnpm build, the Caddy build with its dependency fixes, the non-root runtime.
4. [`services/api/Dockerfile`](../../services/api/Dockerfile) — the two stages, `--locked`, what is removed, `USER 10001:10001`.
5. [`infrastructure/generate_secrets.py`](../../infrastructure/generate_secrets.py) and [`infrastructure/postgres/init/01-roles.sh`](../../infrastructure/postgres/init/01-roles.sh) — what the stack needs before its first start.
6. [`compose.dev.yaml`](../../compose.dev.yaml) — the same hardening, with loopback ports and the fixed names.
7. [`.github/workflows/compose.yml`](../../.github/workflows/compose.yml) and [`.github/scripts/check-security-headers.sh`](../../.github/scripts/check-security-headers.sh) — every claim above, as a check.
8. [`.github/workflows/api.yml`](../../.github/workflows/api.yml), [`web.yml`](../../.github/workflows/web.yml), [`security.yml`](../../.github/workflows/security.yml), [`codeql.yml`](../../.github/workflows/codeql.yml) — `permissions: {}`, the SHAs, `persist-credentials: false`.
9. [`.github/dependabot.yml`](../../.github/dependabot.yml) — groups, cooldown, ignores.
10. [`docs/deploy/self-hosting.md`](../deploy/self-hosting.md) — sections 4 to 6: key custody, backups, and what crypto-shredding cannot reach.

## Set up

Everything here runs alongside the development stack without touching it: a different project name (`humani-t-stack`), its own volumes, its own secrets directory, and ports 8080 and 8443. Docker Desktop must be running. From the repository root:

```powershell
python infrastructure\generate_secrets.py --dir secrets\stack
copy .env.example .env
notepad .env
```

`secrets\stack` is inside the git-ignored `secrets\` folder, apart from the development files. In `.env`, set these lines (leave the others as they are):

```dotenv
HUMANI_T_PUBLIC_ORIGIN=https://localhost:8443
HUMANI_T_HTTP_PORT=8080
HUMANI_T_HTTPS_PORT=8443
HUMANI_T_SMTP_HOST=mailpit
HUMANI_T_SMTP_PORT=1025
HUMANI_T_SMTP_STARTTLS=false
COMPOSE_PROFILES=local-mail
HUMANI_T_SECRETS_DIR=./secrets/stack
```

Then build and start (the first build takes several minutes, most of it compiling Caddy):

```powershell
docker compose up -d --build
docker compose ps
```

Compose reads `.env` only for `compose.yaml`'s variables; it changes nothing for `compose.dev.yaml`, and the API's own `services\api\.env` is a different file.

`curl.exe` below is Windows' real curl; plain `curl` in PowerShell 5.1 is an alias for `Invoke-WebRequest`. `-k` accepts Caddy's local certificate authority, which Windows does not trust.

## Manual tests

#### Build and start

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T14.1 | `docker compose config --quiet; docker compose -f compose.dev.yaml config --quiet; docker compose -f compose.dev.yaml config \| Select-String 'humani-t_postgres-data'` | no errors; the volume name `humani-t_postgres-data` | Both files are valid; development keeps its volume (D14.7) | The message names the line |
| T14.2 | `docker run --rm humani-t-api:local sh -c "id; ls -a /app; python -m pip --version"` then `docker run --rm humani-t-web:local sh -c "id; caddy version; caddy build-info; which curl"` | `uid=10001(humani)`; only `.venv alembic.ini app migrations pyproject.toml uv.lock` (plus `.` and `..`); `No module named pip`. Web: `uid=10001`, `v2.11.7`; `build-info` shows `go1.26.10`, `golang.org/x/net v0.60.0` and `go-jose/v4 v4.1.6`; no `curl` | Non-root images, allowlisted context, no pip; Caddy built with the patched Go (D14.2–D14.4) | A file from the host in `/app` means `.dockerignore` lost its first line |
| T14.3 | `docker compose ps -a` | `caddy`, `api`, `worker`, `postgres`, `valkey`, `mailpit` `healthy`; `migrate` `exited (0)` | Start-up order and health checks (D14.1, D14.8) | `docker compose logs <service>`; see the self-hosting guide's troubleshooting table |

#### From the outside

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T14.4 | `curl.exe -sk https://localhost:8443/api/v1/health` and `.../api/v1/ready` | `{"status":"ok"}`, `{"status":"ready"}` | The API answers through Caddy on one origin (D14.5) | `docker compose logs caddy api` |
| T14.5 | `curl.exe -skI https://localhost:8443/` | `content-security-policy` with `script-src 'self'`, `require-trusted-types-for 'script'` and `frame-ancestors 'none'`; `strict-transport-security: max-age=63072000; includeSubDomains`; `cache-control: no-cache`; `referrer-policy: no-referrer`; COOP and CORP `same-origin`; **no** `server` line | The app's headers (D14.5) | A missing header points at the Caddyfile's `header` blocks |
| T14.6 | `curl.exe -skI https://localhost:8443/api/v1/health` | `content-security-policy: default-src 'none'; …`, `cache-control: no-store`, an `x-request-id`, and still no `server` | The API's stricter headers pass through unchanged (D14.5) | If the CSP is the app's, the `?` was lost from a `header` line |
| T14.7 | `curl.exe -s -o NUL -w "%{http_code} %{redirect_url}" http://localhost:8080/` | `308 https://localhost/` | HTTP is redirected to HTTPS (the URL drops the port because Caddy assumes 443) | |
| T14.8 | `curl.exe -sk -o NUL -w "%{http_code} %{content_type}" https://localhost:8443/projects/anything`; then `curl.exe -skI https://localhost:8443/assets/` followed by any file name from `docker compose exec caddy ls /srv/assets` | `200 text/html; charset=utf-8`; the asset has `cache-control: public, max-age=31536000, immutable` | Deep links reach the app's router; hashed assets are cached for good | |
| T14.9 | `[IO.File]::WriteAllBytes("$env:TEMP\big.json", (New-Object byte[] 1100000)); curl.exe -sk -o NUL -w "%{http_code}" -H "Content-Type: application/json" --data-binary "@$env:TEMP\big.json" https://localhost:8443/api/v1/auth/login` | `413` | Bodies over 1 MiB stop at the edge (D14.6) | |
| T14.10 | `curl.exe -sk -H "X-Forwarded-For: 203.0.113.9" https://localhost:8443/api/v1/health`, then `docker compose logs --tail 3 api` | the access line shows Docker's gateway address (for example `172.x.0.1`), **not** `203.0.113.9` and not Caddy's `172.30.231.2` | A client cannot choose its own address for throttling; the API takes it from Caddy alone (D14.13) | `203.0.113.9` means Caddy trusted the client's header; `172.30.231.2` means `HUMANI_T_TRUSTED_PROXIES` is not reaching the API |

#### Inside the stack

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T14.11 | `docker compose ps -q \| ForEach-Object { docker inspect --format '{{.Name}} ro={{.HostConfig.ReadonlyRootfs}} drop={{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}} user={{.Config.User}}' $_ }` | every line `ro=true drop=[ALL] [no-new-privileges:true]` and a non-empty, non-root user | The hardening baseline (D14.8) | A service lost its `<<: *hardened` |
| T14.12 | `docker compose ps --format "table {{.Service}}\t{{.Ports}}"` | ports only on `caddy` (8080, 8443) and `mailpit` (`127.0.0.1:8025`) | Only the edge is published (D14.9) | A `ports:` line was added to another service |
| T14.13 | `docker compose exec postgres wget -q -T 5 -O - http://example.org` | an error (`bad address` or a timeout), no page | Postgres cannot reach the internet (D14.9) | `postgres` was attached to a non-internal network |
| T14.14 | `docker compose exec api ls /run/secrets` | exactly `humani_t_database_url humani_t_inference_token humani_t_key_encryption_keys humani_t_openalex_api_key humani_t_smtp_password humani_t_valkey_url`; no migration URL, no Postgres passwords | Each container holds only its own secrets (D14.10, D14.11) | |
| T14.15 | `docker compose exec api env \| Select-String -Pattern 'password\|postgresql\|redis://'` | no output | No secret in the environment (D14.10) | |
| T14.16 | `docker compose top valkey` | one process, `valkey-server *:6379`; no password anywhere in the line | The Valkey password is not in a process list (D14.12) | If a `sh -c` line shows the password, the `exec` was lost |
| T14.17 | `docker compose exec -T postgres psql -U postgres -d humani_t -tAc "select tableowner from pg_tables where tablename = 'users'"` and `docker compose exec -T postgres psql -U postgres -lqt` | `humani_t_migrator`; the database list has `humani_t` and **no** `humani_t_test` | Migrations ran as the schema owner; production has no test database (D14.11) | |

#### Operating it

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T14.18 | `"violet-harbour-quietly-41-lanterns" \| docker compose exec -T api python -m app.cli create-admin --email admin@example.org --password-stdin` <!-- gitleaks:allow --> | `created admin admin@example.org` | The operator command works inside the container | `password refused` lists the policy it broke |
| T14.19 | Browser: https://localhost:8443/ (accept the warning), sign in as the admin; invite `reviewer@example.org`; open http://127.0.0.1:8025 | the app loads with no CSP errors in the developer console (F12); the invitation arrives in Mailpit | The app works under the strict CSP and Trusted Types; mail leaves through `egress` (D14.5, D14.9) | A console error naming `Content-Security-Policy` or `TrustedHTML` is a bug in the app or the policy: record it |
| T14.20 | `cmd /c "docker compose exec -T postgres pg_dump -U postgres --format=custom humani_t > stack-backup.dump"`, then `cmd /c "docker compose exec -T postgres pg_restore --list < stack-backup.dump" \| Select-Object -First 5` | a non-empty file; the listing starts with `; Archive created at …` and `dbname: humani_t` | The backup command in the self-hosting guide works from PowerShell | A listing error means PowerShell's own `>` was used and corrupted the file |
| T14.21 | `docker compose exec api python -m app.cli verify-audit` | `audit chain intact: N event(s)` | The audit command works in the stack | |

#### CI and GitHub (after you push the branch)

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T14.22 | On GitHub, the pull request's **Checks** tab | `api`: two jobs; `web`: five; `security`: gitleaks, osv-scanner, trivy ×3, zizmor and actionlint; `codeql`: three; `compose`: one. All green. | The whole pipeline (D14.15–D14.18) | Open the failing job; every step says what it checked |
| T14.23 | Repository **Settings** › **Code security**: Dependabot alerts and security updates, private vulnerability reporting | all enabled (see the hand-over list) | Reports reach the maintainers privately (D14.20) | |

To stop the trial stack: `docker compose down` keeps its data; `docker compose down -v` deletes its volumes. Either touches only `humani-t-stack`, never the development containers.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T14.1 | ☐ | |
| T14.2 | ☐ | |
| T14.3 | ☐ | |
| T14.4 | ☐ | |
| T14.5 | ☐ | |
| T14.6 | ☐ | |
| T14.7 | ☐ | |
| T14.8 | ☐ | |
| T14.9 | ☐ | |
| T14.10 | ☐ | |
| T14.11 | ☐ | |
| T14.12 | ☐ | |
| T14.13 | ☐ | |
| T14.14 | ☐ | |
| T14.15 | ☐ | |
| T14.16 | ☐ | |
| T14.17 | ☐ | |
| T14.18 | ☐ | |
| T14.19 | ☐ | |
| T14.20 | ☐ | |
| T14.21 | ☐ | |
| T14.22 | ☐ | |
| T14.23 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the command, its output and the service's log. It gets a failing check before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The API answers through Caddy over HTTPS; HTTP redirects | `compose.yml` › *The API answers through Caddy over HTTPS*, › *Plain HTTP redirects to HTTPS* |
| Every security header on the app and the API; no `Server`, no `unsafe-inline` | `compose.yml` › *Security headers* |
| Bodies over 1 MiB refused at the edge | `compose.yml` › *Oversized request bodies are refused at the edge* |
| Non-root, read-only, no capabilities, `no-new-privileges`; only Caddy publishes | `compose.yml` › *Every container runs as a non-root user*, › *Every container is hardened and publishes only what it should* |
| Each container holds only its own secrets; none in the environment | `compose.yml` › *Each container holds only its own secrets* |
| Every image pinned; development runs production's images | `compose.yml` › *Every image is pinned by digest*, › *Development images match the self-hosted stack* |
| The API's tests pass against real Postgres and Valkey with ≥ 85% branch coverage | `api.yml` › *pytest with branch coverage* |
| Lint, format, types and layering | `api.yml` › static job; `web.yml` |
| No secrets, no vulnerable dependencies, no fixable HIGH/CRITICAL in images | `security.yml` |
| Workflows pinned and least-privilege | `security.yml` › zizmor, actionlint |
| SAST | `codeql.yml` |

The workflows lint locally with Docker, from the repository root:

```powershell
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:1.7.12
docker run --rm -v "${PWD}/.github:/repo/.github:ro" -w /repo ghcr.io/zizmorcore/zizmor:1.31.1 --offline --persona=pedantic .github/
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G14.1 | Server-Sent Events through Caddy are configured but not tested end to end | A buffering regression would freeze run progress in the UI without failing CI | Add to the smoke test once `GET /api/v1/runs/{id}/events` exists |
| G14.2 | The CSP and Trusted Types are checked as headers, not in a browser running the app | A library that writes `innerHTML` would break the app in production only | M13: Playwright against the compose stack, failing on any CSP violation (T14.19 by hand until then) |
| G14.4 | `migrate` runs with `HUMANI_T_ENVIRONMENT=development` because `migrations/env.py` validates the whole `Settings` | None today (Alembic uses only the migration URL), but a future production-only check would not apply to it | `env.py` reads only the migration URL; then remove the override |
| G14.5 | Behind Docker Desktop, every client appears as Docker's gateway address | Per-IP throttling applies to all clients together on Windows and macOS hosts | Production is Linux, where addresses are preserved; documented |
| G14.6 | `CODE_OF_CONDUCT.md` still reads `[INSERT CONTACT METHOD]` | Nobody can report a conduct problem privately | The maintainer chooses a contact address |
| G14.7 | No published, signed images; operators build from source | Slower installs; no provenance to verify | Publish to GHCR with cosign signatures and SLSA provenance |
| G14.8 | SMTP over TLS, load across several API workers, mutation testing and API fuzzing are not in CI | See [`controls.md`](../security/controls.md#gaps--known-not-yet-closed) | Each listed there |
| G14.9 | Valkey uses one shared password, not per-client ACLs | A compromised worker could run any Valkey command | With the job queue's final command set |

## Glossary

| Term | Meaning |
| --- | --- |
| **Digest** | `sha256:…` after an image name: the hash of its exact contents. Unlike a tag, it cannot be moved to point at something else. |
| **Multi-stage build** | A Dockerfile with several `FROM`s; only the last stage becomes the image, so build tools stay behind. |
| **Allowlist `.dockerignore`** | Exclude everything (`*`), then re-include only what the build needs (`!app/`). |
| **Compose project** | The name Compose prefixes to containers, networks and volumes (`humani-t-stack-api-1`). Different names never share anything. |
| **Profile** | A label on an optional service; it starts only when its profile is enabled (`COMPOSE_PROFILES=local-model`). |
| **Internal network** | A Docker network with no route out of the host and no published ports. |
| **Capability** | A slice of root's power in Linux (binding low ports, changing file owners…). `cap_drop: [ALL]` removes every one. |
| **`no-new-privileges`** | A process flag that stops a program gaining privileges by running a setuid binary or one with file capabilities. |
| **tmpfs** | A filesystem in memory; gone when the container stops. |
| **Docker secret** | A file Compose mounts at `/run/secrets/<name>`, readable only inside the containers that list it. |
| **KEK** | Key-encryption key: the operator-held key that wraps every project's data key ([03](03-envelope-encryption.md)). |
| **CSP** | Content Security Policy: a response header listing where a page may load scripts, styles and other resources from. |
| **Trusted Types** | A browser feature, enabled by CSP, that refuses plain strings at DOM sinks such as `innerHTML`. |
| **HSTS** | A header telling browsers to use only HTTPS for this host from now on. |
| **COOP / CORP** | Cross-Origin Opener and Resource Policy: isolate the page's window and resources from other sites. |
| **BREACH** | An attack that recovers secrets from compressed HTTPS responses that also reflect attacker-chosen text. |
| **SSE** | Server-Sent Events: a one-way stream of events from server to browser over one HTTP response. |
| **SAST** | Static application security testing: finding vulnerabilities by analysing code without running it. |
| **SHA pinning** | Referring to a GitHub Action by its full commit hash instead of a tag. |
| **Cooldown** | Dependabot waiting a number of days after a release before proposing it. |
| **Crypto-shredding** | Making data unreadable by destroying its key rather than the data ([03](03-envelope-encryption.md); limits in the self-hosting guide, section 6). |
