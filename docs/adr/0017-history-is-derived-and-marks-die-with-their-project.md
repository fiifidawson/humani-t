# ADR-0017: History is derived from existing records, and marks die with their project

- **Status:** Accepted
- **Date:** 2026-11-19

## Context

P3 adds Needs you, Recent activity, a project timeline, and seen and saved marks (UI_UX_SPEC §7.1, §7.4, §7.5). Each needs to know what happened, to whom, and which papers a person has read across projects. Two choices are hard to undo once data exists: whether history gets its own event store, and how a paper is recognised in another project when a paper's identity is stored only as a digest under its own project's key.

## Decision

- **No new event store.** Needs you, activity and the timeline are derived on every read from reviews, plan versions, runs and their event logs, gates, briefs, follow-up questions, documents and label rule versions. These already record who and when, at the moment each thing happened, and they are already sealed and shredded with the project. The audit log is not used: it holds ids only, and has no index by project.
- **Only personal choices are stored:** `needs_you_dismissals` (person, item key), `brief_reads` (person, brief) and `paper_marks` (person, paper, seen or saved). An item key names the gate, run, plan version or brief, so a dismissed item comes back only when the thing changes.
- **A paper mark keeps the paper's identity** (`PaperRecord.identity`: DOI, else normalised title and year, the dedup rule's key) **sealed under the paper's own project key**. To show the mark in another project, the identity is opened (the person is a member) and digested under that project's identity key, then looked up as a re-run looks up papers.
- Every read starts from the projects the person is a member of now; marks and lines in other projects are never opened.

## Consequences

- Nothing new can drift from what the pipeline recorded, and deleting a project removes its lines and makes its marks unreadable with no extra step.
- Each read does more work: a timeline decrypts every run's log. This is fine at the size of a team's projects; if it is not, a cache can be added without changing what is stored.
- A person who leaves a project loses its marks from view; they come back if the person is added again.

## Alternatives rejected

- **An activity table written beside each action:** a second record of the same facts that every use case would have to remember to write, and that could disagree with the first.
- **A per-user digest of the identity:** it would survive the project's deletion, keeping a trace of what a person read in a project that no longer exists.
- **Matching papers by decrypting every paper of the other project:** the same answer at a cost growing with the project's size, where the digest lookup grows with the person's marks.
