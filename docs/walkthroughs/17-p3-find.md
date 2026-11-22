# 17 — P3: find (search, the command palette and the keyboard)

> **Milestones:** P3 (UI_UX_SPEC §15) · **Status:** done · **Previous:** [17 — P3: trade-off priorities and "Your view first"](17-p3-brief.md) · **Next:** [18 — P4 retention: the period, "Deleted in N days unless kept", Keep](18-p4-retention.md)

Before this phase a person found a project by scrolling the project list. Now Ctrl K (⌘K on a Mac) opens one search box from anywhere: it finds projects, review questions, brief claims, papers, the brief's passages and follow-up questions across every project the person is a member of, grouped by project, and it runs three commands. The rest of the keyboard table in [UI_UX_SPEC §11.5](../UI_UX_SPEC.md#115-keyboard) works too, and single-key shortcuts can be turned off (WCAG 2.1.4). It serves [UI_UX_SPEC §7.6](../UI_UX_SPEC.md#76-search-across-projects) and [ADR 0018](../adr/0018-search-decrypts-and-matches-per-request.md).

## What this phase delivers

- `GET /api/v1/search?q=&scope=`: results grouped by project, only from the caller's projects, over text members already see; raw document text is never searched ([`api.md`](../api.md#search-across-projects)).
- Limits: a 2–200 character query, 5 hits per scope in a project, 50 in all, 100 projects searched, 60 searches a minute per person.
- The palette: scope chips (Tab changes the scope), ↑↓ / Enter / Esc, recent projects and commands when the box is empty, "New project", "Go to settings", "Toggle dark mode".
- Keys: `?` shows the shortcuts, `]` opens or closes the canvas, `j` / `k` move between chat cards, Enter on a card follows its link, Esc in a dialog closes only the dialog.
- Account → Appearance: a "Single-key shortcuts" switch beside "Reduce motion", kept in this browser.
- No migration, no new table, no new setting.

## Architecture choices and why

### D17.1 Decrypt and match per request, no index

- **Decision:** each search opens the caller's projects one at a time, in its own short transaction, decrypts what is searched and matches every word, ignoring case and accents.
- **Why:** project text is sealed under each project's key; a plaintext index would survive crypto-shredding.
- **Rejected:** Postgres full-text search (matches nothing in ciphertext, or needs plaintext); an index sealed per project (needs a write wherever project text changes, owned by other features).
- **Costs:** cost grows with the caller's data; no ranking or stemming.
- **Where:** [`application/project_search.py`](../../services/api/app/application/project_search.py), [`domain/project_search.py`](../../services/api/app/domain/project_search.py).
- **Proven by:** `tests/test_project_search_api.py`, `tests/test_project_search_domain.py`.

### D17.2 Single-key shortcuts are a browser preference

- **Decision:** the switch is stored in `localStorage` (`humani-t:single-key-shortcuts`), like the theme and reduced motion; Ctrl K is not a single key and always works.
- **Why:** it is a display preference about this keyboard, not project data.
- **Rejected:** an account setting in the API: a new endpoint for a per-device choice.
- **Where:** [`app/preferences.ts`](../../apps/web/src/app/preferences.ts), [`shell/useShortcuts.ts`](../../apps/web/src/shell/useShortcuts.ts).
- **Proven by:** `src/shell/Keyboard.test.tsx`, `src/features/account/AccountPage.test.tsx`.

## Libraries

None added.

## Files to read, in order

1. [`services/api/app/domain/project_search.py`](../../services/api/app/domain/project_search.py) — what matches, the excerpt, the caps.
2. [`services/api/app/application/project_search.py`](../../services/api/app/application/project_search.py) — which texts are opened, and the membership check per project.
3. [`apps/web/src/features/search/CommandPalette.tsx`](../../apps/web/src/features/search/CommandPalette.tsx) — the combobox, scopes and where each hit leads.
4. [`apps/web/src/shell/useShortcuts.ts`](../../apps/web/src/shell/useShortcuts.ts) and `ReviewLayout` in [`ReviewPage.tsx`](../../apps/web/src/features/reviews/ReviewPage.tsx) — the keys.

