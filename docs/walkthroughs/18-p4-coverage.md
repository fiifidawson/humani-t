# 18 — P4: coverage estimate

> **Milestones:** P4 (UI_UX_SPEC §15) · **Status:** done · **Previous:** [18 — P4 retention: the period, "Deleted in N days unless kept", Keep](18-p4-retention.md) · **Next:** [18 — P4: field](18-p4-field.md)

A reviewer reading a run's papers can see roughly what share of the relevant papers the searches found, as a range that is always called an estimate, with what it means and does not, or a plain "no estimate" and why. It is worked out by the API from what the run already stores, with no model call, and the Screen stage records it in the run's manifest ([UI_UX_SPEC §6.8](../UI_UX_SPEC.md#68-screening-results), [ADR-0021](../adr/0021-coverage-is-a-capture-recapture-estimate.md), [`architecture.md` §4.2](../architecture.md#42-reviews)).

## What this phase delivers

- `GET /api/v1/reviews/{review_id}/papers` gains `coverage`: method and version, the labels' rules version, relevant papers found, the sources that answered, each pair's counts, and a 95% range in whole fives, or `no_estimate_because` (`screening_unfinished`, `one_source`, `too_few_overlaps`, `sources_nested`).
- The Screen stage's manifest facts gain `coverage`, the estimate from the model's labels when screening finished (`GET /api/v1/runs/{run_id}/manifest`, `stage_facts`).
- Web: a "Coverage estimate" box below the PRISMA flow, with "What this estimate means".
- No new endpoint, table, migration or setting.

## Architecture choices and why

### D18c.1 Capture-recapture across sources, the most cautious pair

- **Decision:** Chapman's estimator for each pair of searched sources over the papers labelled included, with Chao's log-normal 95% range; the pair suggesting the most papers missed sets the range; coverage is shown only as a range rounded outward to fives.
- **Why:** the run already knows which sources found each paper (`RunPaper.found_by`), so the overlap is free, reproducible and checkable by hand. Sources share records, which makes every pair optimistic; the least optimistic pair is the honest choice.
- **Rejected:** a model's judgement (not reproducible); a log-linear model over three sources (needs far more overlap than a run has); a point percentage (precision the data cannot support).
- **Where:** [`domain/coverage.py`](../../services/api/app/domain/coverage.py), `coverage_of` in [`application/papers.py`](../../services/api/app/application/papers.py).
- **Proven by:** `tests/test_coverage_domain.py::TestThreeSources`, `::TestAnEstimate`.

### D18c.2 No estimate rather than a made-up one

- **Decision:** no range when screening has not finished, fewer than two sources answered, no pair shares at least 7 relevant papers, or each pair that does is nested (one source found nothing relevant the other did not).
- **Why:** each of these gives a number that looks precise and means nothing; a nested pair gives a range of zero width at 100%.
- **Rejected:** showing the number with a warning (people read the number, not the warning).
- **Proven by:** `tests/test_coverage_domain.py::TestNoEstimate`, `tests/test_coverage_api.py::TestNoEstimate`.

### D18c.3 Recorded at screening, worked out again on read

- **Decision:** the Screen stage records the estimate from the model's labels in its manifest; the papers list works it out from the labels it shows, naming the rules version.
- **Why:** the record says what it was when the decisions were made; the table's estimate always agrees with the counts beside it, even after the label rules change (ADR 0013).
- **Rejected:** showing only the recorded one (it would disagree with the table once rules change); a table of estimates (the manifest already holds stage facts).
- **Proven by:** `tests/test_coverage_api.py::TestRecordedInTheManifest`, `::TestOnTheScreeningTable::test_the_estimate_follows_the_label_rules_the_table_shows`.

## Files to read, in order

Domain rule first, because everything else only passes it along.

1. [`services/api/app/domain/coverage.py`](../../services/api/app/domain/coverage.py) — the estimator, the reasons, and the facts recorded.
2. [`services/api/app/application/papers.py`](../../services/api/app/application/papers.py) — `coverage_of`: included papers, searched sources; `list_papers` passes the labels shown.
3. [`services/api/app/application/screening.py`](../../services/api/app/application/screening.py) — the `coverage` manifest fact.
4. [`services/api/app/api/schemas/papers.py`](../../services/api/app/api/schemas/papers.py) — `CoverageOut`.
5. [`apps/web/src/features/reviews/Coverage.tsx`](../../apps/web/src/features/reviews/Coverage.tsx) — the words shown for each case.

## Set up

Nothing beyond [00 — Setup](00-setup.md). For an estimate the deployment must search at least two sources; the default `HUMANI_T_PAPER_SOURCES=openalex,europepmc,arxiv` does.

## Manual tests

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T18c.1 | Run a review with all stages on a health question (for example cash transfers and child malnutrition in displacement), then open Papers. | Below the PRISMA flow, "Coverage estimate" with either "Estimate: this run found between X% and Y% …" (X and Y multiples of 5) or "No coverage estimate for this run." and a reason. | D18c.1, D18c.2 | Look at `coverage` in `GET /api/v1/reviews/{review_id}/papers` in Postman. |
| T18c.2 | In Postman, `GET http://127.0.0.1:8731/api/v1/reviews/{review_id}/papers` with your session cookie. | `coverage.overlaps` lists each pair of `searched_sources` with `first_found`, `second_found`, `both_found`; exactly one of `interval` and `no_estimate_because` is set. | D18c.1 | A source missing from `searched_sources` failed in Discover: see the run's log. |
| T18c.3 | Check the numbers by hand: with the pair in `interval.sources`, compute (n1+1)(n2+1)/(m+1) − 1. | It rounds to `interval.estimated_total` (or equals `relevant_found` when that is larger). | D18c.1 | The formula in ADR-0021 and the code disagree: a bug. |
| T18c.4 | Open "What this estimate means". | The counts ("Of the N relevant papers this run found, M were found by both …"), what it leaves out, "the top of the range is likely too high", whose labels it counted, and "Method: capture–recapture between two sources (Chapman), version 1." | D18c.1 | — |
| T18c.5 | `GET http://127.0.0.1:8731/api/v1/runs/{run_id}/manifest`. | The `screen` entry of `stage_facts` has `facts.coverage` with the same method, version and counts. | D18c.3 | The run was screened before this change. |
| T18c.6 | Edit rules so more papers count (Possibly relevant needs 1 criterion), Apply, reload Papers. | `coverage.rules_version` is the new version and `relevant_found` matches the Included count; the manifest's `coverage` is unchanged. | D18c.3 | — |
| T18c.7 | Start the API in demo mode (`HUMANI_T_PAPER_SOURCES=demo`) and run a review. | "No coverage estimate for this run. Only one source was searched …" | D18c.2 | — |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T18c.1 | ☐ | |
| T18c.2 | ☐ | |
| T18c.3 | ☐ | |
| T18c.4 | ☐ | |
| T18c.5 | ☐ | |
| T18c.6 | ☐ | |
| T18c.7 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The estimator, its rounding, the cautious pair and every reason for none | `tests/test_coverage_domain.py` |
| The papers list carries it, under the labels shown | `tests/test_coverage_api.py::TestOnTheScreeningTable` |
| No estimate for one source, a failed source, too little overlap, unfinished screening | `tests/test_coverage_api.py::TestNoEstimate` |
| The Screen stage records it, and the record stays when rules change | `tests/test_coverage_api.py::TestRecordedInTheManifest` |
| The screen shows a range called an estimate, what it means, or why there is none | `apps/web/src/features/reviews/Coverage.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_coverage_domain.py tests/test_coverage_api.py -v
cd ..\..\apps\web
pnpm exec vitest run src/features/reviews/Coverage.test.tsx
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G18c.1 | Sources are not independent (OpenAlex imports PubMed records), and easy-to-find papers are found by all. | The range is likely too high; the UI says so. | Accepted (ADR-0021). A different method is a new `method_version`. |
| G18c.2 | Skipped and failed papers have no label and are not counted. | Fewer relevant papers to compare; more runs with no estimate. | Accepted. |
| G18c.3 | The estimate counts only what this run's query could reach in each source's top `papers_per_source`. | It is not coverage of all the literature; the UI says so. | Accepted. |

## Glossary

| Term | Meaning |
| --- | --- |
| **Capture-recapture** | Estimating a total from two independent catches: the more of the first catch the second catches again, the fewer were missed by both. |
| **Chapman's estimator** | (n1+1)(n2+1)/(m+1) − 1: the total from two catches of n1 and n2 with m in both, less biased than n1·n2/m for small counts. |
| **Nested sources** | Two sources where one found nothing relevant the other did not, so their overlap says nothing about what both missed. |
