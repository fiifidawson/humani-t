# humani-t

Privacy-preserving evidence synthesis for humanitarian decisions.

A reviewer states a research question. humani-t searches academic repositories,
screens the results against review criteria, and synthesises them with
citations. It does this reproducibly, and without sending sensitive material
anywhere it should not go.

humani-t is a **self-hosted web application**. An organisation runs it on
infrastructure it controls, so questions, documents and results stay within
that deployment. Personal data is masked before any text reaches a language
model.

> **Status: early development.** The search domain and API skeleton exist. The
> security core (accounts, MFA, projects, encryption, audit log) is being built
> next. Do not deploy this with real data yet.

## How it works

1. **Ask a question.** Refine the search plan with the model, or bring your own
   strict vocabulary.
2. **Discover** papers in OpenAlex, Europe PMC and arXiv.
3. **Mask** personal data in papers and your own documents, locally, with no
   LLM.
4. **Screen** each paper against the criteria. Every decision keeps its
   evidence and provenance.
5. **Review, export and ask.** Relabel, export a dataset and a PRISMA report,
   and ask follow-up questions answered with cited sources.

See [`docs/WEB_APP_BRIEF.md`](docs/WEB_APP_BRIEF.md) for the full workflow and
the security rules the app keeps.

## Development

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js with
[pnpm](https://pnpm.io/).

```bash
cd services/api
uv sync
uv run uvicorn app.main:app --reload --port 8731   # http://127.0.0.1:8731/docs
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Documentation

| Document | What it covers |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | Code standards and the working agreement |
| [`docs/architecture.md`](docs/architecture.md) | Layers, provider seams, domain model, security model |
| [`docs/api.md`](docs/api.md) | HTTP API reference |
| [`docs/standards.md`](docs/standards.md) | Commit messages and git workflow |
| [`docs/adr/`](docs/adr/) | Architecture decision records |

## Licence

[Apache License 2.0](LICENSE).
