# CHITCHAT

**ArCHitectures for Interpretable & Transparent Continuous Humanitarian Alignment in chatbot Technologies**

CHITCHAT is a research project of the Laboratory of Intelligent Global Health &
Humanitarian Response Technologies at EPFL. It asks a simple question with a difficult
answer: when conversational AI systems are used in humanitarian and global-health
settings, what does it take for them to be *interpretable*, *transparent* and genuinely
*aligned* with humanitarian principles — not once at launch, but continuously?

## The problem

Chatbots and other language-model systems are increasingly deployed where the stakes are
high and the users are vulnerable: health guidance, crisis response, humanitarian
services. Judging whether such a system is safe and appropriate means drawing on evidence
scattered across very different worlds — machine-learning preprints, medical literature,
ethics and policy scholarship, humanitarian practice.

That evidence is hard to gather and harder to keep current. The vocabulary itself is
contested: what counts as "humanitarian alignment", "transparency" or "interpretability"
depends on which field you start from. A review done by hand is out of date the moment it
is finished, and nobody can check how it was assembled.

## The approach

CHITCHAT treats the literature review as *infrastructure* rather than a one-off exercise.
The search is written down as code, so the whole chain — which terms were used, which
sources were queried, which papers were kept and why — is version-controlled, inspectable
and repeatable by anyone.

In practice that means a pipeline that:

1. **Starts from a curated vocabulary** of research concepts, each with its synonyms and
   near-synonyms, organised into thematic categories (from broad foundations through
   humanitarian and social impact to environmental and infrastructural cost).
2. **Builds the search queries automatically** from that vocabulary, rather than having a
   researcher type them by hand.
3. **Searches several academic repositories at once** — arXiv, OpenAlex, Europe PMC and
   Google Scholar — and merges the results into one deduplicated set.
4. **Collects the papers themselves**, downloading the documents that are openly available
   and extracting their text alongside the bibliographic metadata.
5. **Screens the corpus with LLM assistance**, scoring each paper for relevance and
   assigning a priority so that human reviewers can spend their attention where it counts.
6. **Analyses and visualises** the results — what was found, from which sources, on which
   themes, across which years.

Every stage hands off a plain data file to the next, so any stage can be re-run, inspected
or swapped out on its own. Re-running the pipeline with an updated vocabulary produces an
updated review; the difference between two runs is itself something you can look at.

## Why it is built this way

Three commitments shape the design:

- **Transparency.** Nothing in the process is implicit. The term list, the query logic, the
  sources and the screening criteria are all artefacts you can read.
- **Reproducibility.** Someone else, starting from the same repository, gets the same
  result — and can change one input to see what it changes.
- **Auditability.** Automated screening narrows the field; it does not make the final call.
  The pipeline is built so a human can see what was excluded and why.

## The components

The tooling is organised into parts that are useful on their own, not only inside CHITCHAT:

| Component | What it is |
| --- | --- |
| **Paper discovery** | Turns a keyword-and-synonym vocabulary into a deduplicated set of papers with metadata and full text, hiding the quirks of four different repository APIs behind one interface. |
| **Screening** | Applies structured, LLM-assisted relevance assessment to a corpus and produces per-paper scores and priorities for human review. |
| **LitLens** | The analytics and visualisation layer — word clouds, screening dashboards, source and year breakdowns. It runs entirely on local files and calls nothing external. |

Each is deliberately unopinionated about the others. Paper discovery does not care what
happens to its output; LitLens does not care where its input came from. That is what lets
pieces of CHITCHAT be reused in separate repositories for related work.

## Who is behind it

The project is led by researchers at EPFL, with collaborators elsewhere:

- **Annie Hartley** — EPFL
- **Laura Ferrarello** — EPFL
- **Johan Rochel** — EPFL
- **David Sasu** — EPFL
- **Tim Arni** — EPFL
- **Trevor Brokowski** — EPFL
- **Fiifi Dawson** — EPFL
- **Oriane Peter** — King's College London

## Licensing, in short

The source code is released under the **Apache License 2.0**.

That covers the code only. It does not cover the material the pipeline retrieves:
bibliographic metadata, abstracts and extracted document text from arXiv, OpenAlex,
Europe PMC and Google Scholar remain the property of their respective rights holders and
are not relicensed. Anyone redistributing the pipeline's outputs is responsible for
complying with the terms of the originating repository and of the underlying publications.

## Where to find more

- Main repository: <https://github.com/fiifidawson/CHITCHAT>
- Documentation, including the stage-by-stage technical report and the module design
  specs, lives in that repository under `docs/`.
