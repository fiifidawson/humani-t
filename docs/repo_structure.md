# Repository Structure

This is the canonical layout. Folders marked *(planned)* are added by the
milestone that first needs them; they are listed so new code has an obvious home.

```text
humani-t/
|
├── apps/
│   └── web/                  # Vite + React + strict TypeScript SPA (planned)
│       └── src/
│           ├── api/          # generated OpenAPI types + typed client
│           ├── features/     # auth, projects, account, …
│           └── components/
│
├── services/
│   └── api/                  # Python backend (FastAPI)
│       ├── app/
│       │   ├── main.py       # composition root: app, middleware, routers
│       │   ├── api/          # HTTP boundary: routes, schemas, dependencies.py
│       │   ├── application/  # use cases / services
│       │   ├── domain/       # entities and rules, no I/O
│       │   ├── search/       # PaperSearchProvider + implementations
│       │   ├── storage/      # StorageProvider + repositories (planned)
│       │   ├── crypto/       # KeyProvider + envelope encryption (planned)
│       │   ├── auth/         # hashers, passkeys, TOTP, OIDC (planned)
│       │   ├── email/        # EmailProvider (planned)
│       │   ├── ratelimit/    # RateLimiter (planned)
│       │   ├── net/          # HttpFetcher, SSRF-guarded (planned)
│       │   └── inference/    # InferenceProvider (planned)
│       ├── migrations/       # Alembic
│       ├── tests/
│       ├── pyproject.toml
│       └── uv.lock
│
├── infrastructure/
│   ├── docker/               # Dockerfiles and compose support files
│   ├── caddy/                # Caddyfile (planned)
│   └── postgres/             # database role setup (planned)
│
├── .github/                  # CI workflows, templates, Dependabot (planned)
├── docs/
│   └── adr/                  # architecture decision records
├── scripts/
│
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

`apps/web` talks to the API only through generated types. Every rule — who may
see a project, what gets masked — lives in `services/api`, because the browser
is not trusted.

## Notes

- `migrations/` lives inside `services/api/`, not at the repository root, so the
  Python service stays self-contained and builds as one image.
- There are no shared `packages/`. With a single frontend there is nothing to
  share, and the generated API types live next to the code that uses them. See
  [ADR-0001](./adr/0001-web-only-self-hosted.md).
