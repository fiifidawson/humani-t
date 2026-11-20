# 17 — P3 history: Needs you, activity, timeline, seen and saved

> **Milestones:** P3 (UI_UX_SPEC §15) · **Status:** in progress · **Previous:** [17 — P3 organise](17-p3-organise.md) · **Next:** [17 — P3 compare](17-p3-compare.md)

A person coming back after a week sees what waits for them and what happened, on Home and in each project's history, and the papers they already read are marked wherever they appear ([UI_UX_SPEC §7.1, §7.4, §7.5](../UI_UX_SPEC.md#7-history-and-project-grouping)). Everything is derived from records the pipeline already keeps; only dismissals and marks are stored ([ADR-0017](../adr/0017-history-is-derived-and-marks-die-with-their-project.md)).

## What this phase delivers

- **Needs you** on Home and as a count in the rail: open gates, drafts waiting for accept, failed runs, unread briefs. One action per row, and Dismiss, which hides an item until it changes.
- **Recent activity** on Home, grouped Today, Yesterday, This week, then month names.
- **History** at `/p/:project/history`, linked from the workspace header: lines grouped by review, runs open to their stages, the model on run and brief lines, and "Show:" filters remembered in this browser for each person.
- **Seen and saved marks:** opening a paper row or a brief's source marks the paper seen, wherever the same paper appears; ☆ saves it; **Saved papers** in the rail lists them.
- Endpoints: `GET /needs-you`, `POST /needs-you/dismissals`, `GET /activity`, `GET /projects/{id}/history`, `GET /projects/{id}/paper-marks`, `PUT /projects/{id}/papers/{paper_id}/seen`, `PUT`/`DELETE …/saved`, `GET /saved-papers`, `PUT /briefs/{id}/seen` ([api.md](../api.md#history-and-marks)).
- Migration `0019`: tables `paper_marks`, `brief_reads`, `needs_you_dismissals`.

## Architecture choices and why

### D17.1 Derived, not logged

- **Decision:** lines and items are built on each read from reviews, plans, runs and their logs, gates, briefs, questions, documents and label rules.
- **Why:** nothing can drift from what the pipeline recorded, and a deleted project's lines go with its key.
- **Rejected:** an activity table written beside each action; the audit log (ids only, no project index).
- **Where:** [`app/application/history.py`](../../services/api/app/application/history.py), [`app/domain/history.py`](../../services/api/app/domain/history.py).
- **Proven by:** `tests/test_history_api.py`.

### D17.2 Papers matched by the dedup key, sealed under their project

- **Decision:** a mark keeps `PaperRecord.identity` (DOI, else title and year) sealed under its paper's project key; another project finds it through its own identity digest.
- **Why:** the same paper is recognised anywhere the person is a member, and the mark is unreadable once its project is deleted.
- **Where:** [`app/application/marks.py`](../../services/api/app/application/marks.py).
- **Proven by:** `tests/test_marks_api.py::TestSeen`.

## Manual tests

Sign in as an owner with a project that has a run and a brief (walkthrough 12).

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T17.1 | Open Home | "Needs you (1)" with "Brief v1 ready to read" and **Read**; the rail's Home shows 1 | D17.1 |
| T17.2 | Click **Read**, then go back Home | The brief opened; the item is gone | D17.1 |
| T17.3 | Start a run that fails (stop Ollama first), then open Home and click **Dismiss** on it | The row leaves; start and fail another run and a new row appears | D17.1 |
| T17.4 | Open the project, click **History** | Lines grouped by review; the run line names the model; the arrow shows its stages | D17.1 |
| T17.5 | Turn off **Runs** under "Show:", reload the page | Run lines stay hidden | §7.4 |
| T17.6 | In the papers canvas, open a row; open a second project with the same paper | "Seen" on the row, and "in 1 other project" naming the first | D17.2 |
| T17.7 | Click ☆ on a row, then **Saved papers** in the rail | The paper is listed with its project; **Remove** takes it off | §7.5 |
| T17.8 | In Postman, `GET /api/v1/projects/<id>/history` as a non-member | `404`, the same body as for a missing project | access |

```powershell
cd services\api
uv run pytest tests/test_history_api.py tests/test_marks_api.py tests/test_history_domain.py -v
```

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T17.1–T17.8 | ☐ | |

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G17.1 | No "Notes" filter | The spec lists one; there are no notes yet | When notes exist |
| G17.2 | Seen marks cover papers, not document passages | A team document opened in the source panel is not marked | If asked for |
| G17.3 | Closed: the timeline ends with the Compare runs picker for its latest review | – | – |
