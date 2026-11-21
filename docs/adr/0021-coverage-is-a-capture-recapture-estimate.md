# ADR-0021: Coverage is a capture-recapture estimate across paper sources, or none

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

UI_UX_SPEC §6.8 asks the screening results for "an estimate of how much of the relevant literature was found, always labelled 'estimate'". Undermind's white paper shows why such a number is uncertain: it is a model of what was not seen. humani-t makes no model call for it, and a reviewer must be able to see months later what the estimate was and how it was worked out.

What a run already stores: each `RunPaper`'s `found_by` (every source that returned it), each `RunSource`'s outcome (searched or failed), and each paper's screening label (the model's, or the label rules', ADR 0013). A deployment may search OpenAlex, Europe PMC and arXiv; each answers with at most `papers_per_source` of its best matches for the same Boolean query.

## Decision

- **Method `chapman-pairs`, version 1** (`app/domain/coverage.py`). Relevant papers are those labelled `include`, any priority. For each pair of searched sources A and B: n1 relevant papers A found, n2 B found, m both found. Chapman's estimate of all relevant papers is N = (n1+1)(n2+1)/(m+1) − 1. Its 95% range is Chao's (1987) log-normal interval: with the unseen part f0 = (n1−m)(n2−m)/(m+1) and variance V = (n1+1)(n2+1)(n1−m)(n2−m) / ((m+1)²(m+2)), the bounds are (n1+n2−m) + f0/C and (n1+n2−m) + f0·C, where C = exp(1.96·√ln(1 + V/f0²)). No bound or estimate is below the relevant papers found by all sources (R).
- **Coverage** is R divided by those bounds, shown only as a range of whole percentages, rounded outward to fives and at most 100. The point estimate is kept as `estimated_total` for provenance; the screen does not show it as a percentage.
- **The most cautious pair.** With three sources, every pair that qualifies gives a range; the one with the largest N (the lowest coverage) is used. Sources that index the same journals overlap more than chance, which makes every pair optimistic; the least optimistic one is the more honest choice.
- **No estimate, with a reason**, whenever the data cannot support one: `screening_unfinished` (some of the run's papers are not screened yet), `one_source` (fewer than two sources answered; a failed source found nothing), `too_few_overlaps` (no pair shares at least 7 relevant papers: Robson and Regier's rule of thumb for the estimator's bias), `sources_nested` (each pair that shares enough has one source whose relevant papers the other found too). A nested pair gives N equal to the larger source and a range of zero width; that almost always means one source holds the other's records, not that nothing was missed.
- **Where it lives.** The Screen stage records the estimate under `coverage` in its manifest facts when it finishes, from the model's labels: method, version, R, the searched sources, every pair's counts, and the range or the reason. Nothing new is stored. The papers list (`GET …/papers`) works out the same estimate on read from the labels the table shows, naming the rules version, so the estimate always agrees with the counts beside it. Until rules change, the two are the same.

## Consequences

- Reproducible: the counts it used are in the response and the manifest, the formula is above, and a change to it is a new `method_version`, never a silent edit.
- Most runs on a default deployment get an estimate only when OpenAlex and Europe PMC both find seven or more of the same relevant papers. arXiv rarely overlaps with health literature, so its pairs usually do not qualify and are left out. Demo mode has one source and never gives an estimate.
- Cost: counting over the run's papers on every list request, with no decryption; negligible next to opening the rows.

### Assumptions and limits, said in the UI and here

- **Same query, same reach.** Every source is asked the same Boolean query and returns only its best `papers_per_source`. The estimate is of the relevant papers these searches could reach, not of everything published: papers no source indexes, or that the query's words miss, are invisible to it.
- **Independence.** Capture-recapture assumes a paper's chance of being found by one source does not depend on the other. OpenAlex imports PubMed records, and both rank by similar signals, so the sources are positively dependent and the estimate is too high. The UI says the top of the range is likely too high.
- **Equal catchability.** Some relevant papers are much easier to find than others (clear titles, English abstracts). That heterogeneity also makes the estimate too high.
- **Labels are judgements.** Relevant means what the model, or the rules, labelled `include`, read from abstracts only. Skipped and failed papers have no label and are not counted.
- **Duplicates.** A preprint and its published version may not merge (`paper.py`), which can lower the overlap and make the estimate too low.

## Alternatives rejected

- **A model's judgement of coverage:** not reproducible, costs a call, and would sound more certain than it is.
- **A log-linear model over three sources:** handles dependence better, but needs many more overlapping papers than a run has, and is far harder to explain or check by hand.
- **The two largest sources, or a fixed pair:** arbitrary, and silently optimistic whenever the chosen pair overlaps most.
- **A point percentage:** suggests precision the data cannot support.
- **Storing the estimate in its own table:** the manifest already holds what each stage recorded; a second store would need its own sealing and deletion.
