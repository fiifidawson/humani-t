# Dependencies and Prior Art

humani-t is a rebuild, not a greenfield project. This document records what it
inherits, what it depends on, and what it deliberately rejects.

---

## Summary

| Project | Kind | What we take | How |
| --- | --- | --- | --- |
| [CHITCHAT](https://github.com/fiifidawson/CHITCHAT) | **Prior art — ours** | Requirements, validated pipeline design, working reference code | Port selectively; see §1 |
| [mmore](https://github.com/EPFLiGHT/mmore) | External dependency | Multimodal extraction, hybrid indexing, RAG | `uv add mmore` — v2.0.0 on PyPI |
| [AMMORE](https://github.com/LiGHTers-playground/AMMORE) | External pattern | The agentic loop, as a design | Reimplement on `autogen-agentchat` |

Only mmore is an actual dependency. CHITCHAT is our own first prototype, and
AMMORE is a design we rebuild rather than import.

---

## 1. CHITCHAT — the first prototype

CHITCHAT was the broader research umbrella; humani-t is the application that grew
out of it. The [overview](./CHITCHAT-OVERVIEW.md) in this directory describes the
**first prototype of this same system**, not a separate project.

That makes it the closest thing the project has to a requirements document. Its
six pipeline stages are already validated by use, and they map onto the build
order directly:

| CHITCHAT stage | humani-t equivalent |
| --- | --- |
| 1. Curated vocabulary of concepts and synonyms | `Vocabulary`, `Concept` domain types |
| 2. Queries built automatically from the vocabulary | `BooleanQuery`, compiled per source |
| 3. Search arXiv / OpenAlex / Europe PMC / Google Scholar, deduplicated | `PaperSearchProvider` |
| 4. Collect papers, extract text and metadata | `fetch_fulltext` + mmore extraction |
| 5. LLM-assisted screening, scores and priorities | `ScreeningProvider` |
| 6. Analyse and visualise | LitLens-equivalent, `apps/web` |

Three commitments from the prototype carry over as architectural constraints, not
preferences:

- **Transparency** — the vocabulary, query logic, sources, and screening criteria
  are all readable artefacts, not implicit behaviour.
- **Reproducibility** — the same inputs produce the same result, and changing one
  input shows you what it changed. This is what `RunManifest` exists for.
- **Auditability** — screening narrows the field, it never makes the final call.
  A human must be able to see what was excluded and why.

One structural idea from the prototype is worth preserving explicitly: **every
stage hands off a plain data file to the next**, so any stage can be re-run,
inspected, or replaced independently. In the new architecture that becomes stage
artefacts recorded against a `RunManifest` — the same property, expressed through
the provider seams rather than through files on disk.

### What carries forward

The open question is not *whether* to reuse the prototype but *how much*. The
repository ships `requirements.txt` and scripts with no `pyproject.toml`, and
already carries a `legacy/` directory, so some of it has been superseded once
before.

| Asset | Recommendation |
| --- | --- |
| The vocabulary itself (terms, synonyms, categories) | **Port as data.** It is the most valuable and least replaceable artefact here — hard-won and independent of implementation. |
| Repository API clients (four sources) | **Port the logic, rewrite the shape** behind `PaperSearchProvider`. The quirks of each API are real knowledge; the calling convention changes. |
| Screening prompts and criteria | **Port**, versioned from the start so decisions stay auditable. |
| LitLens visualisations | **Rebuild** in `apps/web`. It was local-file analytics; here it is a UI over the API. |
| Orchestration scripts | **Do not port.** This is what the application layer replaces. |

This is worth recording as **ADR-0001**, because the vocabulary decision in
particular is hard to reverse once screening results depend on it.

---

## 2. mmore

A genuine external dependency, published as `mmore` 2.0.0 with modular extras
(`process`, `index`, `rag`, `api`), so we install only what we use.

**What we take:** document extraction across PDFs, Office files, images and audio;
hybrid dense+sparse indexing into Milvus; the retrieval layer beneath the agentic
loop.

**What we do not take:** mmore's own FastAPI servers. We consume it as a library
from inside `services/api`, behind our own interfaces. Running mmore's API
alongside ours would create a second HTTP boundary with its own contract, which
breaks the single-API-contract rule that makes V2 possible.

**Where it sits:** implementations only, in `app/storage/` and
`app/infrastructure/`. mmore types must not appear in `domain/` or `application/`.

**Open risk — the Boolean search branch.** The Boolean paper search we need is on
a branch that has not been merged upstream. Options, in order of preference:

1. Contribute it upstream and depend on a released version
2. Pin a git revision in `pyproject.toml` until it merges
3. Fork, if upstream stalls

Pinning a git ref is acceptable short-term and must be recorded with a reason — an
unpinned fork is how a project quietly acquires a dependency nobody maintains.

**Open risk — AI screening does not exist upstream.** Assume we write it, against
our own `ScreeningProvider`. If it later lands upstream, the interface makes
adopting it a swap rather than a rewrite.

---

## 3. AMMORE

Not a dependency. A design we reimplement.

AMMORE is an application rather than a package — absent from PyPI, and valuable to
us for its architecture rather than its code:

```text
Planner    decomposes the question into 4-6 sub-queries
Retriever  searches the corpus, or the web as fallback
Critic     assesses coverage; approves or returns follow-up queries
Writer     synthesises with citations
```

That loop is the right shape for evidence synthesis. The framework underneath it,
`autogen-agentchat`, is on PyPI and can be added directly.

**Explicitly rejected:**

- **Its OpenAI default.** AMMORE defaults to `gpt-4o-mini`. Sending a humanitarian
  team's question to a third-party API contradicts the core privacy commitment.
  Every model call goes through `InferenceProvider`, and the default must be one
  we can run ourselves.
- **Its Next.js + Chakra frontend.** We are on Vite + React in `apps/web`, and the
  frontend must stay shareable with the V2 Tauri shell.
- **Tavily web-search fallback**, unless a user explicitly enables it. A silent
  fallback to live web search is undisclosed egress of the user's question.

---

## 4. What this changes in the build order

The checklist in [`system_overview.md`](./system_overview.md) assumes a chat
application: chat, then conversations, then files, then RAG at step 16, agents at
17. For this project that order is inverted — retrieval is the product, not a
later feature.

Revised order for V1:

```text
[x] 1. Health endpoint, running locally
[ ] 2. Provider interfaces defined (all ten)
[~] 3. Fake implementations of each, no external services
        done: PaperSearchProvider
[x] 4. Vocabulary + BooleanQuery domain types
[ ] 5. One PaperSearchProvider end to end (OpenAlex - open API, no key)
        the HTTP surface is built and served by the fake; only
        api/dependencies.py changes to make it live
[ ] 6. Persistence: Review, PaperRecord, RunManifest
[ ] 7. Remaining search providers behind the same interface
[ ] 8. Deduplication and full-text fetch
[ ] 9. mmore extraction + indexing behind VectorStoreProvider
[ ] 10. ScreeningProvider with a real model
[ ] 11. Human review surface - the auditability requirement
[ ] 12. Agentic synthesis loop (Planner/Retriever/Critic/Writer)
[ ] 13. SanitizationProvider - mmore privacy mode at query time
[ ] 14. LitLens-equivalent analytics
[ ] 15. Auth, export/delete, privacy controls
[ ] 16. Dockerise, deploy to Render
[ ] 17. Freeze the API and domain contracts

    OPEN - ingest-time sanitisation. Not scheduled. Investigate first
    whether the real exposure is the corpus or the user's own inputs.
    See architecture.md 3.10.
```

Step 5 is the first vertical slice worth aiming at. OpenAlex needs no API key, so
one provider can be proven end to end before any credential or paid service is
involved.

Steps 2 and 3 stay early for the reason given in
[`architecture.md`](./architecture.md) §7: fake implementations prove an interface
is right while it is still free to change.
