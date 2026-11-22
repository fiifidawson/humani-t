# 18 — P4: low data mode and the brief as a PDF

> **Milestones:** P4 (UI_UX_SPEC §15) · **Status:** done · **Previous:** [18 — P4: evaluations](18-p4-evaluations.md) · **Next:** none yet

Someone on a slow or costly connection can now turn on low data mode, or accept it when the app offers it: the app then uses the device's own fonts, stops animating, and redraws a live run every 10 seconds instead of on every event. Anyone can now open the decision brief as a print page and save it as a PDF to read offline, with the caveat on every page and the sources at the end. It serves [UI_UX_SPEC §13](../UI_UX_SPEC.md#13-low-bandwidth-and-field-use), [§6.16](../UI_UX_SPEC.md#616-account) and [§6.9](../UI_UX_SPEC.md#69-decision-brief) "Downloads and sharing".

## What this phase delivers

- A **Low data mode** switch in Account → Appearance, after Reduce motion, kept per browser.
- In low data mode: no font file is downloaded (system fonts), nothing animates, and live run progress is redrawn at most every 10 seconds. A gate and the end of a run still show at once.
- A one-time notice offering low data mode when the browser reports `Save-Data` or a slow connection. It never turns the mode on by itself.
- `GET /api/v1/reviews/{review_id}/exports/brief.html` now opens in a tab (`inline`), is styled under its own policy, and repeats the caveat on every printed page without covering text. No new endpoint, setting, table or migration.

## Architecture choices and why

### D18.1 The browser makes the PDF

- **Decision:** the print page `brief.html` is the PDF route; the reader prints it and chooses Save as PDF. See [ADR 0019](../adr/0019-the-brief-pdf-is-made-by-the-browser.md).
- **Why:** the browser draws every script correctly with the device's fonts, and no dependency, font or shaping library is added to the API image.
- **Rejected:** a server PDF (ReportLab or fpdf2 need Pillow, bundled fonts and HarfBuzz for Arabic and other scripts); WeasyPrint (Pango in the image); a hand-made PDF with the built-in fonts (Latin-1 only, so text would be lost).
- **Costs:** two steps to save instead of one tap; how it looks depends on the browser.
- **Where:** [`api/exports.py`](../../services/api/app/api/exports.py), [`api/routes/exports.py`](../../services/api/app/api/routes/exports.py), [`domain/exports.py`](../../services/api/app/domain/exports.py).
- **Proven by:** `tests/test_exports_api.py::TestBrief`, `tests/test_exports_domain.py::TestFilenames`.

### D18.2 The print page opens inline, with a policy that allows only its stylesheet

- **Decision:** `brief.html` alone is `Content-Disposition: inline`, with `Content-Security-Policy: default-src 'none'; style-src 'sha256-…'; …`. The hash is computed from the fixed stylesheet when the module loads.
- **Why:** as an attachment the "opens in a new tab" link downloaded an HTML file, and in a tab the API's `default-src 'none'` blocked the stylesheet.
- **Rejected:** `'unsafe-inline'` styles (any injected style would apply); a separate stylesheet route (a second request and a second audited download).
- **Costs:** the page shows on the app's origin, so it depends on escaping; the policy forbids every script as a second line.
- **Proven by:** `test_the_print_brief_may_use_its_own_stylesheet_and_nothing_else`, `test_the_print_brief_escapes_the_models_text`.

### D18.3 The caveat repeats in a table footer

- **Decision:** the page is one layout table (`role="presentation"`) whose `<tfoot>` holds the caveat.
- **Why:** browsers repeat a table footer on every printed page in space of its own. The old `position: fixed` footer was printed over the last two lines of each page in Chrome, hiding text.
- **Rejected:** `@page` margin boxes (not in Firefox); a fixed footer (covers text).
- **Proven by:** `test_the_print_brief_repeats_the_caveat_on_every_page_and_runs_no_script`, `test_the_print_brief_ends_with_its_sources`, and by hand in T18.5.

### D18.4 Low data mode reuses the theme

