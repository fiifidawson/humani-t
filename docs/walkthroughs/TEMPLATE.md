# NN — Phase title

<!-- Copy this file to `NN-short-name.md` when a phase starts. Keep every heading, in this order, even when a section is short: a reader learns where to look once and finds the same thing in every phase. Delete these comments. Never hard-wrap: one paragraph or list item per line. -->

> **Milestones:** M? · **Status:** planned | in progress | done · **Previous:** link to the previous phase file · **Next:** link to the next phase file, or its name in backticks while it is planned

<!-- Then add the phase to the table in README.md and update its status there. -->

<!-- Numbering: decisions D<phase>.<n>, manual tests T<phase>.<n> (sub-steps T<phase>.<n>.<m>), so any walkthrough can cite "D5.3" or "T2.14" and link to it. -->

<!-- Conventions: commands are Windows PowerShell 5.1 (no `&&`, no `export`; `;`, `if ($?) { }`, `$env:NAME = '...'`). Python snippets are piped in as a here-string (`@' … '@ | uv run python -`), because PowerShell 5.1 mangles double quotes inside program arguments. Every claim — path, endpoint, status, header, setting, test name — is checked against the code before it is written. A fictional password that gitleaks could mistake for a secret carries a `gitleaks:allow` comment on the same line. -->

<!-- Sections in order: What this phase delivers · Architecture choices and why · Libraries · Files to read, in order · Set up · Manual tests · Automated tests that prove it · Known gaps · Glossary. -->

<!-- Under "What this phase delivers", add a "### Data model" table when the phase adds or changes tables. Under "Set up", add "### Troubleshooting" only for problems specific to this phase; general ones belong in 00-setup.md. -->

<!-- When the code and an older document disagree, follow the code, say so in the walkthrough, and list the difference under Known gaps. -->

<!-- "Deliberately not used" (a table of libraries or approaches rejected for the whole phase) goes at the end of "Architecture choices and why". -->

<!-- Replace this paragraph: -->


One paragraph: what a person can do at the end of this phase that they could not do before, and which brief rules or architecture sections it serves (link them, for example [`architecture.md` §5.2](../architecture.md#52-sessions)).

## What this phase delivers

- A bullet per capability, written as behaviour ("a password reset link works once"), not as files.
- Endpoints added, with method and path.
- Tables and migrations added.
- Operator commands added.
- Settings added.

## Architecture choices and why

<!-- One subsection per decision, numbered D<phase>.<n> (D5.3 is the third decision of phase 05), so other walkthroughs can cite it. Every decision names at least one alternative that was rejected. -->

### DN.1 Decision title

- **Decision:** what was chosen, stated precisely enough to check against the code.
- **Why:** the reason, tied to the brief, a standard (NIST, OWASP, RFC) or a failure it prevents.
- **Rejected:** the obvious alternatives, and why each lost.
- **Costs:** what this makes harder or slower; what we gave up.
- **Where:** the files that implement it, as relative links.
- **Proven by:** `tests/test_x.py::TestClass` (and the method, when one test carries the claim).

## Libraries

<!-- One subsection per library or tool this phase introduced. -->

### Library name and locked version

- **What it does:** in plain words, as used here.
- **Why this one:** what made it the choice.
- **Alternatives:** what else was considered.
- **Trade-offs:** costs, risks, footguns, and how we contain them.
- **What we gave up:** capability or convenience we deliberately did without.

## Files to read, in order

<!-- Numbered. Start from the outermost layer a request touches, or from the domain rule; say which, and why that order. One line per file saying what to look for. -->

1. [`services/api/app/...`](../../services/api/app/) — what to notice in it.

## Set up

<!-- Only what this phase needs beyond [00 — Setup](00-setup.md). PowerShell 5.1: no `&&`, no `export`; use `;`, `if ($?) { }` and `$env:NAME = '...'`. -->

```powershell
cd services\api
```

## Manual tests

<!-- Numbered T<phase>.<n> (T5.12 is the twelfth manual test of phase 05). Each test has the four parts below. Group related tests under a level-4 heading if the list is long. Short tests may share a table with the columns `#`, `Do`, `Expect`, `Proves`, `If it fails`; a test that needs several steps or a code block gets its own subsection in the bullet form below. End with the results checklist. -->

### TN.1 Short name

- **Do:** the PowerShell command, or the Postman request (method, URL, headers, body).
- **Expect:** status code, headers, body, log line or database row — exact where it can be exact.
- **Proves:** which decision above this checks.
- **If it fails:** what a different result would mean, and where to look first.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| TN.1 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Behaviour in one line | `tests/test_x.py::TestClass` |

How to run only this phase's tests:

```powershell
uv run pytest tests/test_x.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G? | What is missing or wrong | What a user or attacker could observe | When and how it is closed |

## Glossary

| Term | Meaning |
| --- | --- |
| **Term** | Plain-language definition, as used in this phase. |
