# CHITCHAT workflow: brief for a web app

This brief describes what CHITCHAT does today, as a command-line pipeline, so it can be rebuilt as a secure web app. It covers the workflow, the data, and the security rules the web app must keep. For full detail see [PIPELINE.md](PIPELINE.md).

## What it does

A researcher asks a research question. CHITCHAT finds relevant papers, adds the researcher's own documents, removes personal data, screens the papers against review criteria with an LLM, produces a dataset and a PRISMA report, and answers follow-up questions with cited sources.

It is built on [mmore](https://github.com/EPFLiGHT/mmore), which provides paper discovery, document reading, privacy masking, indexing and retrieval.

## The workflow

| # | Step | Who acts | Kind | Typical time | Output |
|---|---|---|---|---|---|
| 1 | **Ask a question** and pick a mode (shallow or deep) | User | Interactive | seconds | The question |
| 2 | **Clarify** (deep mode): the LLM asks about 4 questions on scope, population, setting, years | User + LLM | Interactive | 1 to 2 min | Answers |
| 3 | **Draft the search**: concepts with synonyms, grouped into categories | LLM | Interactive | seconds | A search plan |
| 4 | **Review the draft**: accept, ask for changes in plain words, or quit. Repeatable | User + LLM | Interactive, multi-turn | minutes | The accepted plan |
| 5 | **Discover**: search OpenAlex, Europe PMC and arXiv, download PDFs | System | Background job | 5 to 60 min | Papers with text |
| 6 | **Add own documents** (optional): PDF, Word, Excel, email, web pages, text | User uploads, system reads | Upload + background job | 2 to 5 min | Documents with text |
| 7 | **Mask personal data** in papers and documents, locally, with no LLM | System | Background job | 1 to 30 min | Masked text + privacy report |
| 8 | **Screen** each paper: the LLM answers the criteria with evidence quotes, fixed rules set the label (HIGH, MEDIUM, LOW, EXCLUDE) | System + LLM | Background job | 1 to 20 min | A label and evidence per paper |
| 9 | **Review results**: read labels and evidence, change rules and relabel without new LLM calls | User | Interactive | as long as needed | Final labels |
| 10 | **Export**: dataset (CSV and JSON) and PRISMA report | System | Background job | seconds | Files to download |
| 11 | **Index** included papers and own documents for questions | System | Background job | 1 to 5 min | A search index per project |
| 12 | **Ask**: questions answered from the index, citing `[P0012]` (papers) and `[D0003]` (documents) | User + LLM | Interactive | seconds | Answer + sources |

Steps 5, 7, 8, 10 and 11 run in order as one job. Each can be rerun on its own. Finished screening results are reused, so a rerun only pays for new or failed papers.

### The two modes

| | Shallow: quick scan | Deep: systematic search |
|---|---|---|
| Clarifying questions | none | about 4 |
| Categories | 1 to 3 | 3 to 6 |
| Papers | up to about 90 | up to several hundred |
| Full text | no: abstracts only | yes: PDFs |
| Time | minutes | up to an hour |

## The data

One **project** per search (today: one run folder).

| Object | Contents | Sensitivity |
|---|---|---|
| Search plan | Question, clarifying answers, every draft, every change request | Low (masked before it reaches the LLM) |
| Raw papers | Paper metadata and full text, as downloaded | Medium: may contain personal data (author contacts) and is copyrighted |
| Raw documents | The user's uploaded files and their text | **High**: may contain names, phone numbers, health data |
| Masked papers and documents | The same text with personal data replaced by tokens like `[PERSON_1]` | Lower, but not anonymous |
| Privacy report | Counts of what was masked, never the values | Low |
| Screening results | Per paper: answers, evidence quotes, label, model, cost | Low |
| Dataset and PRISMA report | Exports for the review | Low |
| Search index | Chunks of masked text, each tagged with its source | Lower (masked only) |
| Answers | Questions, answers, sources, privacy summary | Low to medium |
| Run history and logs | Each job: stages, durations, outcome, errors | Low |

## Security rules the web app must keep

These are already enforced by the pipeline. The web app must not weaken them.

### Personal data

1. **Mask before any LLM.** The question, clarifying answers, change requests and every text sent for screening or answering are masked first, on our own servers, with no LLM.
2. **Raw text never leaves its store.** Only masked text goes to LLMs, the dataset, the index and answers. Raw papers and raw documents live in a separate, access-restricted store.
3. **Own documents are always masked**, with a broader list (names, emails, phone numbers, dates, places, medical record and insurance IDs). This cannot be switched off.
4. **Privacy reports hold counts, never values.**
5. **Masking is not anonymity.** It misses some formats (for example medical record numbers like `MRN 448-1207`). Tell users so, and offer a review step before answers are sent (today: `ask --review`).

### Access and isolation

6. **Every user signs in** (the organisation's single sign-on, with multi-factor authentication).
7. **Each project belongs to its owner and the people they share it with.** Every request checks this. Projects never share an index (today each run has its own index collection).
8. **Roles:** owner, reviewer (can relabel and ask), viewer (read-only exports). Only owners upload documents or delete a project.

### Secrets and outside services

9. **API keys stay on the server.** The browser never sees them. Use a secrets manager, not files.
10. **Outbound calls are limited** to the paper sources, PDF hosts and the LLM provider. PDF links come from paper metadata, so block internal addresses (to prevent server-side request forgery) and cap download sizes.
11. **LLM provider terms:** use a provider account that does not train on our data.

### Uploads and processing

12. **Check uploads**: allowed types only (PDF, DOCX, XLSX, PPTX, EML, HTML, MD, TXT), size limits, malware scan.
13. **Process documents in an isolated worker** with no network access except the job queue.
14. **Treat paper text as data, not instructions.** Screening already wraps paper text in tags and tells the LLM to ignore instructions inside it. Keep this.

### Storage, audit and retention

15. **Encrypt data at rest and in transit** (HTTPS only).
16. **Keep an audit log** of who ran, relabelled, asked, exported, shared or deleted what. Today's run history, search plans and answer log are the starting point.
17. **Let owners delete a project completely**, including raw text, the index and logs. Set a retention period for raw documents.
18. **Licensing:** paper text belongs to its publishers. Exports and shared answers must respect each source's terms.

## How the commands map to the web app

| Today (command line) | Web app |
|---|---|
| `chitchat search "question"` | "New review" form, then the clarify → draft → review screen |
| Draft boxes per category, `a` / `c` / `q` | An editable search view: one card per category, a chat box for changes, Accept and Discard buttons |
| `chitchat run` (stages) | A background job with live progress per stage, and a Cancel button |
| Live stage output, the Done or Failed box | A progress panel and a job summary, both kept in history |
| `chitchat run --redecide`, `chitchat report` | "Edit rules" and "Relabel" on the results screen, with no new LLM cost |
| `dataset.csv`, `prisma.md` | A results table with filters, evidence on click, PRISMA diagram, downloads |
| `chitchat ask`, `--no-privacy`, `--review` | A chat screen with sources as links, a privacy summary per answer, and an optional "review before sending" step |
| `chitchat status`, history and logs | A project overview: stages, durations, recent jobs, errors |
| `my_docs/` folder | A "Your documents" upload area per project |

## What a first version can leave out

- Google Scholar as a source (it hits captchas).
- The predefined legacy search (keep it as an admin-only template).
- Choosing LLM providers per user (one organisation-wide provider is enough).
- Editing the screening questions in the browser (start with fixed criteria plus rule editing).
