# ADR-0006: Papers are kept once per review under a keyed digest; decisions belong to the run that made them

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

The pipeline finds papers, keeps their text, and screens them ([walkthrough 11](../walkthroughs/11-screening.md)). Three choices about how this is stored are expensive to change once real reviews hold data:

1. **How a paper is found again.** A re-run must recognise the papers it already has, or every count and every comparison between runs is wrong. The natural key is the DOI, or the normalised title and year. Stored in the clear, that key tells anyone with a database copy what the review is about.
2. **Who owns a decision.** A decision could belong to the paper (re-used by later runs) or to the run that made it.
3. **Whether a decision may change.** Relabelling is coming (UI_UX_SPEC §6.8, P2).

## Decision

- A paper is stored once per review. It is found again by `identity_digest`: HMAC-SHA256 of `PaperRecord.identity` under the subkey `papers.identity` of the project's data key. The record itself is sealed under the project's key and bound to its row. Uniqueness is `(review_id, identity_digest)`.
- A run works from a **paper set**: its own discovery, or (for a run started without Discover) the review's newest earlier one. A paper's tag is its place in that set (`P12`).
- A `ScreeningDecision` belongs to one run and one paper, keyed `(run_id, paper_id)`. It is written once and never updated: the app role has no `UPDATE` on `screening_decisions`. A future relabel is a new record that names the decision it overrides, not an edit.

## Consequences

- Changing how identity is computed (for example adding fuzzy matching) changes every digest. Old and new papers would no longer match, so it needs a migration that decrypts every record and recomputes its digest under the project's key, project by project.
- Destroying a project's key makes both the records and the digests meaningless, so crypto-shredding covers papers with no extra work.
- A retried run re-screens its papers, which costs model time; reusing a decision when every input is unchanged is possible later without changing this decision.
- Relabelling must be designed as an append (a new record), which keeps the original decision as evidence of what the model said.

## Alternatives rejected

- **DOI or title in the clear with a unique index:** reveals the topic of every review to anyone with a database copy.
- **One paper table shared across projects:** crosses the project's boundaries for access, encryption and deletion.
- **Decisions per paper, re-used by every run:** a run could no longer say what it decided, and a change to masking or criteria would leave stale decisions in place without anyone noticing.
- **Updatable decisions:** the model's original answer would be lost the first time someone relabels.
