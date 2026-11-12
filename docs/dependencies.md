# Dependencies and Prior Art

humani-t is a rebuild, not a greenfield project. This document records what it inherits, what it depends on, and what it deliberately rejects.

---

## Summary

| Project | Kind | What we take | How |
| --- | --- | --- | --- |
| [CHITCHAT](https://github.com/fiifidawson/CHITCHAT) | **Prior art — ours** | Requirements, validated pipeline design, working reference code | Port selectively; see §1 |
| [mmore](https://github.com/EPFLiGHT/mmore) | External dependency | Multimodal extraction, hybrid indexing, RAG | `uv add mmore` — v2.0.0 on PyPI |
| [AMMORE](https://github.com/LiGHTers-playground/AMMORE) | External pattern | The agentic loop, as a design | Reimplement on `autogen-agentchat` |

Only mmore is an actual dependency. CHITCHAT is our own first prototype, and AMMORE is a design we rebuild rather than import.

---

## 1. CHITCHAT — the first prototype

CHITCHAT was the broader research umbrella; humani-t is the application that grew out of it. The [overview](./CHITCHAT-OVERVIEW.md) in this directory describes the **first prototype of this same system**, not a separate project.

That makes it the closest thing the project has to a requirements document. Its six pipeline stages are already validated by use, and they map onto the build order directly:

| CHITCHAT stage | humani-t equivalent |
| --- | --- |
| 1. Curated vocabulary of concepts and synonyms | `Vocabulary`, `Concept` domain types |
| 2. Queries built automatically from the vocabulary | `BooleanQuery`, compiled per source |
| 3. Search arXiv / OpenAlex / Europe PMC / Google Scholar, deduplicated | `PaperSearchProvider` |
| 4. Collect papers, extract text and metadata | Full-text retrieval (not built) + mmore extraction |
| 5. LLM-assisted screening, scores and priorities | `ScreeningProvider` |
| 6. Analyse and visualise | LitLens-equivalent, `apps/web` |

Three commitments from the prototype carry over as architectural constraints, not preferences:

- **Transparency** — the vocabulary, query logic, sources, and screening criteria are all readable artefacts, not implicit behaviour.
- **Reproducibility** — the same inputs produce the same result, and changing one input shows you what it changed. This is what `RunManifest` exists for.
- **Auditability** — screening narrows the field, it never makes the final call. A human must be able to see what was excluded and why.

One structural idea from the prototype is worth preserving explicitly: **every stage hands off a plain data file to the next**, so any stage can be re-run, inspected, or replaced independently. In the new architecture that becomes stage artefacts recorded against a `RunManifest` — the same property, expressed through the provider seams rather than through files on disk.

### What carries forward

The open question is not *whether* to reuse the prototype but *how much*. The repository ships `requirements.txt` and scripts with no `pyproject.toml`, and already carries a `legacy/` directory, so some of it has been superseded once before.

| Asset | Recommendation |
| --- | --- |
| The vocabulary itself (terms, synonyms, categories) | **Port as data.** It is the most valuable and least replaceable artefact here — hard-won and independent of implementation. |
| Repository API clients (four sources) | **Port the logic, rewrite the shape** behind `PaperSearchProvider`. The quirks of each API are real knowledge; the calling convention changes. Three are ported; Google Scholar is dropped (§4). |
| Screening prompts and criteria | **Port**, versioned from the start so decisions stay auditable. |
| LitLens visualisations | **Rebuild** in `apps/web`. It was local-file analytics; here it is a UI over the API. |
| Orchestration scripts | **Do not port.** This is what the application layer replaces. |

This is worth recording as an ADR in [`adr/`](./adr/), because the vocabulary decision in particular is hard to reverse once screening results depend on it.

---

## 2. mmore

A genuine external dependency, published as `mmore` 2.0.0 with modular extras (`process`, `index`, `rag`, `api`), so we install only what we use.

**What we take:** document extraction across PDFs, Office files, images and audio; hybrid dense+sparse indexing into Milvus; the retrieval layer beneath the agentic loop.

**What we do not take:** mmore's own FastAPI servers. We consume it as a library from inside `services/api`, behind our own interfaces. Running mmore's API alongside ours would create a second HTTP boundary with its own contract and its own authentication, outside the access checks and audit log this one enforces.

**Where it sits:** implementations only, inside the provider packages that wrap it (vector store, sanitisation, extraction). mmore types must not appear in `domain/` or `application/`.

**Open risk — the Boolean search branch.** The Boolean paper search we need is on a branch that has not been merged upstream. Options, in order of preference:

1. Contribute it upstream and depend on a released version
2. Pin a git revision in `pyproject.toml` until it merges
3. Fork, if upstream stalls

Pinning a git ref is acceptable short-term and must be recorded with a reason — an unpinned fork is how a project quietly acquires a dependency nobody maintains.

**Open risk — AI screening does not exist upstream.** Assume we write it, against our own `ScreeningProvider`. If it later lands upstream, the interface makes adopting it a swap rather than a rewrite.

---

## 3. AMMORE

Not a dependency. A design we reimplement.

AMMORE is an application rather than a package — absent from PyPI, and valuable to us for its architecture rather than its code:

```text
Planner    decomposes the question into 4-6 sub-queries
Retriever  searches the corpus, or the web as fallback
Critic     assesses coverage; approves or returns follow-up queries
Writer     synthesises with citations
```

That loop is the right shape for evidence synthesis. The framework underneath it, `autogen-agentchat`, is on PyPI and can be added directly.

The CHITCHAT prototype has since rebuilt the loop in plain Python, and that version is the one to follow. Its Critic reviews the Writer's draft rather than only the retrieval; every agent returns a schema through structured output; code checks the citations; and the output is a `DecisionBrief` ([architecture.md](architecture.md) §4.2) rather than free text.

**Explicitly rejected:**

- **Its OpenAI default.** AMMORE defaults to `gpt-4o-mini`. Sending a humanitarian team's question to a third-party API contradicts the core privacy commitment. Every model call goes through `InferenceProvider`, and the default must be one we can run ourselves.
- **Its Next.js + Chakra frontend.** We are on Vite + React in `apps/web`: a static SPA served by Caddy, with no server-side rendering to secure.
- **Tavily web-search fallback**, unless a user explicitly enables it. A silent fallback to live web search is undisclosed egress of the user's question.

---

## 4. Outside services and the libraries that reach them

Every request to another server goes through `HttpFetcher` (`app/net/`), scoped to the hosts the operator configured. Nothing is contacted by default: paper sources are listed in `HUMANI_T_PAPER_SOURCES`, and the model server is `HUMANI_T_INFERENCE_URL`.

### Paper sources

Facts checked against each source's documentation, or its live API where the documentation could not be read, on 2026-11-03.

| Source | Endpoint | Query | Rate limit we keep | Notes |
| --- | --- | --- | --- | --- |
| OpenAlex | `https://api.openalex.org/works` | `search.title_and_abstract` with AND/OR and quoted phrases | 1 request/s without a key, 5/s with one (searches with more than five Boolean operators) | Since February 2026 the `mailto` "polite pool" is gone and `mailto` is ignored. An API key (`HUMANI_T_OPENALEX_API_KEY`, sent as a bearer token) raises the daily budget from about $0.10 shared per IP to $1. Treat a key as required in production. |
| Europe PMC | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` | `TITLE_ABS:"…"` with AND/OR | 10 requests/s | The 10/s figure is from the service team on their support forum; the documentation pages refuse automated reads. An unknown field code matches nothing rather than failing, so field codes are fixed in code. |
| arXiv | `https://export.arxiv.org/api/query` | `ti:"…" OR abs:"…"` with AND/OR | One request every 3 s, one connection at a time | The limit covers every machine a deployment runs on. Metadata is CC0; PDFs keep their authors' licences (brief rule 18). |

**Google Scholar is not a source, and will not be.** It has no API: its help page answers bulk-access requests with "we're unable to provide bulk access", its `robots.txt` disallows `/scholar` and `/search` for every user agent, and Google's terms forbid automated access in violation of `robots.txt`. A scraper would break those terms and could stop working without notice, which a reproducible review cannot accept. The CHITCHAT prototype's fourth source is dropped; OpenAlex covers most of what Scholar indexes.

Pacing is per process. A deployment with several API or worker processes searching at once can exceed a source's limit; a shared limiter in Valkey is the fix when that happens.

### The model server

The default is Ollama, run by the operator: the compose service `http://ollama:11434`, or a remote server over https with a bearer token. We use its native `/api/chat` with `format` set to a JSON schema, because that is where structured output is documented; the OpenAI-compatible endpoint does not document the `json_schema` response format. Settings refuse known hosted model APIs (OpenAI, Anthropic, Google, ollama.com and others), and Ollama `*-cloud` models, which a signed-in Ollama daemon forwards to ollama.com, unless the operator sets `HUMANI_T_ALLOW_HOSTED_INFERENCE=true`.

### Libraries

| Library | Why |
| --- | --- |
| `httpx2` (runtime, was dev-only) | The maintained successor of httpx, from the same author under Pydantic's maintenance. Its connection layer, `httpcore2`, lets us replace the network backend, which is where the SSRF guard sits: the address is checked and the socket opened to it in one place, while TLS still verifies the host name. `aiohttp` and `urllib3` were considered; neither exposes connect-time hooks as cleanly. |
| `jsonschema` | Validates every model answer against the schema the caller asked for (draft 2020-12). Model servers constrain generation but do not promise conformance. The standard Python implementation; it pulls in `attrs`, `referencing`, `jsonschema-specifications` and `rpds-py`. `fastjsonschema` is lighter but generates and runs Python code from the schema. |
| `pypdf` | Reads the text of uploaded PDFs ([ADR-0009](adr/0009-local-extraction-in-an-isolated-process.md)). Pure Python, BSD-3-Clause, no runtime dependencies on Python 3.13, no network code, and it caps how far a compressed stream may inflate. It runs only inside the extraction child process. pdfminer.six is larger and slower for plain text; PyMuPDF wraps native MuPDF under AGPL. mmore is the later, broader extractor behind the same seam. |

---

## 5. What this changes in the build order

Generic plans for AI applications assume a chat product: chat, then conversations, then files, then RAG, then agents. For this project that order is inverted — retrieval is the product, not a later feature.

Security is the other inversion. The data is sensitive from the first upload, so accounts, sessions, access control, encryption and audit come before any feature that stores user data, not at the end.

Build order:

```text
[x] 1. Health endpoint, running locally
[ ] 2. Provider interfaces defined (all ten)
[~] 3. Fake implementations of each, no external services
        done: PaperSearchProvider
[x] 4. Vocabulary + BooleanQuery domain types
[~] 5. One PaperSearchProvider end to end (OpenAlex)
        providers built and wired into Providers.paper_sources;
        the project search route still to use them
[ ] 6. Quality gates, containers, CI (Docker Compose, self-hosted)
[ ] 7. Security core: accounts, sessions, MFA, projects and roles,
        envelope encryption, audit log, SSRF-guarded HTTP
        done: SSRF-guarded HTTP (app/net)
[ ] 8. Persistence: Review, PaperRecord, RunManifest
[x] 9. Remaining search providers behind the same interface
        Europe PMC and arXiv; Google Scholar dropped (section 4)
[ ] 10. Deduplication and full-text fetch
[ ] 11. Masking stage at ingest + SanitizationProvider at query time
[ ] 12. mmore extraction + indexing behind VectorStoreProvider
[ ] 13. ScreeningProvider with a real model
[ ] 14. Human review surface - the auditability requirement
[ ] 15. Own documents: uploads, malware scan, isolated worker
[ ] 16. Agentic loop for the decision brief (Planner/Retriever/Writer/Critic)
[ ] 17. LitLens-equivalent analytics, exports, PRISMA report
[ ] 18. Freeze the API and domain contracts

    OPEN - masking stage design. See architecture.md 3.9.
```

Step 5 is the first vertical slice worth aiming at. OpenAlex still answers without a key, within a small shared daily budget, so one provider can be proven end to end before any credential or paid service is involved; production should configure a free key (section 4).

Steps 2 and 3 stay early because fake implementations prove an interface is right while it is still free to change.