- **Decision:** `applyLowData` sets `data-low-data` on `<html>` before the first render. The `fonts.body` and `fonts.heading` tokens become system font stacks under a `lowData` condition, and the reduced-motion rule in `theme/system.ts` also matches `html[data-low-data]`.
- **Why:** a browser fetches a font file only when text uses its family, so pointing the tokens at system fonts means no font file is downloaded. Chakra's token layer comes after its base layer, so the override has to be a token condition; a global rule would lose.
- **Rejected:** removing the font CSS imports at run time (the bundle is already loaded); a second reduced-motion switch.
- **Where:** [`app/preferences.ts`](../../apps/web/src/app/preferences.ts), [`theme/system.ts`](../../apps/web/src/theme/system.ts), [`main.tsx`](../../apps/web/src/main.tsx).
- **Proven by:** `src/theme/lowData.test.ts`, `src/features/account/AccountPage.test.tsx` ("turns low data mode on…").

### D18.5 Run progress: the stream stays, only drawing slows

- **Decision:** `useRunEvents` applies every event at once to its own copy of the state, as before. In low data mode it hands that state to React at most every 10 seconds, instead of every 0.5 seconds for progress only. `gate.required`, `gate.resolved` and `job.finished` still draw at once.
- **Why:** de-duplication and resuming stay exact (they read the copy, not what is drawn), and nothing waits 10 seconds for a decision. The server still sends one small event at a time (§13.2 "SSE, not polling").
- **Rejected:** polling every 10 seconds (more requests, and loses Last-Event-ID resume); asking the server for fewer events (a new API option for a display preference).
- **Where:** [`features/runs/useRunEvents.ts`](../../apps/web/src/features/runs/useRunEvents.ts).
- **Proven by:** `src/features/runs/useRunEvents.test.tsx` ("in low data mode").

### D18.6 The offer appears once, and only by choice

- **Decision:** `LowDataOffer` in the shell reads `navigator.connection` (`saveData`, or an `effectiveType` of `slow-2g`, `2g` or `3g`). "Turn on low data mode" or "No thanks" ends the offer in this browser (`humani-t:low-data-offered`).
- **Why:** §13.2 asks for an automatic offer, and a preference must stay the person's choice.
- **Rejected:** turning the mode on by itself; asking on every visit.
- **Costs:** Firefox and Safari do not have the Network Information API, so they never see the offer; the switch in Account still works there.
- **Proven by:** `src/shell/LowDataOffer.test.tsx`.

## Libraries

None added.

## Files to read, in order

1. [`apps/web/src/app/preferences.ts`](../../apps/web/src/app/preferences.ts) — `readLowData`, `applyLowData`, `useLowData`, `useLowDataOfferAnswered`.
2. [`apps/web/src/theme/system.ts`](../../apps/web/src/theme/system.ts) — the `lowData` condition, the font semantic tokens, the motion rule.
3. [`apps/web/src/features/runs/useRunEvents.ts`](../../apps/web/src/features/runs/useRunEvents.ts) — the 10-second throttle and what bypasses it.
4. [`apps/web/src/shell/LowDataOffer.tsx`](../../apps/web/src/shell/LowDataOffer.tsx) — the offer.
5. [`services/api/app/api/exports.py`](../../services/api/app/api/exports.py) — `_PRINT_CSS`, `PRINT_PAGE_CSP`, `_brief_html`.
6. [`services/api/app/api/routes/exports.py`](../../services/api/app/api/routes/exports.py) — the inline header and the policy.

## Set up

Nothing beyond [00 — Setup](00-setup.md). Run the API and the web app as usual, sign in, and have a review with a finished run and a brief (the demo mode has one).

