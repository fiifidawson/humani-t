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

## Git Commands

Branches in this repo: `main` (default), `v1` (active work), `staging`, `prod`.
Remote is `origin`.

### Daily loop
| Command | What it does |
| ------- | ------------ |
| `git status -sb` | Short status, plus branch and ahead/behind count |
| `git add -p` | Stage hunk by hunk — the best habit for clean, reviewable commits |
| `git add .` | Stage everything changed at or below the current directory |
| `git commit -m "feat(api): add health endpoint"` | Commit using the format above |
| `git push` | Push the current branch to `origin` |

### Branching
| Command | What it does |
| ------- | ------------ |
| `git switch v1` | Move to an existing branch |
| `git switch -c feat/inference-provider` | Create a branch and move to it |
| `git switch -` | Jump back to the previous branch |
| `git branch` | List local branches |
| `git branch -a` | List local and remote branches |
| `git push -u origin feat/inference-provider` | First push of a new branch; sets upstream |
| `git branch -d feat/inference-provider` | Delete a local branch that has been merged |

`switch` and `restore` are the modern split of the two unrelated jobs `checkout`
used to do. Older tutorials say `git checkout <branch>` — still valid, but
`switch` states the intent.

### Inspecting before you commit
| Command | What it does |
| ------- | ------------ |
| `git diff` | Unstaged changes |
| `git diff --staged` | Exactly what you are about to commit |
| `git diff --stat` | Files changed and line counts, no content |
| `git log --oneline --graph --decorate -10` | Recent history as a graph |
| `git show <sha>` | Full contents of one commit |
| `git log -- docs/architecture.md` | History of a single file |

### Undoing things
| Command | What it does | Risk |
| ------- | ------------ | ---- |
| `git restore <file>` | Discard unstaged changes to a file | Those edits are gone |
| `git restore --staged <file>` | Unstage but keep the edits | None |
| `git commit --amend` | Rewrite the most recent commit | Only if not yet pushed |
| `git reset --soft HEAD~1` | Undo last commit, keep changes staged | None |
| `git reset --hard HEAD` | Discard all uncommitted work | Destructive |
| `git stash` / `git stash pop` | Shelve changes, then restore them | None |
| `git revert <sha>` | New commit that undoes an old one | None — safe on pushed history |
| `git reflog` | Every position HEAD has held, including before a bad reset | None — this is the recovery lifeline |

Rule of thumb: `revert` for anything already pushed, `reset` only for local
commits nobody else has pulled.

### Syncing
| Command | What it does |
| ------- | ------------ |
| `git fetch` | Update remote-tracking refs; changes nothing in the working tree |
| `git pull --rebase` | Fetch, then replay your commits on top — avoids merge-commit noise |
| `git merge main` | Bring `main` into the current branch |

### Typical session
```bash
git switch v1
git pull --rebase

# ...work...

git status -sb
git diff
git add -p
git commit -m "feat(api): add health endpoint"
git push
```

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
