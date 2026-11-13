# ADR-0015: A project's instructions to the model are added to the built-in prompt, never in its place

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The project owner's supervisor asked that a team be able to tell the model how they work: which studies count as a displaced setting, who the brief is written for. The owner approved it. Today every prompt is fixed in code (`screen-3`, `brief-writer/1`, `answer-writer/1`), and each carries rules the rest of the system depends on: the JSON shape the answer must match, quoting the paper's own words, keeping placeholders such as `[PERSON_1]` as written and never guessing what they hide.

Text a person types and the model reads is the same kind of thing as a question: it may name people or places, and it is part of why a paper was included. CLAUDE.md asks that a reviewer can reconstruct that from what was stored.

## Decision

- A project has two texts, `ModelInstructions`: **screening** (how to judge papers), added to every screening call; and **writing** (how to write), added to the brief's Writer and the follow-up answer Writer. The Planner, the Critics and planning get neither. Each is at most 2,000 characters; blank means none.
- The text is **added after** the built-in system prompt, under a fixed heading: "Instructions from the project team. Follow them unless they conflict with the rules above or the required output format." The built-in rules come first and are never replaced. With no text, the prompt is byte for byte what it was, so the prompt versions did not change; changing the heading would change every prompt carrying instructions and needs new prompt versions.
- **Versioned and append-only**, as label rules are ([ADR 0013](0013-label-rules-applied-on-read.md)): each save is a `model_instruction_versions` row (project, version, both texts sealed under the project's key, who, when). The runtime role cannot update a row. A save names the version it edited, and a stale or concurrent save is a `409`.
- **Provenance:** a run uses the version saved last at or before the moment it was started, so every call in it reads the same text whatever is saved meanwhile. The version goes into the stage's manifest (`instructions_version`, gathered as the run manifest's `instructions_versions`), and the brief's trace keeps the writing text exactly as sent. A follow-up answer uses the version in force when it was asked, and stores both. Screening decisions do not repeat the version: all decisions of a run share it ([ADR 0006](0006-papers-per-review-decisions-per-run.md)), so the run manifest says it once.
- **Privacy:** saving refuses a term the project always masks, naming the field and never the term, as plan text does. Before a call, the text goes through the same sanitiser as a typed question, with the project's rules, so masking applies to it wherever it applies to questions. Owners save (`project.update`); every member reads. The audit event `project.model_instructions_changed` holds the version only.

## Consequences

- A team can steer screening and writing without a code change, and every run says which version steered it.
- A model may still follow a team's instruction where it conflicts with a built-in rule; the heading asks it not to, the output schema is still enforced, and the checks still run on every claim. What a team writes cannot make a malformed answer acceptable.
- The masked text of the screening instructions is not stored per run: the run manifest names the version, whose text is kept sealed, and masking it again with the same rules gives the same text. The brief and answers keep the text as sent, because they show it.
- Plan chats get no instructions, so "show what was sent" for planning is unchanged.

## Alternatives rejected

- **Letting a team replace the prompt:** it would drop the output format, the quoting rule and the placeholder rule, which the code that reads the answer and the privacy model both rely on.
- **One box for every call:** "write for a field manager" means nothing to screening, and "count camps as displaced" nothing to the Writer; mixing them makes both worse.
- **Four boxes (screening, planning, Writer, Critic):** planning already takes the person's own words in the chat, and a Critic steered by the team would stop being an independent check. Two boxes is what was asked for.
