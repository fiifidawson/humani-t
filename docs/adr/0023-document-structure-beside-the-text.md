# ADR-0023: A Word file's structure is kept beside its text; other files' layout is guessed

- **Status:** Accepted
- **Date:** 2026-11-22

## Context

The owner reviews an uploaded document's masking with the original and "What the model will see" side by side, paired paragraph by paragraph (walkthrough 16, D16.13). Extraction keeps only the words, so a long report reads as an unbroken run of paragraphs: no headings, lists, tables or page breaks. A Word file says what each paragraph is (its style, its numbering, the table it sits in); a PDF or a text file does not. Every mask, selection and correction works on character offsets into the plain text, and that must not change.

## Decision

- **One kind per line, beside the text.** The DOCX reader records, for each line of the extracted text, a `DocumentLine` (`app/domain/document_lines.py`): `paragraph`, `heading` with a level 1 to 3, `list_item`, or `table_cell` with its table, row and column, counted from 0. The kinds are a fixed vocabulary, validated in the domain; anything else, from storage or from the extraction child, is refused. Nothing is ever written into the text, so masking, offsets and the mask bar are untouched.
- **Where kinds come from.** A heading is a paragraph whose style is Title (level 1) or Heading 1 to 9 (Heading 1 is level 1, Heading 2 level 2, Heading 3 and below level 3). Styles are matched by name, read from `word/styles.xml`, because Word localises style ids but never the built-in names; without that part the id itself is tried. A list item has numbering (`w:numPr` with a `numId` other than 0) or a style whose name starts with "list". A paragraph in a table is a cell of the outermost table: a table inside a cell stays part of that cell. Only a paragraph's own `w:pPr` counts, not the previous one a tracked change keeps. `styles.xml` is parsed with the same refusals and size checks as the text parts.
- **Stored as `page_starts` is.** `document_texts.lines` is a nullable JSONB column (migration 0023), not sealed: it holds no words, only kinds and positions, as `page_starts` holds only offsets. It is deleted with the text and is only ever returned in the same responses as the text it describes, under the same access rules: `lines` on the owner's review (lines of `raw_text`) and on the masked text any member reads. The masked text's `lines` is `null` when a mask joined two lines, because they no longer fit it.
- **The extractor's version is `local@2`.** Texts read before it have no `lines`; they are not read again.
- **The web lays both panes out from the original's lines.** Lines are grouped into blocks (heading, paragraph, list, table) and page dividers from `page_starts`; both panes get the same blocks, mapped onto each text's own lines, so they pair up block by block. If the masked text has a different number of lines, both panes fall back to the plain paragraphs shown before, and to one block each if even those differ. Text is always drawn as text, never as HTML.
- **Other files' layout is guessed, cautiously, in the browser.** It is display only and decides nothing the API enforces. A heading is a line of at most 60 characters that starts a block (after a blank line, at the top of a page, or, outside a PDF, after a sentence), contains a letter, does not start in lower case, has no sentence end in it or at its end, and is followed on the same page by a longer paragraph that ends a sentence. A list is two or more lines in a row starting with a marker (•, -, *, "1.", "a)", "(i)" and similar), kept in the text; in a PDF an item goes on over printed lines until one ends a sentence. Guessed headings all have one level.

## Consequences

- Word files read since `local@2` show their own headings, lists and tables; everything else gets a layout that is usually right and, when unsure, a plain paragraph.
- Known limits: Word's list numbers are not in the extracted text, so Word lists are shown with bullets; nested lists are flat; merged table cells are not spanned; a Word table's first row is shown as its column headings. A guessed heading can still be wrong: a short title-like line followed by a sentence, or a PDF's running header at the top of each page. A level is never guessed.
- One more read per masked-text request: the text row, for its `lines`. No more decryption.

## Alternatives rejected

- **Markup inside the text** (Markdown or HTML from the extractor): every offset, mask and stored correction would shift, and the browser would be asked to render markup from an untrusted file.
- **Sealing the structure:** it reveals nothing the text does not, and nothing more than `page_starts`; sealing it would cost a decryption per read for no protection.
- **Guessing in the API:** a heuristic that changes would change stored data, or need a version of its own, for something that only affects how a page looks.
- **Rendering the original file:** opening an untrusted file in the browser, which the design avoids (D16.13).
