# Standards

## Commit Messages
### Standard format
```bash
<type>(<scope>): <description>
```
Example:
```bash
feat(chat): add streaming responses
```

### Commit Types
| Type       | Use for                                              | Example                                               |
| ---------- | ---------------------------------------------------- | ----------------------------------------------------- |
| `feat`     | New functionality                                    | `feat(chat): add conversation streaming`              |
| `fix`      | Bug fixes                                            | `fix(auth): prevent unauthorized conversation access` |
| `refactor` | Code restructuring without changing behavior         | `refactor(inference): extract provider interface`     |
| `docs`     | Documentation                                        | `docs(api): document chat endpoints`                  |
| `test`     | Adding/changing tests                                | `test(chat): add streaming failure tests`             |
| `chore`    | Maintenance that doesn't affect application behavior | `chore(deps): update fastapi`                         |
| `build`    | Build/package/tooling changes                        | `build(desktop): configure tauri bundle`              |
| `ci`       | CI/CD changes                                        | `ci: add python type checking`                        |
| `perf`     | Performance improvements                             | `perf(rag): reduce embedding latency`                 |
| `revert`   | Reverting a previous commit                          | `revert: revert feat(chat): add tool execution`       |

### Schemas
```
web
desktop
ui
api
auth
chat
conversation
files
rag
inference
agents
storage
backup
sync
security
privacy
database
api-client
schemas
docs
deps
```

Note: Enforce through commitlint + Git hook/CI rule

## Library

| Tool       | Where it lives                                         | Scope                            |
| ---------- | ------------------------------------------------------ | -------------------------------- |
| `pnpm`     | Usually installed/managed at developer/CI level        | Tool used to manage JS workspace |
| Turborepo  | **Root `package.json`**                                | Entire monorepo                  |
| Biome      | **Root `package.json`**                                | Entire JS/TS workspace           |
| TypeScript | Root or individual package                             | Usually shared from root         |
| `uv`       | Installed as a developer tool                          | Manages Python projects          |
| Ruff       | **Python project's `pyproject.toml` / uv environment** | Python service                   |
| Pyright    | Root JS side or Python environment                     | Python type checking             |
| FastAPI    | **`services/api/pyproject.toml`**                      | Python backend                   |
| SQLAlchemy | **`services/api/pyproject.toml`**                      | Python backend                   |
| Alembic    | **`services/api/pyproject.toml`**                      | Python backend                   |
| pytest     | **`services/api/pyproject.toml`**                      | Python backend                   |
| Tauri      | **`apps/desktop` / Rust Cargo project**                | Desktop app                      |
| Cargo      | Rust toolchain                                         | Rust projects                    |
| Vitest     | Root/package `package.json`                            | JS/TS tests                      |
| Playwright | Root/package `package.json`                            | E2E testing                      |
| Changesets | Root `package.json`                                    | Entire monorepo                  |
| Renovate   | `.github/renovate.json`                                | Entire repo                      |
| Docker     | Developer/CI environment                               | Builds/deploys services          |
