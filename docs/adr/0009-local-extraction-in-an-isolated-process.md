# ADR-0009: Uploaded documents are read by a small local extractor in a separate process; mmore comes later behind the same seam

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

An uploaded document has to become text before it can be masked, reviewed and cited. [`dependencies.md` §2](../dependencies.md#2-mmore) names mmore as the extraction library, behind our own interface, and [`architecture.md` §3.9](../architecture.md#39-masking) puts it in the provider packages. The brief's rule 13 asks that documents be processed "in an isolated worker with no network access except the job queue".

mmore's `process` extra reads PDFs, Office files, images and audio, and does so with a large dependency tree (PyTorch-based models among them), model downloads on first use, and a much larger attack surface than the four formats P2 accepts (PDF, DOCX, TXT, MD). Every file it parses was chosen by someone outside the deployment.

## Decision

- Extraction is a seam: `DocumentExtractor` in `app/extraction/base.py`, with `ExtractionLimits` (pages, characters, archive members, a member's inflated size, its compression ratio, a timeout) and fixed failure reasons (`ExtractionFailedError`), never a parser's message, which can quote the file.
- The implementation for P2 is `app/extraction/local.py`:
  - text and Markdown: UTF-8 with the standard library;
  - DOCX: `zipfile` and `expat` from the standard library. Members, declared sizes and ratios are checked before inflating and real bytes are counted while inflating. A part with a document type declaration is refused, which rules out external entities (XXE) and entity-expansion bombs;
  - PDF: **pypdf** (pure Python, BSD-3-Clause, no runtime dependencies on Python 3.13, no network code, bounded stream decompression).
- Production runs it through `IsolatedDocumentExtractor` (`app/extraction/isolated.py`): a child process of the same interpreter, started with `-I` and an environment holding only what Windows needs to start Python, so no secret or connection string is within reach. The child installs an audit hook that refuses network connections, starting processes and writing files; on Linux it also caps its own memory, CPU time and file size. The parent kills it at the timeout.
- Extraction runs in the worker (`DocumentProcessor`), never in the upload request.
- mmore becomes a second implementation of `DocumentExtractor` when its formats are needed (images with OCR, spreadsheets, audio), run the same isolated way.

## Consequences

- One small dependency (pypdf) instead of a model stack. Nothing is downloaded at run time.
- Text in images is not read: a scanned page or a photographed form produces no text, or fails with `no_text`. The review screen says so, and masking cannot hide what it never saw.
- XLSX, PPTX, EML and HTML, listed in UI_UX_SPEC §6.13, are not accepted yet. XLSX and PPTX can reuse the DOCX reader's archive and XML checks; HTML and EML can use the standard library's parsers.
- A child process costs about half a second per document on Windows, less on Linux. It is paid once per upload, in the background.
- The audit hook is defence in depth, not a sandbox: native code could bypass it. Real isolation is the container: the worker container has no route to the internet except where the operator allows (rule 13), and a separate extraction container with no network at all is the next step if mmore's native code arrives.

## Alternatives rejected

- **mmore now:** its formats beyond PDF and DOCX are not needed in P2, and its size and model downloads conflict with "nothing is fetched at run time".
- **python-docx:** convenient, but it brings lxml (a large native parser) for what `zipfile` and `expat` do in a page of code we control, including refusing a doctype outright.
- **pdfminer.six or PyMuPDF:** pdfminer.six is slower and larger for plain text; PyMuPDF wraps the native MuPDF library under AGPL, more native code facing hostile files.
- **Extraction in a thread of the worker:** a thread cannot be stopped mid-parse, so a timeout would only stop the wait. `LocalDocumentExtractor` does exactly that, for tests and development only.