## Set up

Nothing beyond [00 — Setup](00-setup.md). Start the API and the web app as usual, sign in as a member of at least two projects, one with a brief.

## Manual tests

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T17.1 | Postman: `GET http://127.0.0.1:8731/api/v1/search?q=cash` with your session cookie | `200`; `groups` lists only your projects, each with `project_name` and `hits` | D17.1 | A project you are not in appears: stop, it is a leak; check `_search_project` |
| T17.2 | Same with `q=a`, then `scope=documents` | `422` both times | Limits | Look at `SearchQuery.__post_init__` and the route's `scope` type |
| T17.3 | Send the request 61 times within a minute (Postman runner) | The 61st is `429` with `Retry-After` | Limits | Check `SEARCHES_PER_MINUTE` and the rate limiter wiring |
| T17.4 | Upload a document whose text holds a made-up word, approve it, then search that word | No hit from the document's raw text | ADR 0018 | Raw text reached search: check nothing reads `documents` |
| T17.5 | In the browser press Ctrl K, type a claim from a brief, Enter | The brief opens at that run | Palette | Check `openHit` in `CommandPalette.tsx` |
| T17.6 | In the palette press Tab twice | "Briefs" is pressed and results are only briefs | Palette | Check `onKeyDown` |
| T17.7 | Press `?` on any page | "Keyboard shortcuts" lists every key; Esc closes it | §11.5 | Check `useShortcuts` |
| T17.8 | On a review with a brief open press `]`, then `]` | The canvas closes, then the same canvas opens | §11.5 | Check `useCanvasKey` |
| T17.9 | Click in the chat, press `j` and `k` | Focus moves card by card with a visible ring | §11.5 | Check `moveBetweenCards` |
| T17.10 | Account → Appearance, turn "Single-key shortcuts" off, press `[` and `?`, then Ctrl K | `[` and `?` do nothing; Ctrl K still opens search | D17.2 | Check `allowsSingleKey` |

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
| T17.8 | ☐ | |
| T17.9 | ☐ | |
| T17.10 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Only the caller's projects; removed members and deleted projects find nothing | `tests/test_project_search_api.py::TestWhoFindsWhat` |
| Each scope finds what it should, with the ids to open it | `tests/test_project_search_api.py::TestWhatIsSearched` |
| Raw document text is never searched; nothing is audited | `tests/test_project_search_api.py::TestPrivacy` |
| Query bounds and the per-person rate | `tests/test_project_search_api.py::TestLimits` |
| Matching, excerpts and caps | `tests/test_project_search_domain.py` |
| The palette | `apps/web/src/features/search/CommandPalette.test.tsx` |
| The keys of §11.5 and the preference | `apps/web/src/shell/Keyboard.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_project_search_api.py tests/test_project_search_domain.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G17.1 | Closed: tags are searched in the `projects` scope (`SealedTags` in `get_search`) | – | – |
| G17.2 | Closed: the empty palette lists Needs you from the same endpoint as Home | – | – |
| G17.3 | Search cost grows with the caller's data (ADR 0018) | Slow for people with many large projects | An index sealed per project, behind the same use case |
| G17.4 | Only the latest brief and the newest run's papers are searched | Older versions are not found | Revisit with brief versions |

## Glossary

| Term | Meaning |
| --- | --- |
| **Scope** | The kind of thing searched: projects, briefs, papers, passages or questions. |
| **Combobox** | A text box that controls a list of choices; the focus stays in the box while arrows move through the list. |
| **Single-key shortcut** | A shortcut that is one key with no Ctrl or ⌘, such as `?`; WCAG 2.1.4 asks that these can be turned off. |
