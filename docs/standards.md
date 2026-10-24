# Standards

This file covers **process**: commit messages, git workflow, and where each tool lives.

For **code** standards — layering rules, PEP conventions, typing, comments, docstrings, naming, error handling and tests — see [`CLAUDE.md`](../CLAUDE.md) at the repository root. It is loaded automatically by coding agents, so it is the file that actually steers how code gets written here.

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
| `build`    | Build/package/tooling changes                        | `build(api): add multi-stage Dockerfile`              |
| `ci`       | CI/CD changes                                        | `ci: add python type checking`                        |
| `perf`     | Performance improvements                             | `perf(rag): reduce embedding latency`                 |
| `revert`   | Reverting a previous commit                          | `revert: revert feat(chat): add tool execution`       |

### Scopes
The scope is optional. When used, it is one of:
```
api          backend HTTP layer and application code
web          frontend
auth         sign-in, sessions, MFA, OIDC
security     headers, CSRF, rate limiting, SSRF guard
crypto       encryption and key handling
audit        audit log
privacy      masking and sanitisation
storage      persistence and migrations
search       paper search providers
inference    model calls
rag          indexing and retrieval
agents       synthesis loop and tools
deploy       Docker, Compose, Caddy
ci           GitHub Actions
docs         documentation
deps         dependency updates
adr          architecture decision records
```

The body (after a blank line) says *why*. A commit that adds a dependency says why that dependency, and not an alternative, in the body.

## Git Commands

`main` is the default branch and is always releasable. Work happens on short-lived branches named `<type>/<topic>` (`feat/api-foundation`, `docs/web-only`) and lands through a pull request. Remote is `origin`.

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
| `git switch main` | Move to an existing branch |
| `git switch -c feat/inference-provider` | Create a branch and move to it |
| `git switch -` | Jump back to the previous branch |
| `git branch` | List local branches |
| `git branch -a` | List local and remote branches |
| `git push -u origin feat/inference-provider` | First push of a new branch; sets upstream |
| `git branch -d feat/inference-provider` | Delete a local branch that has been merged |

`switch` and `restore` are the modern split of the two unrelated jobs `checkout` used to do. Older tutorials say `git checkout <branch>` — still valid, but `switch` states the intent.

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

Rule of thumb: `revert` for anything already pushed, `reset` only for local commits nobody else has pulled.

### Syncing
| Command | What it does |
| ------- | ------------ |
| `git fetch` | Update remote-tracking refs; changes nothing in the working tree |
| `git pull --rebase` | Fetch, then replay your commits on top — avoids merge-commit noise |
| `git merge main` | Bring `main` into the current branch |

### Typical session
```bash
git switch main
git pull --rebase
git switch -c feat/my-change

# ...work...

git status -sb
git diff
git add -p
git commit -m "feat(api): add health endpoint"
git push -u origin feat/my-change   # then open a pull request
```

## Library

| Tool | Where it lives | Scope |
| --- | --- | --- |
| `pnpm` | Developer/CI tool, version pinned in root `package.json` | JS workspace |
| Biome | Root `package.json` | Format and lint all TS/JSON |
| TypeScript | `apps/web`, strict, extends root `tsconfig.base.json` | Frontend type checking |
| Vitest | `apps/web` | Frontend unit and component tests |
| Playwright | `apps/web` | End-to-end smoke tests against Compose |
| `uv` | Developer/CI tool | Python environments and lockfile |
| Ruff | `services/api/pyproject.toml` | Python format and lint |
| Pyright | `services/api/pyproject.toml` | Python type checking |
| import-linter | `services/api/pyproject.toml` | Enforces the layering rules |
| pytest | `services/api/pyproject.toml` | Python tests |
| FastAPI | `services/api/pyproject.toml` | HTTP layer |
| SQLAlchemy + Alembic | `services/api/pyproject.toml` | Persistence and migrations |
| pre-commit | `.pre-commit-config.yaml` | Runs the checks before each commit |
| Docker + Compose | `compose.yaml`, `services/api/Dockerfile` | Build and self-hosted deployment |
| GitHub Actions | `.github/workflows/` | CI and security scanning |
| Dependabot | `.github/dependabot.yml` | Dependency update pull requests |