## Manual tests

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T18.1 | Account → Appearance → turn **Low data mode** on, reload, then off | The switch stays on after the reload; in DevTools → Elements, `<html>` has `data-low-data` while it is on | D18.4 | `humani-t:low-data` in DevTools → Application → Local storage |
| T18.2 | DevTools → Network, filter **Font**, tick Disable cache, reload with the mode off, then on | Off: `manrope-latin-…woff2` and `playfair-display-latin-…woff2`. On: no font requests, and text in the system font | D18.4 | Computed `font-family` of `<body>`; the `--ht-fonts-body` variable on `<html>` |
| T18.3 | In Chrome, DevTools → Network → throttling **3G**; console: `localStorage.removeItem('humani-t:low-data-offered')`; reload | A "Low data mode" notice at the top; "No thanks" hides it for good; "Turn on low data mode" turns it on | D18.6 | `navigator.connection.effectiveType` in the console must say `3g` or slower |
| T18.4 | With the mode on, start a run and watch the run panel and DevTools → Network → the `events` request → EventStream | Events keep arriving one by one; the counts on screen change about every 10 s; a privacy gate appears at once | D18.5 | `useRunEvents.ts`: `readLowData()` and `ALWAYS_AT_ONCE` |
| T18.5 | Brief panel → **Download** → **Brief to print or save as PDF**; then Ctrl P → Save as PDF | A new tab shows the styled brief with "To keep a copy…" at the top. The PDF has no such line, has the caveat at the foot of every page, hides no line, and ends with Sources. Repeat in Firefox and Safari | D18.1, D18.3 | DevTools → Console for a blocked style (policy hash) |
| T18.6 | DevTools → Network → the `brief.html` request → Headers | `content-disposition: inline; filename="Humani-T-run-…-brief.html"`, `content-security-policy` with `style-src 'sha256-…'` and no `script-src`; `brief.md` is still `attachment` | D18.2 | `routes/exports.py`, `ExportName.opens_in_browser` |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T18.1 | ☐ | |
| T18.2 | ☐ | |
| T18.3 | ☐ | |
| T18.4 | ☐ | |
| T18.5 | ☐ | Chrome ☐ Firefox ☐ Safari ☐ |
| T18.6 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| The print brief opens inline, styled only by its own stylesheet, with no script | `tests/test_exports_api.py::TestBrief` |
| Only `brief.html` opens in the browser; the header keeps its safe name | `tests/test_exports_domain.py::TestFilenames` |
| Low data mode uses system fonts and stops animation | `apps/web/src/theme/lowData.test.ts` |
| The switch applies at once and is remembered | `apps/web/src/features/account/AccountPage.test.tsx` |
| Progress redraws every 10 s; gates and the end at once; one stream | `apps/web/src/features/runs/useRunEvents.test.tsx` |
| The offer appears once, only on a slow connection or Save-Data, and never turns the mode on by itself | `apps/web/src/shell/LowDataOffer.test.tsx` |

```powershell
cd services\api
uv run pytest tests/test_exports_api.py tests/test_exports_domain.py -v
cd ..\..\apps\web
pnpm exec vitest run src/theme/lowData.test.ts src/features/runs/useRunEvents.test.tsx src/shell/LowDataOffer.test.tsx src/features/account/AccountPage.test.tsx
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G18.1 | The first screen's JavaScript is about 214 KB gzip (13 files, 676 KB raw) against the 170 KB budget of §13.1; `index-*.js` alone is 139 KB | Slower first load on slow links, in every mode | Split more by route (the shell imports the search palette and dialogs eagerly); measure again |
| G18.2 | Lighthouse budgets are not checked in CI (§13.1) | A regression in size goes unnoticed | Add `@lhci/cli` with a throttled mobile profile, `upload.target: filesystem` (never the public storage), against `vite preview` and the API's demo mode with a signed-in script |
| G18.3 | Two font files load normally (Manrope 25 KB, Playfair 38 KB), not one | Within the 100 KB budget, but not "one variable font file" | Decide whether headings keep their own family |
| G18.4 | The offer depends on the Network Information API | Firefox and Safari users are never offered the mode | The Account switch works everywhere |
| G18.5 | Safari's printing of the table footer is not checked | The caveat might show only on the last page there | T18.5 in Safari |

## Glossary

| Term | Meaning |
| --- | --- |
| **Low data mode** | A per-browser preference that makes the app load and redraw less. |
| **Save-Data** | A browser setting that asks sites to send less data; pages read it as `navigator.connection.saveData`. |
| **Effective connection type** | The browser's estimate of the connection speed (`slow-2g`, `2g`, `3g`, `4g`), from `navigator.connection.effectiveType`. |
| **Style hash** | A Content-Security-Policy entry that allows one exact stylesheet, named by the SHA-256 of its text. |
