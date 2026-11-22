# 17 — P3: compare versions and runs

> **Milestones:** P3 (UI_UX_SPEC §15) · **Status:** done · **Previous:** [17 — P3 history: Needs you, activity, timeline, seen and saved](17-p3-history.md) · **Next:** [17 — P3: trade-off priorities and "Your view first"](17-p3-brief.md)

A reviewer can open any earlier version of a review's brief, see what changed against another version (options added or removed, evidence strength changes, sources added or removed), and put two runs of a review side by side: setup, discovery, screening and brief. Every comparison is computed by the API from what was stored when each brief and run was made ([UI_UX_SPEC §6.9 Versions, §7.4](../UI_UX_SPEC.md), [`architecture.md` §4.2](../architecture.md#42-reviews)).

## What this phase delivers

- `GET /api/v1/reviews/{review_id}/brief/versions`: every version, newest first, the latest marked.
- `GET /api/v1/briefs/{brief_id}/compare/{other_brief_id}`: what changed between two versions, older to newer.
- `GET /api/v1/runs/{run_id}/compare/{other_run_id}`: two runs of one review, part by part, or why they cannot be compared.
- `GET /api/v1/runs/{run_id}/manifest` already existed (walkthrough 11) and is unchanged, except that `model_ids` now includes the Brief stage's per-role models.
- Web: a version menu and "Compare with" in the brief's header; a Compare runs canvas at `/p/:project/r/:review/compare?a=&b=`; the "Compare runs: [Run 3] with [Run 1] [Compare]" picker at the foot of each run's canvas.
- No table and no migration: briefs were already one `decision_briefs` row per run, numbered by version within the review (migration 0011).

## Architecture choices and why

### D17.1 No migration for brief versions

- **Decision:** versions are the existing `decision_briefs` rows; the repository gains `list_for_review`.
- **Why:** each run of the Brief stage already stored its own sealed row with `version` (unique per review), so every brief stored before P3 already has its number: the first is version 1.
- **Rejected:** a `brief_versions` table, which would copy sealed content the existing rows hold.
- **Where:** [`storage/postgres/briefs.py`](../../services/api/app/storage/postgres/briefs.py), [`application/comparisons.py`](../../services/api/app/application/comparisons.py).
- **Proven by:** `tests/test_briefs_storage_contract.py::TestBriefStorage::test_a_reviews_briefs_are_listed_newest_version_first`.

### D17.2 Comparisons read older to newer, and are never stored

- **Decision:** both comparisons sort their two sides (version, or the run's place in the review) and report `before` and `after`; nothing is written.
- **Why:** "Cash: Weak → Moderate" must read the same whichever version the reader opened first; a stored comparison would be a second record to shred when a project is deleted.
- **Rejected:** comparing in the browser (the browser is not trusted, and would need both briefs' evidence).
- **Proven by:** `tests/test_comparisons_api.py::TestCompareBriefVersions::test_the_order_of_the_ids_does_not_change_the_direction`.

### D17.3 What matches across versions and runs

- **Decision:** options match by name ignoring case and spacing; sources and papers match by paper id, never by tag; a stage's own settings are compared only when both runs finished that stage.
- **Rejected:** fuzzy option matching (a wrong match hides a real change).
- **Proven by:** `tests/test_comparisons_domain.py`.

## Files to read, in order

1. [`app/domain/comparisons.py`](../../services/api/app/domain/comparisons.py) — the rules, no I/O.
2. [`app/application/comparisons.py`](../../services/api/app/application/comparisons.py) — access, ordering, and when a run cannot be compared.
3. [`app/api/routes/comparisons.py`](../../services/api/app/api/routes/comparisons.py) — the three routes.
4. [`apps/web/src/features/briefs/BriefVersions.tsx`](../../apps/web/src/features/briefs/BriefVersions.tsx) and [`features/reviews/CompareRunsCanvas.tsx`](../../apps/web/src/features/reviews/CompareRunsCanvas.tsx).

## Set up

Nothing beyond [00 — Setup](00-setup.md). You need one review with at least two finished runs that each wrote a brief: in the workspace, run the review, then "Run again" from the Brief stage.

## Manual tests

Sign in as a member in Postman (walkthrough 13). `{{review}}`, `{{brief1}}`, `{{brief2}}`, `{{run1}}`, `{{run2}}` are ids from the responses.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T17.1 | `GET /api/v1/reviews/{{review}}/brief/versions` | `200`, newest first, only the first has `"is_latest": true` | D17.1 | Check the repository's ordering |
| T17.2 | `GET /api/v1/reviews/{{review}}/brief` | `200`, the highest `version` | latest unchanged | The route's default changed |
| T17.3 | `GET /api/v1/briefs/{{brief2}}/compare/{{brief1}}` | `200`, `before.version` is the lower one | D17.2 | Sorting in `ComparisonService.compare_briefs` |
| T17.4 | The same with `{{brief1}}` twice | `422` | input check | |
| T17.5 | `GET /api/v1/runs/{{run1}}/compare/{{run2}}` | `200`, four parts, `cannot_compare: null` | §7.4 | |
| T17.6 | T17.5 signed in as a non-member | `404`, same body as for a made-up id | access | `require_member` in the service |
| T17.7 | Browser: open the brief, choose "Compare with" → Version 1 | "Changes since version 1" lists the changes, or says nothing changed | §6.9 | Network tab: the compare call |
| T17.8 | Browser: open a run, use "Compare runs" → Compare | The Compare runs canvas shows Setup, Discovery, Screening and Brief | §7.4 | The `a` and `b` in the address |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T17.1–T17.8 | ☐ | |

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Comparison rules | `tests/test_comparisons_domain.py` |
| Routes, access, ordering, no-record message | `tests/test_comparisons_api.py` |
| Listing versions in both stores | `tests/test_briefs_storage_contract.py` |
| Version menu and compare panel | `apps/web/src/features/briefs/BriefVersions.test.tsx` |
| Picker and Compare runs canvas | `apps/web/src/features/reviews/CompareRuns.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_comparisons_domain.py tests/test_comparisons_api.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G17.1 | A renamed option shows as one removed and one added | The reader matches them by eye | Accepted (D17.3) |
| G17.2 | Closed: the project timeline hosts `RunComparePicker` for its latest review | – | – |

## Glossary

| Term | Meaning |
| --- | --- |
| **Version** | One brief, written by one run's Brief stage; numbered from 1 within the review. |
| **Record of its setup** | Any fact a run's stages wrote into its manifest; without one, a run cannot be compared. |
