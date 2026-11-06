## What and why

<!-- What this changes, and the reason. Link the issue it closes ("Closes #12"). One idea per pull request: two unrelated changes are two pull requests. -->

## How it was tested

<!-- The failing test you wrote first, then the commands you ran and what they printed. A failing test named in full is more useful than "tests pass". -->

## Before saying it is done

From [`CLAUDE.md`](https://github.com/fiifidawson/humani-t/blob/main/CLAUDE.md). Tick each one, or say why it does not apply.

- [ ] `cd services/api && uv run pytest --cov && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` passes.
- [ ] For web changes: `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build` pass, and `pnpm check:api` reports no stale API types.
- [ ] A new seam got a `Protocol` in `<area>/base.py` and a fake.
- [ ] Nothing outside `app/api/dependencies.py` imports a concrete provider.
- [ ] A new endpoint got tests and an entry in [`docs/api.md`](https://github.com/fiifidawson/humani-t/blob/main/docs/api.md).
- [ ] A new domain entity appears in [`docs/architecture.md`](https://github.com/fiifidawson/humani-t/blob/main/docs/architecture.md) §4.
- [ ] A decision that is hard to reverse has an ADR in [`docs/adr/`](https://github.com/fiifidawson/humani-t/tree/main/docs/adr).
- [ ] A new dependency is justified in the commit message.

## Privacy and provenance

- [ ] Nothing new leaves the deployment (a request, a log line, a document) except to a destination the operator configures, and only after masking.
- [ ] Any new inclusion or exclusion decision stores what produced it, at the moment it is made.
