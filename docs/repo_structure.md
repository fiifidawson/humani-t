# Repository Structure

This is the canonical layout. Folders marked *(planned)* are added by the milestone that first needs them; they are listed so new code has an obvious home.

```text
humani-t/
|
├── apps/
│   └── web/                  # Vite + React + strict TypeScript SPA
│       ├── Dockerfile        # builds the SPA, serves it from Caddy
│       └── src/
│           ├── api/          # generated OpenAPI types + typed client
│           ├── features/     # auth, projects, account, chat, runs, …
│           ├── shell/        # three-zone layout
│           └── ui/           # shared components
│
├── services/
│   └── api/                  # Python backend (FastAPI)
│       ├── Dockerfile        # one image: api, worker and migrate entry points
│       ├── app/
│       │   ├── main.py       # composition root: app, middleware, routers
│       │   ├── worker.py     # background run worker
│       │   ├── api/          # HTTP boundary: routes, schemas, dependencies.py
│       │   ├── application/  # use cases / services
│       │   ├── domain/       # entities and rules, no I/O
│       │   ├── search/       # PaperSearchProvider + implementations
│       │   ├── storage/      # StorageProvider + repositories
│       │   ├── crypto/       # KeyProvider + envelope encryption
│       │   ├── auth/         # password hashing, passkeys
│       │   ├── email/        # EmailProvider
│       │   ├── ratelimit/    # RateLimiter
│       │   ├── signals/      # run wake-ups across processes
│       │   ├── runs/         # Stage seam for pipeline stages
│       │   ├── net/          # HttpFetcher, SSRF-guarded
│       │   └── inference/    # InferenceProvider
│       ├── migrations/       # Alembic
│       ├── tests/
│       ├── pyproject.toml
│       └── uv.lock
│
├── infrastructure/
│   ├── caddy/                # Caddyfile and the Caddy build
│   ├── postgres/             # database role setup
│   └── generate_secrets.py   # secrets for the self-hosted stack
│
├── .github/                  # CI workflows, templates, Dependabot
├── docs/
│   ├── adr/                  # architecture decision records
│   ├── deploy/               # self-hosting guides
│   └── walkthroughs/         # one detailed walkthrough per phase
├── scripts/
│
├── compose.yaml              # the full self-hosted stack
├── compose.dev.yaml          # development services: Postgres, Valkey, Mailpit
├── package.json              # pnpm workspace root: Biome, shared scripts
├── pnpm-workspace.yaml
├── biome.json
├── .gitattributes
└── .gitignore
```

## Rules

| Directory | Contains | Never contains |
| --- | --- | --- |
| `apps/` | deployable frontends | business logic, secrets |
| `services/` | server-side application logic | UI |
| `infrastructure/` | deployment and runtime config | application code |
| `docs/` | how and why | generated API types |

**Dockerfiles sit next to the code they build**, not in a shared `infrastructure/docker/`. Each image's build context is then its own package, with its own `.dockerignore`, and Dependabot finds each base image by directory. `infrastructure/` holds only what runs beside the application: Caddy, the Postgres init script, secret generation.

`apps/web` talks to the API only through generated types. Every rule — who may see a project, what gets masked — lives in `services/api`, because the browser is not trusted.

## Notes

- `migrations/` lives inside `services/api/`, not at the repository root, so the Python service stays self-contained and builds as one image.
- There are no shared `packages/`. With a single frontend there is nothing to share, and the generated API types live next to the code that uses them. See [ADR-0001](./adr/0001-web-only-self-hosted.md).
