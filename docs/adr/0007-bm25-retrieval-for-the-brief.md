# ADR-0007: The decision brief retrieves passages with in-process BM25, behind a `Retriever` seam

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

The agent loop that writes a `DecisionBrief` needs to search a run's evidence: the planner's sub-questions first, then whatever the Critic asks for. [`architecture.md` §3.4](../architecture.md#34-planned-not-built) names Milvus, through mmore, as the vector store. In P1 the evidence is the masked abstracts of a run's included papers: a few dozen to a few hundred passages per run.

A vector store brings a new service to deploy and secure, an embedding model to run, and stored vectors that must be rebuilt whenever masking changes the text. It also makes a search harder to reproduce: the ranking depends on the embedding model and its version.

## Decision

- Retrieval is a `Retriever` Protocol (`app/briefs/base.py`): `search(corpus, query, limit) -> tuple[Passage, ...]`, with a `retriever_id` recorded in the run manifest.
- The P1 implementation is Okapi BM25 (`k1 = 1.5`, `b = 0.75`) in process (`app/briefs/bm25.py`). The index is built from the run's passages for each search and nothing is kept between searches. Ties keep corpus order, so the same inputs always give the same ranking.
- The corpus is exactly the run's included passages from the pipeline's `EvidenceSource`, already masked. No vector, index or copy of the text is stored anywhere else.

## Consequences

- No new service, no embedding model and nothing to keep in step with masking: a re-masked paper is searched as it now reads.
- Matching is lexical. "cash" does not find "money", and there is no stemming. The Planner and the Critic ask several phrasings, which recovers part of that.
- Building the index per search is linear in the corpus. At a few hundred abstracts that is milliseconds; at tens of thousands of full-text chunks it is not, and that is when the vector store is needed.
- Replacing it is a new class behind the same Protocol, chosen in `api/dependencies.py`. Briefs already written keep the retriever id they were made with.

## Alternatives rejected

- **Milvus through mmore now:** a service, a model and stored vectors for a corpus that fits in memory, before full text or uploads exist.
- **Embeddings in Postgres (pgvector):** still an embedding model and stored vectors to rebuild after masking, for no gain at this size.
- **Letting the model choose passages from the whole corpus:** the prompt would hold every abstract, which a local model's context cannot take, and "which passages were retrieved" would stop being something code can check.
