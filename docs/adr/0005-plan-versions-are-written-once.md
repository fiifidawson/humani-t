# ADR-0005: A review's plan is a list of versions written once, vocabulary and criteria together

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

A run must be reproducible from what it recorded ([architecture §4.3](../architecture.md#43-how-a-vocabulary-is-acquired)): which terms were searched and which criteria screened the papers. The plan changes over a conversation (clarifying questions, drafts, change requests) and can change again after a run. A reviewer must be able to tell, months later, which version a run used, who wrote each term, and what was asked to produce it. Two people may accept the same draft at the same moment (UI_UX_SPEC §11.2).

## Decision

**One row per version, written once.** `plan_versions` is keyed by `(review_id, version)`. A new version is an insert; two writers of "the next version" collide on the key and the second gets `409`. The content (vocabulary, criteria, notes, the change request and clarifying answers it came from, the diff against the previous version, the drafting model and prompt version) is sealed under the project's key and bound to its row. Origin and author stay in the clear so the schema can refuse a user-supplied version written by the model.

**Accepting is the only update.** It is one conditional `UPDATE`: only if the version is the latest and not yet accepted. A trigger refuses every other change, including any change to an accepted row. Runs use the latest accepted version; a newer draft does not replace it until it is accepted itself.

**Vocabulary and criteria share the version number.** A plan version is the unit a person accepts, so `vocabulary_version` and `criteria_version` in a `RunManifest` are both the plan's version.

**Authorship is recorded per version.** Who added a term is the author of the version that introduced it, derived from recorded authors. Free text is never matched to guess it.

## Alternatives considered

- **One mutable plan row with a version counter.** Simpler, but an accepted plan could be overwritten by a bug or a race, and the history a reviewer needs would be gone.
- **Separate version numbers for vocabulary and criteria.** More precise, but two numbers to accept, record and compare, for a distinction no screen needs. The diff already shows which part changed.
- **Inferring per-term authorship from the change request's text.** It would show "you" on the term a person asked for, but it guesses, and a guess presented as provenance is worse than none.
- **Storing only the diff, or only the versions.** Versions alone make the diff derivable, but storing it as decided keeps "what changed" exactly as it was shown when the version was made.

## Consequences

- Every change makes a row; a long conversation makes many small rows. Acceptable at planning scale.
- A change request that only edits criteria still bumps the vocabulary version.
- A term a person asked the model to add shows as added by the model; only terms a person wrote themselves (an upload, an edit at the gate) show as theirs.
- Rewriting history needs the migrator role and dropping the trigger; the app role cannot.
