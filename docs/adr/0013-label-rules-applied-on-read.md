# ADR-0013: Label rules are versioned and applied when labels are read

- **Status:** Accepted
- **Date:** 2026-11-12
- **Replaces:** the relabelling plan in [ADR-0006](0006-papers-per-review-decisions-per-run.md) ("a future relabel is a new record that names the decision it overrides")

## Context

UI_UX_SPEC §6.8 lets owners and reviewers change the rules that turn the model's per-criterion answers into a label ("4 would move from Relevant to Highly relevant"), with no new model call. ADR-0006 planned a relabel as one new record per paper, naming the decision it overrides. Since then every decision stores its answer to each criterion (`yes`, `partly`, `no`, `unclear`), so a label can be computed from what is already stored.

## Decision

- A review's rules are a `LabelRules` value: how many inclusion criteria Possibly relevant needs, and what an `unclear` answer counts as (met, not met, or uncertain, which asks a person).
- Each apply inserts a `LabelRulesVersion` (`label_rule_versions`: review, version, the rules, who, when, papers moved). The runtime role has no `UPDATE` on it, as for `screening_decisions`.
- A paper's label is computed when it is read: the stored `ScreeningDecision` plus the review's latest rules version, by `label_under` in `app/domain/label_rules.py`. The decision itself is never changed.
- Decisions the rules cannot reach keep the model's label: no per-criterion answers, an unreadable answer, or answers that do not match the criteria of their plan version. Skipped and failed papers have no decision to relabel.
- With no rules version stored, every label is the model's, exactly as before.

## Consequences

- One small row per apply, instead of one row per paper per relabel. A versioned rule set plus the stored answers reproduces every label any version ever showed, so nothing is lost by not writing per-paper records.
- The papers list, its tier filters and counts, PRISMA and the downloads use the label the latest rules give. Each decision says what set its label (`label_set_by`: `model` or `rules`, with `rules_version`), so the screen and the dataset can say so honestly.
- An existing brief keeps the labels it was made from: its evidence was stored when it was written. Briefs and answers written later read the labels the latest rules give (`ScreenedEvidence`), so a brief never cites a paper the papers list shows as excluded.
- Reading labels under rules opens each decision's sealed answers, so it costs one decryption per decision on every list. A run's few hundred decisions make that acceptable; caching is possible later without changing this decision.
- Changing what `label_under` does changes every label shown under existing rules. Such a change needs its own rules version semantics (a new field, say), never a silent edit.

## Alternatives rejected

- **A relabel record per paper (ADR-0006's plan):** many rows for one decision a person made, and the rule that produced them would be lost unless stored as well.
- **Updating the decision's label:** loses what the model said, which ADR-0006 exists to keep.
- **Rules in the browser:** the browser is not trusted, and exports and PRISMA must agree with the screen.
