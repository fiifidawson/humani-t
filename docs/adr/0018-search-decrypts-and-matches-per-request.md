# ADR-0018: Search across projects decrypts and matches per request, with no stored index

- **Status:** Accepted
- **Date:** 2026-11-19

## Context

UI_UX_SPEC §7.6 asks for one search across a person's projects (`GET /search`, the Ctrl K palette): project names and questions, brief claims, paper titles and abstracts, the brief's passages, and follow-up questions, grouped by project and only from projects the person is a member of. Raw document text must never be searched.

Every one of those texts is sealed under its project's data key and bound to its row ([`architecture.md` §5.4](../architecture.md#54-encryption)). SQL full-text search over ciphertext matches nothing, and a plaintext index (Postgres `tsvector`, an external engine) would put the text back in the clear beside the ciphertext, where deleting the project's key no longer reaches it.

## Decision

- **No index.** Each search lists the caller's live projects, newest first, and opens each one in its own short transaction: it checks the membership again, decrypts the texts below, and matches them in the application. Nothing about a search is stored, so there is nothing to shred: deleting a project's key ends its part in search at once.
- **What is searched** is text every member already reads through the API: the project's name; each review's question; the claims of each review's latest brief; that brief's cited passages, which are masked text (papers' masked abstracts and approved documents' masked passages); the titles and abstracts of the papers of the review's newest run with papers (public bibliographic records); and follow-up questions, stored masked. Raw document text and raw kept paper text, which only the mask stage and an owner's review read, are never opened. Tags join through a `ProjectTagSource` seam once tags exist.
- **Matching** is plain: every word of the query must appear, ignoring case and accents. No ranking beyond the order above; hits come back as an excerpt of about 160 characters around the first match, with the ids needed to open the hit.
- **Limits.** A query is 2 to 200 characters. At most 5 hits per scope in a project and 50 in all; at most 100 projects are searched, newest first, and the search stops once 50 hits are found. `is_truncated` says a limit was reached. Each person may search 60 times a minute (the costliest read there is); more is `429` with `Retry-After`.
- **Not audited.** A search reads only what the caller may already read and writes nothing; the audit log records decisions, not reads of this kind.

## Consequences

- Correct by construction: a non-member's project is never opened, a removed member's search stops at the membership check, and a deleted project's text cannot be read.
- Cost grows with everything the caller's projects hold: each search decrypts every name, question, brief and paper record of up to 100 projects, one database read per paper, and each decryption fetches the project's data key. A person with a few dozen projects of a few hundred papers each will wait about a second; the palette waits 250 ms after typing stops before it asks.
- Results are as fresh as the data: no index to rebuild or fall behind.
- No relevance ranking, stemming or typo tolerance: "price" finds "prices", "rises" does not find "rose".
- A brief's older versions and a review's older runs are not searched: the latest brief and the newest run with papers are what the screens open by default.

## Alternatives rejected

- **A plaintext index (`tsvector`, OpenSearch, SQLite FTS):** fast, but the text sits in the clear outside the project's key, so crypto-shredding a project would leave its words searchable and readable.
- **An index sealed under each project's key:** one encrypted blob per project, updated whenever a name, question, brief, paper or question changes, decrypted per search. It still decrypts every project per search, saving only the row reads, and needs a write at every place project text changes, owned by five other features. Worth it only if the per-request cost proves too high; it can be added behind the same use case without changing the endpoint.
- **Blind indexes (keyed hashes of words):** support exact words only, leak word frequency across a project, and need the same write hooks.
