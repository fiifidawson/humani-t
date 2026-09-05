# Repository Structure

This is the canonical layout. It supersedes the sketch in
[`system_overview.md`](./system_overview.md) Part 1, which placed the Python
backend at `backend/` and omitted `crates/`.

```text
humani-t/
|
├── apps/
│   ├── web/                  # V1 - Vite + React web application
│   └── desktop/              # V2 - Tauri shell (placeholder until V2)
│
├── packages/
│   ├── ui/                   # shared React components
│   ├── api-client/           # AppAPI interface + RemoteAPIClient / LocalAPIClient
│   ├── schemas/              # OpenAPI spec (source of truth) + generated types
│   └── config/               # shared tsconfig / lint / runtime config
│
├── services/
│   └── api/                  # Python backend (FastAPI)
│       ├── app/
│       │   ├── api/          # HTTP routes + dependencies
│       │   ├── application/  # use cases / services
│       │   ├── domain/       # entities and rules, no I/O
│       │   ├── infrastructure/
│       │   ├── inference/    # InferenceProvider + implementations
│       │   ├── storage/      # StorageProvider + repositories
│       │   ├── auth/         # authentication / authorization
│       │   └── agents/       # ToolProvider + tool implementations
│       ├── migrations/       # Alembic
│       ├── tests/
│       ├── pyproject.toml
│       └── uv.lock
│
├── crates/
│   └── desktop-core/         # V2 - Rust/Tauri core
│
├── infrastructure/
│   ├── docker/
│   ├── render/
│   └── ci/
│
├── docs/
├── scripts/
│
├── package.json
├── pnpm-workspace.yaml
├── turbo.json
├── biome.json
├── tsconfig.base.json
├── pyrightconfig.json
├── .env.example
└── .gitignore
```

## Rules

| Directory | Contains | Never contains |
| --- | --- | --- |
| `apps/` | deployable applications | business logic |
| `packages/` | code shared across apps | server-side logic |
| `services/` | server-side application logic | UI |
| `crates/` | native/desktop runtime (V2) | application logic |
| `infrastructure/` | deployment and runtime config | application code |

`apps/web` is a thin composition of `packages/ui` over `packages/api-client`.
It must remain replaceable by `apps/desktop` without moving any logic.

## Notes on deviations from earlier drafts

- `services/api/` replaces the earlier `backend/` location. The empty `backend/`
  directory from initial setup is removed.
- `migrations/` lives inside `services/api/`, not at the repository root, so the
  Python service stays self-contained and can be packaged as a V2 sidecar.
- `app/auth/` is carried over from `system_overview.md` Part 5; it was absent
  from the earlier structure sketch.
