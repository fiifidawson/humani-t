# ADR-0019: The brief's PDF is made by the reader's browser from a print page

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

UI_UX_SPEC §6.9 and §13.2 ask for the decision brief as a single-file PDF someone can download and read offline, with the caveat on every page (§8.7) and the sources at the end. `brief.md`, `brief.json` and a print page, `brief.html`, already exist (`GET …/exports/{name}`).

The print page did not work as intended. It was served as an attachment, so the "opens in a new tab" link downloaded an HTML file instead. Opened in a tab, the API's `default-src 'none'` policy blocked its own stylesheet. Its caveat was a `position: fixed` footer, which Chrome printed over the last two lines of each page and so hid text.

The brief is model text in the language of the question, with paper titles in any script. Field users read it on phones over slow links.

## Decision

- **The browser makes the PDF.** `brief.html` stays the PDF route: the reader opens "Brief to print or save as PDF", prints it and chooses "Save as PDF". A line on the page, hidden when printed, says so. The browser shapes and draws every script with the device's own fonts, so text is never lost or garbled.
- **Served inline, with its own policy.** `brief.html` alone is `Content-Disposition: inline` (the file name stays, built from ids). Its `Content-Security-Policy` is `default-src 'none'; style-src 'sha256-…'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`: the hash of its one fixed stylesheet and nothing else, so no script can run in it. Every other export is still an attachment under the API's default policy.
- **The caveat repeats in a table footer.** The page is one layout table whose `<tfoot>` holds the caveat. Browsers repeat a table's footer on every printed page, in space of its own, so nothing is covered. The sources are the last section.
- **No new dependency.**

## Consequences

- No PDF engine, fonts or shaping library on the server, so the API image does not grow and no new code reads untrusted text.
- Saving takes two steps (print, then Save as PDF), not one tap. On Android Chrome: menu, Share, Print. On iOS Safari, saving a print as a PDF is harder to find.
- How the PDF looks depends on the browser. Checked in Chrome 157 with a twelve-page brief: the caveat is on every page and no line is covered. Firefox also repeats table footers. Safari is not checked yet (walkthrough 18, T18.5).
- Because the page is shown in a tab on the app's origin, it relies on escaping and its policy. Both are tested: hostile model text comes out escaped, and the policy allows no script.

## Alternatives rejected

- **A PDF made on the server (ReportLab, BSD; fpdf2, LGPL-3.0).** A real file in one tap, and no policy change. But both need Pillow (a large C library with frequent security fixes) and font files bundled in the image. Correct Arabic, Amharic or Burmese would also need HarfBuzz and a Noto font for each script, tens of MB, or text comes out as boxes or in the wrong order. Worth revisiting if field users cannot manage the print dialog, or if a signed or archival PDF (PDF/A) is needed.
- **WeasyPrint.** Good HTML-to-PDF, BSD licence, but it needs Pango and its system libraries in the image (about 100 MB).
- **A hand-written PDF writer using pypdf (already a dependency) and the built-in PDF fonts.** Those fonts cover Latin-1 only, so other characters would be dropped. That silently changes a decision brief.
- **A PDF made in the browser by a script (jsPDF).** A new client dependency with the same font and shaping problem, and a script running on brief text.
- **The app calls `print()` on the page in a hidden frame.** One tap fewer, but it would need `frame-ancestors 'self'` on the page and `frame-src 'self'` in the app's policy, loosening both.
