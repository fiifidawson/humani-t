# 17 — P3: trade-off priorities and "Your view first"

> **Milestones:** P3 · **Status:** in progress · **Previous:** [16 — Documents](16-documents.md) · **Next:** `the other P3 lanes`

A reader of a decision brief can now say what matters most to them and see the trade-off matrix reordered and highlighted, with no scores invented ([UI_UX_SPEC §6.9](../UI_UX_SPEC.md#69-decision-brief)). An owner can also turn on "Your view first": each member is then asked which option they lean towards before reading the brief, and only they see their answer.

## What this phase delivers

- "What matters most to you?" picks one or more criteria; picked rows move to the top with a highlight bar and the word "priority"; the matching option card gets an outline and the consideration shows above the matrix. Humanitarian criteria are listed first, in the picker and in the matrix.
- "Show humanitarian criteria only", and a sort: "Your priorities first" or "Original order".
- On a narrow screen (below 768 px) the matrix becomes one card per criterion, with the options inside.
- The picks are kept in the address (`?priorities=[0,1]`, positions in the brief's list of criteria, never their names), not in the API.
- `PUT /api/v1/projects/{project_id}/view-first` (owner) and `is_view_first_on` on `ProjectOut`, off by default.
- `GET` and `PUT /api/v1/runs/{run_id}/leaning`: your own answer for one run's brief, written once.

### Data model

| Table or column | What it holds |
| --- | --- |
| `projects.is_view_first_on` | The owner's switch, `false` by default and for existing projects. |
| `leanings` | One row per run and member: `option_sealed` (the option, or `null` for "Not sure", sealed under the project's key, bound to `run_id:user_id`) and `answered_at`. The app role may not update it. |

Migration: `20261119_0020_your_view_first.py`.

## Architecture choices and why

### D17.1 A leaning is keyed by run, written once

- **Decision:** a member's answer is stored per run that wrote the brief and per member; a second answer is `409`.
- **Why:** an answer "before reading" is worth nothing once it can be rewritten after reading. The run id names the brief even while brief versions are being built.
- **Rejected:** keyed by brief id (brief versions may change what a brief id means); editable answers (defeats the purpose).
- **Where:** [`application/leanings.py`](../../services/api/app/application/leanings.py), [`domain/leanings.py`](../../services/api/app/domain/leanings.py).
- **Proven by:** `tests/test_leanings_api.py::TestAnswering`.

### D17.2 Priorities are a view preference

- **Decision:** the picks live in the URL as positions; the API never sees them.
- **Why:** they change how one person reads a brief, not the brief. Positions keep project text out of the address and the proxy's logs.
- **Where:** [`BriefCanvas.tsx`](../../apps/web/src/features/briefs/BriefCanvas.tsx), [`router.tsx`](../../apps/web/src/app/router.tsx).

## Manual tests

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T17.1 | Open a brief, tick "Cost to deliver" under "What matters most to you?" | Its row moves to the top with a bar and "priority"; the address gains `priorities=` with a number, not the name | D17.2 | `BriefView` in `BriefCanvas.tsx` |
| T17.2 | Choose "Original order" | The row returns to its place and keeps "priority" | D17.2 | `orderedRows` |
| T17.3 | Narrow the window below 768 px | One card per criterion, options inside | §6.9 narrow screens | `useMediaQuery` in `TradeOffs` |
| T17.4 | As owner, Project settings → Briefs → turn on the switch | The switch stays on after a reload | D17.1 | `PUT …/view-first` in the network tab |
| T17.5 | Open the brief, click "Skip" | The brief shows; reopening asks again; nothing in the network tab but `GET …/leaning` | §6.9 skippable | `BriefCanvas` |
| T17.6 | Open the brief, click "Cash transfers" | The header shows "You leaned towards Cash transfers before reading."; another member signed in elsewhere is still asked | D17.1 | `GET /api/v1/runs/{run_id}/leaning` as each user |
| T17.7 | In Postman, `PUT /api/v1/runs/{run_id}/leaning` with `{"option": "Vouchers"}` again | `409` | D17.1 | `LeaningService.record` |

In a database client, `SELECT run_id, user_id, answered_at, length(option_sealed) FROM leanings;` shows ciphertext only.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T17.1 | ☐ | |
| T17.2 | ☐ | |
| T17.3 | ☐ | |
| T17.4 | ☐ | |
| T17.5 | ☐ | |
| T17.6 | ☐ | |
| T17.7 | ☐ | |

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The switch is owner-only and off by default | `tests/test_leanings_api.py::TestTheProjectSetting` |
| One answer per member and run, seen only by them | `tests/test_leanings_api.py::TestAnswering` |
| Sealed, bound to its row, shredded with the project | `tests/test_leanings_api.py::TestStoredSafely` |
| Priorities, sort, narrow cards, the question | `apps/web/src/features/briefs/Brief.test.tsx` |
| The settings switch | `apps/web/src/features/projects/ViewFirstSwitch.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_leanings_api.py tests/test_leanings_domain.py tests/test_leanings_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G17.1 | The New project form has no "Your view first" box (§6.3) | Owners turn it on in Project settings after creating the project | Add with the form's "More options" |

## Glossary

| Term | Meaning |
| --- | --- |
| **Leaning** | The option a member said they leaned towards before reading a brief, or "Not sure". |
