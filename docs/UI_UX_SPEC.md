# humani-t: UI and UX specification

This document says what the humani-t web interface shows, how it behaves, and why. It is written for the people who will design and build `apps/web`, and for the team reviewing those choices.

> **Status: proposal.** Nothing here is built yet. Fields marked **(new, needs API)** don't exist in the API or the CHITCHAT prototype today and need a backend change before the screen can use them.

## Contents

0. [About this document](#0-about-this-document)
1. [Principles](#1-principles)
2. [What makes it different](#2-what-makes-it-different)
3. [Information architecture](#3-information-architecture)
4. [Layout](#4-layout)
5. [The condensed chat](#5-the-condensed-chat)
6. [Screens](#6-screens)
7. [History and project grouping](#7-history-and-project-grouping)
8. [Trust, uncertainty and privacy patterns](#8-trust-uncertainty-and-privacy-patterns)
9. [Visual design system](#9-visual-design-system)
10. [Component inventory](#10-component-inventory)
11. [Interaction and state](#11-interaction-and-state)
12. [Accessibility](#12-accessibility)
13. [Low bandwidth and field use](#13-low-bandwidth-and-field-use)
14. [Microcopy](#14-microcopy)
15. [Delivery phases](#15-delivery-phases)
16. [Open questions](#16-open-questions)
17. [References](#17-references)

---

## 0. About this document

### Who it is for

- Front-end developers building `apps/web` (Vite, React, strict TypeScript; see [repo_structure.md](repo_structure.md)).
- Designers making mock-ups and prototypes from it.
- Reviewers checking that the interface keeps the security and privacy rules in [architecture.md](architecture.md) and [WEB_APP_BRIEF.md](WEB_APP_BRIEF.md).

### What it builds on

| Source | What this document takes from it |
|---|---|
| CHITCHAT `docs/WEB_APP_REFERENCE.md` | Job events, the search-planning conversation, the agent loop, the decision-brief screen, the API list |
| CHITCHAT `src/chitchat/agents/schemas.py` | The exact fields of the plan, the brief, claims, conflicts and the Critic's scores |
| CHITCHAT `src/chitchat/ui.py` and `agents/render.py` | The terminal's condensed boxes and their wording, which already work as a small version of this design |
| humani-t [architecture.md](architecture.md) | Projects as the organising unit (§4.1), reviews and run manifests (§4.2), vocabulary origin (§4.3), gates (§4.4), masking review (§3.9.1), security headers and errors (§5) |
| `chitchat-ui` (the first mock-up) | A few small components and the flat, hairline style; its colours and type are replaced by LiGHT's brand (§9) |
| LiGHT Brand Guidelines (August 2026) and [light-laboratory.org](https://www.light-laboratory.org/) | Colours, type, shapes, logos and the navigation, button and footer patterns (§9.9) |
| Research (section [17](#17-references)) | The patterns for disclosure, trust, evidence display, trade-offs, history and low bandwidth |

### Decisions this document depends on

- **The decision brief.** The agent loop's output is a `DecisionBrief` with a fixed schema: options, trade-offs, conflicts, considerations, gaps ([architecture.md](architecture.md) §4.2). It replaced the earlier `Synthesis`.
- **Run events.** A run streams `stage.started`, `stage.progress`, `stage.finished`, `agent.step`, `gate.required`, `gate.resolved` and `job.finished`. The gate events are humani-t's additions to CHITCHAT's job events. [architecture.md](architecture.md) §4.4 defines them, with how a gate is resolved and how a dropped stream resumes. Section [11.2](#112-live-events) says what the interface does with each.
- **API paths.** Most "Source" lines name the CHITCHAT prototype's project-scoped paths (`/projects/{id}/plan`, `/projects/{id}/brief`, …), because humani-t hasn't defined those endpoints yet. Runs and gates use humani-t's paths from [architecture.md](architecture.md) §4.4. [api.md](api.md) is the reference once an endpoint exists.
- **The chitchat-ui mock-up** was built for a different tool (ethical trade-off cards from fixed fields). Only its look carries over, not its screens.

---

## 1. Principles

Each principle has a short reason and its source. When two principles pull against each other, the earlier one wins.

1. **Nothing that matters is hidden.** The caveat, failed checks, privacy warnings and "this is a hypothesis" labels are always visible. Detail can be folded away; warnings cannot. *Source: CHITCHAT brief screen rules; Springer and Whittaker on selective transparency.*
2. **One glance, then depth.** Every piece of output has a one-line version. The user opens more only when they want it. A chat turn never shows what a canvas can show better. *Source: progressive disclosure research; the terminal UI's summary boxes.*
3. **Checking is cheaper than trusting.** Every claim is one click from the passage that supports it. Explanations only reduce over-reliance when checking is easy (Vasconcelos et al.). Citations raise trust even when they are random (Ding et al.), so a citation count is never shown as a quality signal on its own.
4. **Words and icons, never colour alone.** Every colour that means something (risk, benefit, strength, label) also has a word and an icon. *Source: WCAG 2.2 1.4.1; CHITCHAT reference §5.*
5. **Say what was sent.** Whenever text goes to a model, the user can see exactly what went, with the masked parts marked. *Source: humani-t security rules; `question_sent` in CHITCHAT.*
6. **The person decides.** The brief compares options; it doesn't pick one. The interface never shows a "winner", a total score per option or a "recommended" badge. *Source: ICRC AI policy (human-centred, precautionary); the fixed caveat.*
7. **Uncertainty in plain words.** Evidence strength uses four fixed words, with the same meaning everywhere, explained on hover. Hedged wording is preferred to confident wording when evidence is weak (Kim et al.). *Source: GRADE and Cochrane Summary of Findings tables.*
8. **Text first, pictures later.** The page is useful before charts, fonts and avatars load. *Source: low-bandwidth guidelines (Aptivate, UK DFID); GOV.UK progressive enhancement.*
9. **Projects, not chats.** The thing a person returns to is a project with its reviews, briefs and questions. Conversations live inside it. *Source: [architecture.md](architecture.md) §4.1–4.2.*
10. **Reproducible by default.** Every output names the model, the run and the vocabulary version that made it. *Source: `RunManifest`; CHITCHAT model policy.*

---

## 2. What makes it different

Most research assistants are a chat window with long answers, or a dashboard with no conversation. humani-t combines a short conversation with rich views, and adds the parts a humanitarian decision needs.

| Idea | What the user sees | Why it is different |
|---|---|---|
| **A chat of cards, not of essays** | Each system turn is a compact card: one headline, a few facts, one main action. The full view opens beside the chat | ChatGPT-style answers become walls of text. Here the chat stays readable after an hour of work |
| **One live run strip** | A single line that grows while a job runs: "Screening 34 of 90 · 12 relevant so far · about 6 min left". Click to see the stages; click again to see each paper | Deep-research tools show a scrolling log. The strip replaces it with one updating line |
| **A brief built for trade-offs** | Options side by side, a criteria × options matrix, where the evidence disagrees and why, and "if this matters most, choose that" | Tools like Elicit or Consensus summarise findings. None lays out options against humanitarian criteria |
| **Disagreement with a reason** | Each conflict says what kind it is: stronger vs weaker studies, different settings, or small and unclear studies | Scite labels support and contrast; it doesn't explain why sources disagree |
| **Privacy receipts** | Each question shows "Sent as: …" with masked parts as labelled pills, and a count of what was masked | No mainstream assistant shows what left the machine |
| **Claim-level evidence** | Each claim is labelled evidence or reasoning, and opens its passage in one click | RELIC and DxHF show that claim-level views help people check long answers |
| **Gates in the conversation** | When a job needs a person (confirm the vocabulary, review masking), a card appears in the chat and in the "Needs you" list | The pause is part of the flow, not a separate admin screen |
| **History as a project timeline** | Each project shows its reviews, runs, briefs and questions on one timeline, with "Compare runs" | Chat products show a flat list of conversations |

---

## 3. Information architecture

### 3.1 Objects

```text
Organisation (one deployment)
└── Collection          optional group of projects, set by people (new, needs API)
    └── Project         the unit of access, encryption and deletion
        ├── Members     owner | reviewer | viewer
        ├── Documents   the user's own files, masked, reviewed before use
        ├── Review      a question, a vocabulary version, criteria
        │   ├── Plan conversation   clarifying questions, drafts, change requests
        │   ├── Runs                one job each: stages, outcome, RunManifest
        │   ├── Papers              screening results, labels, evidence
        │   └── Brief               the decision brief, its versions, its trace
        └── Questions   follow-up conversations (ask), each tied to the project's index
```

- **A project can hold several reviews.** In the first version, the UI can assume one review per project and grow later. The URLs below already leave room for more.
- **Collections** are one level deep (no folders inside folders). A project is in at most one collection. **Tags** are free labels, many per project. Section [7](#7-history-and-project-grouping) explains the choice.

### 3.2 Site map

```text
/                               Home: needs you, pinned, recent
/projects                       All projects: cards or list, grouped and filtered
/c/:collection                  One collection
/p/:project                     Project workspace (chat + canvas), opens the latest review
/p/:project/r/:review           A review's conversation
/p/:project/r/:review/draft     Canvas: the search draft
/p/:project/r/:review/run/:run  Canvas: a run's progress or summary
/p/:project/r/:review/papers    Canvas: screening results
/p/:project/r/:review/brief     Canvas: the decision brief (latest version)
/p/:project/r/:review/brief/:v  A given brief version
/p/:project/ask/:thread         A follow-up conversation
/p/:project/documents           Documents and their masking status
/p/:project/history             The project's timeline
/p/:project/compare?a=…&b=…     Compare two runs
/p/:project/settings            Members, tags, collection, deletion
/search?q=…                     Search across projects
/settings                       Account: sessions, password, MFA
/admin/users                    Admin: invitations, users
/admin/evaluations              Admin: evaluations
/sign-in, /mfa, /reset          Sign-in flow
```

Canvas URLs are real routes, so a link to a brief or a passage opens the workspace with that canvas already open. A passage is a query parameter on any canvas: `…/brief?source=P0012`.

### 3.3 Roles

| Action | Owner | Reviewer | Viewer |
|---|---|---|---|
| Read the chat, briefs, papers, history | yes | yes | yes |
| Ask follow-up questions | yes | yes | no |
| Start a review, answer clarifying questions, accept a draft | yes | yes | no |
| Start, rerun or cancel a run | yes | yes | no |
| Confirm a vocabulary gate | yes | yes | no |
| Upload or delete documents | yes | no | no |
| Review masking, see raw text | yes (audited) | no | no |
| Edit label rules and relabel | yes | yes | no |
| Manage members, tags, collection | yes | no | no |
| Delete the project | yes (recent sign-in) | no | no |

The interface hides actions a role can't do instead of greying them out, except where a viewer would otherwise not know the action exists ("Only owners can upload documents"). Non-members never see the project; links return the normal "not found" page (the API returns 404, never 403).

---

## 4. Layout

### 4.1 Three zones

The workspace has three zones. The chat is always the centre; the canvas is where big things open.

```text
┌──────────────┬───────────────────────────────┬───────────────────────────────────────┐
│ RAIL         │ CHAT                          │ CANVAS                                │
│              │                               │                                       │
│ ⌕ Search  ⌘K │ Project: Cash vs vouchers …   │ Decision brief                  ⤢  ✕ │
│              │ ───────────────────────────── │ ───────────────────────────────────── │
│ ● Needs you 2│  You                          │ Should we switch from in-kind food    │
│              │  Should we switch from …      │ aid to cash in …?                     │
│ PINNED       │                               │                                       │
│  Cash vs vou…│  ⛨ Sent as: … ⟨LOCATION 1⟩ …  │ Bottom line                           │
│  Biometrics …│                               │ Cash transfers are likely …           │
│              │  ▸ Search plan · 3 categories │                                       │
│ COLLECTIONS  │    Accepted                   │ ┌ Cash ─────────┐ ┌ Vouchers ──────┐  │
│ ▾ Sahel 2026 │                               │ │ ●●○ Moderate  │ │ ●○○ Weak       │  │
│    Cash vs v…│  ✓ Run finished · 27 min      │ │ + …  [P0012]  │ │ + …  [P0031]   │  │
│    Market as…│    52 relevant of 340         │ │ − …  [P0007]  │ │ − …  [D0002]   │  │
│ ▸ Protection │                               │ └───────────────┘ └────────────────┘  │
│              │  ◆ Decision brief ready       │                                       │
│ RECENT       │    Cash · Vouchers · In-kind  │ Trade-offs                            │
│  …           │    2 disagreements · 3 gaps   │ …                                     │
│              │    [Open brief]               │                                       │
│              │                               │                                       │
│ + New project│ ┌───────────────────────────┐ │ ⚠ This brief is an aid to a decision, │
│              │ │ Ask a follow-up…        ⏎ │ │   not the decision. …                 │
│ ⚙  Fiifi D. │ └───────────────────────────┘ │                                       │
└──────────────┴───────────────────────────────┴───────────────────────────────────────┘
   240 px              min 380, max 640 px                 the rest, min 480 px
```

| Zone | Holds | Behaviour |
|---|---|---|
| **Rail** | Search, Needs you, pinned projects, collections, recent projects, new project, account | Collapses to icons (72 px) with `[`. Remembers its state per browser |
| **Chat** | The project's conversation as cards, and the composer | Always visible on desktop. Scroll position is kept when the canvas changes |
| **Canvas** | One large view at a time: draft, run, papers, brief, trace, documents, compare | Opens from a card's main action. Resizable by dragging the divider. `⤢` makes it full width (the chat becomes a 56 px strip showing the latest turn). `Esc` or `✕` closes it |

Inside the canvas, a **source panel** can slide over from the right edge (400 px) to show a passage, without closing what is underneath. Section [6.10](#610-source-panel).

**Density.** Everything after sign-in is drawn at 75% of its natural size (CSS `zoom: 0.75` on the app frame, `COMPACT_ZOOM` in `apps/web`), the owner's chosen density. That covers the header, rail, chat, every canvas and panel, the Home, Projects and Account pages, and dialogs. Sign-in, enrolment and recovery pages stay full size. It is the design, not a preference. The frame sizes itself in percentages of the window, never `100vh`, because zoom scales viewport units too. The page itself never scrolls; its content area does.

**Resizable panels.** On screens 1024px and wider, a handle between the chat and an open canvas resizes the chat: 440px to 960px of the zoomed page (330px to 720px on screen), 640px (480px on screen) at first. On screens 768px and wider, a handle on the source panel's left edge resizes it: 320px to 640px, 400px at first. The pointer shows ↔ over a handle. Drag it, or focus it and use the arrow keys (16px a step), Home and End. A double click restores the first width. Each width is kept in this browser only (`humani-t:width:chat`, `humani-t:width:source`). Narrower screens have no handles.

**Chat roles.** Readers tell the speakers apart at a glance, and colour is never the only sign:

- **The person's own words** (the question, change requests) sit on the right as a tinted bubble marked "You".
- **Humani-T's cards** sit on the left, plain.
- **What needs the person** has a blue bar on the left and a hand icon.
- **A problem** has a purple bar, an alert icon and the word "Problem".

Yellow marks a caution only as an icon or a bar, never as text colour: yellow text is hard to read on white.

**Wording.** System text is short and plain, like a person would say it: "Double-check codes and numbers.", "Sent, but no answer came back." Masked words show as a small tinted tag reading "Location 1"; screen readers hear "masked location 1".

**Run stages** are a vertical stepper. Each stage has a marker joined to the next by a line: filled with a tick when done, a spinning ring while running, a blue hand when waiting for the person, purple with a cross when stopped, and hollow and numbered before it starts. The current stage carries `aria-current="step"`.

**Paper labels** are small tinted tags with an icon and a word: Highly relevant (badge tick), Relevant (circled tick), Possibly relevant (circled dot), Uncertain (circled question mark), Excluded (circled minus), Not screened (dashed circle).

**Full width.** On screens 1024px and wider, a panel's header has a Full width toggle that hides the chat so the panel takes the workspace. Esc gives the chat back first, and closes the panel on a second press.

**PRISMA flow.** The papers panel opens on the PRISMA 2020 flow: Identified → Unique → Screened → Included. Each step shows what left beneath it. Included, excluded, uncertain and not screened are buttons that filter the table, which keeps its rows while a new filter loads.

**Downloads (Wave 2).** The papers and brief panels have a **Download** button in their header. It opens a list of plain file links, each with its run's id:

- **Papers panel:** Papers (CSV), Papers (JSON lines), Included papers for a reference manager (RIS), PRISMA report, and Run record.
- **Brief panel:** Brief (Markdown), Brief to print or save as PDF (opens in a new tab; "Save as PDF" in the browser's print dialog), and Brief (JSON).

Every member may download what they may read. The API records each download in the audit log.

**Controls.**

- **Card actions:** a card's main action is a solid button; its other actions are outlined buttons with an icon, on one row.
- **Show and hide:** every section that opens and closes uses one control, a quiet button with an arrow that points right when the section is closed and down when it is open.
- **Underlined text:** used only for links inside text and in one-line rows.
- **Screening criteria:** shown in words, never by their ids (I1, E1). The groups are "A paper is included only if it meets all of these" and "A paper is excluded if any of these apply". A paper's reasons say "Doesn't meet: …" for an inclusion criterion it missed.

### 4.2 Breakpoints

| Width | Layout |
|---|---|
| ≥ 1280 px | Rail, chat and canvas side by side |
| 1024–1279 px | Rail collapsed to icons; chat and canvas side by side |
| 768–1023 px | Chat full width; the canvas opens as an overlay from the right (90% width); the rail is a drawer |
| < 768 px | One column. The canvas opens as a full-screen sheet with a back arrow; the source panel is a bottom sheet. The rail is a drawer from the menu button |

```text
Phone (< 768 px)

┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│ ☰  Cash vs vouchers ⋯ │   │ ←  Decision brief   ⋯ │   │ ←  Decision brief     │
│───────────────────────│   │───────────────────────│   │───────────────────────│
│ ◆ Decision brief ready│   │ Bottom line           │   │ …                     │
│   Cash · Vouchers · … │   │ Cash transfers are …  │   │┌─────────────────────┐│
│   [Open brief]        │──►│                       │──►││ P0012 · 2023        ││
│                       │   │ Cash  ●●○ Moderate  ▸ │   ││ "Households receiv… ││
│ ┌───────────────────┐ │   │ Vouchers ●○○ Weak   ▸ │   ││ [Open paper]        ││
│ │ Ask a follow-up…  │ │   │ …                     │   │└─────────────────────┘│
└─┴───────────────────┴─┘   └───────────────────────┘   └───────────────────────┘
   chat                        canvas as a sheet           source as a bottom sheet
```

On phones the option cards collapse to one line each (name and strength), and the trade-off matrix becomes one card per criterion (section [6.9](#69-decision-brief)).

### 4.3 Outside the workspace

Home, Projects, Search, Settings and Admin are single-column pages (max 1120 px wide) with the rail on the left. They don't have a chat or a canvas.

---

## 5. The condensed chat

### 5.1 Four levels of detail

Every system output exists at four levels. The chat only ever shows the first two.

| Level | Name | Where | Size | Example |
|---|---|---|---|---|
| **L0** | Glance | A chat line, the run strip, a history row | One line, up to 3 status chips | "◆ Decision brief ready · 3 options · 2 disagreements" |
| **L1** | Card | A chat card | A headline, 3 to 5 facts, one main action, up to 2 more | The brief card: bottom line, options with strength, counts, Open brief |
| **L2** | Canvas | The canvas | The full view | The whole decision brief |
| **L3** | Source | The source panel, the trace, "Sent as" | The underlying evidence | The passage behind `[P0012]`, the Critic's notes, the exact masked text sent |

The levels nest: L0 expands into L1 in place; L1's main action opens L2; any citation, number or step in L2 opens L3.

### 5.2 Rules for chat cards

- **Words.** A card has at most 60 words of body text, not counting lists of names (options, categories). The brief's bottom line (2 or 3 sentences) is the only longer text allowed in the chat.
- **Chips.** At most 3 status chips per card.
- **Actions.** One main action (a filled button) and at most 2 others (text buttons). Rare actions go in the card's `⋯` menu.
- **Numbers.** Use counts, not percentages, unless the total is shown next to them ("52 of 340").
- **No raw lists in the chat.** More than 5 items (papers, queries, passages) means "Open …" to the canvas.
- **Collapse when replaced.** When a newer card makes an older one out of date (a new draft replaces the old one, a finished run replaces its progress), the older card folds to its L0 line with "replaced by draft 3" or "finished". It can still be opened.
- **Warnings stay.** A card that carries a warning (failed checks, masking warning) never folds below a line that keeps the warning visible.
- **Time.** Each card shows a relative time ("2 min ago") with the full timestamp on hover and for screen readers.
- **Authors.** In a shared project, user turns show the person's name. System cards show which part of humani-t made them: "Search planner", "Run", "Brief writer", "Privacy".

### 5.3 Card types

Each card type below has its L0 and L1 form, what opens it to L2, and where its data comes from.

#### 5.3.1 Your question

```text
L1  ┌──────────────────────────────────────────────────────────────┐
    │ Fiifi D. · 10:42                                             │
    │ Should we switch from in-kind food aid to cash transfers in  │
    │ the displacement camps near Gao? We worry about markets.     │
    │ Deep search                                                  │
    └──────────────────────────────────────────────────────────────┘
```

- Plain text, never truncated in the chat (questions are short).
- The mode (Shallow or Deep) shows under it.
- Source: `POST /projects/{id}/plan` request.

#### 5.3.2 Privacy receipt

Shown right after any user text that goes to a model: the question, clarifying answers, change requests, follow-up questions.

```text
L0  ⛨ Sent with 2 items masked · Show what was sent

L1  ┌ ⛨ What the model received ───────────────────────────────────┐
    │ Should we switch from in-kind food aid to cash transfers in   │
    │ the displacement camps near ⟨LOCATION 1⟩? We worry about      │
    │ markets.                                                      │
    │                                                               │
    │ Masked: 1 location. Masking is not anonymity: check codes     │
    │ and numbers yourself. Learn more                              │
    └───────────────────────────────────────────────────────────────┘

L0 when nothing was masked:   ⛨ Sent as written · nothing to mask
```

- Masked items are pills with the entity type and a number: `⟨PERSON 1⟩`, `⟨LOCATION 1⟩`. Pills have a dashed border and the type spelled out for screen readers ("masked location 1").
- Counts only, never the original values, except for the owner in the masking review screen.
- Default is L0. It opens to L1 by itself the first time in a project, and whenever something was masked in a deep search.
- Source: `question_sent` and the masking counts from the plan and question endpoints.

#### 5.3.3 Clarifying questions (deep mode)

```text
L1  ┌ Search planner · 4 questions before the search ──────────────┐
    │ 1 of 4  Which outcomes matter most?                          │
    │  (Food security) (Market prices) (Protection risks) (Other…) │
    │                                                              │
    │ [Answer]   Skip this one   Skip all and draft now            │
    └──────────────────────────────────────────────────────────────┘

L0 after answering:  ✓ 4 clarifying questions answered · Show answers
```

- One question at a time in the chat, with its suggestions as chips. A chip fills the answer; the user can edit before sending. "Other…" focuses the composer.
- "Answer all at once" in `⋯` opens all four as a form in the canvas, for people who prefer that.
- Each answer gets its own privacy receipt (L0 only, unless something was masked).
- Source: `ClarifyingQuestions`; `POST /projects/{id}/plan/answers`.

#### 5.3.4 Search draft

```text
L1  ┌ Search planner · Draft 2 ────────────────────────────────────┐
    │ 3 categories · 9 concepts · OpenAlex, Europe PMC, arXiv      │
    │                                                              │
    │ 1  cash transfers AND food security AND displacement         │
    │ 2  cash transfers AND local markets AND prices               │
    │ 3  vouchers AND humanitarian AND outcomes                    │
    │                                                              │
    │ ⓘ 2 changes made automatically · see notes                   │
    │                                                              │
    │ [Accept and search]   Open draft   Ask for changes           │
    └──────────────────────────────────────────────────────────────┘

L0 when replaced:  Draft 1 · 3 categories · replaced by draft 2
L0 when accepted:  ✓ Search plan accepted · 3 categories · Open
```

- Each category is one line: its concepts joined by AND, synonyms hidden. "Open draft" shows the synonyms in the canvas.
- Automatic fixes (`plan_problems`, `normalize_plan`, `fit_to_limit`) are counted, with the notes one click away.
- "Ask for changes" focuses the composer with a hint: "Say what to change, for example: add 'mobile money' as a synonym for cash."
- **The vocabulary gate.** "Accept and search" is the confirmation of the vocabulary (architecture §4.3). After it, the draft becomes version-numbered and frozen. The accepted card says which version.
- Source: `SearchPlan` from `POST …/plan`, `…/plan/answers`, `…/plan/changes`; `POST …/plan/accept`.

**Changing the search after a run (Wave 2, prompt-first).** Once a plan is accepted, and after a run, the chat box is labelled "Change the search". The person writes what to change; the next draft card appears after the run, in time order, and shows what changed. Its main button says what accepting will run:

- **Accept and search again:** the search terms changed, so it runs every stage.
- **Accept and re-screen:** only the criteria changed, so it re-judges the papers already found, from Screen onwards.
- **Accept:** nothing changed, so nothing runs.

The API decides which (`rerun_from`); the person never picks stages. While a run is going, the box says "Wait for the run to finish, or cancel it, to change the search." Follow-up questions about results come with the follow-up threads later in Wave 2.

#### 5.3.5 Run strip

The run strip is one card that updates in place for the whole job. It never adds new chat turns while it runs.

```text
L0 running   ◐ Screening · 34 of 90 · 12 relevant so far · ~6 min left        ▸
L0 waiting   ⏸ Waiting for you · Review masking in 2 documents               [Review]
L0 done      ✓ Run finished · 27 min · 52 relevant of 340 papers              ▸
L0 failed    ✗ Run stopped at Screening · The model server isn't answering    ▸
L0 cancelled ■ Run cancelled at Ingest · finished stages are kept             ▸

L1 (click ▸)
    ┌ Run 3 · started 10:51 ───────────────────────────── [Cancel] ┐
    │ ✓ Queries      2 s     3 queries                             │
    │ ✓ Discover     1 m     412 found · 340 after duplicates      │
    │ ✓ Ingest       4 m     298 full texts · 42 abstracts only    │
    │ ✓ Mask         31 s    1,204 items masked                    │
    │ ◐ Screen       9 m     34 of 90 ▓▓▓▓▓▓░░░░░░  12 relevant    │
    │ ○ Export                                                     │
    │ ○ Index                                                      │
    │ ○ Brief                                                      │
    │                                                              │
    │ Latest: HIGH  "Cash transfers and food prices in Mali…"      │
    │ Open run details                                             │
    └──────────────────────────────────────────────────────────────┘
```

- Stage names in the UI are words a non-technical user knows: Queries, Discover, Ingest (later "Read papers"), Mask, Screen, Export, Index ("Prepare for questions"), Brief. The internal name shows in the tooltip.
- "Time left" shows only after 10 items, and is always "~" (an estimate).
- During the Brief stage, the L1 card shows the agent step in plain words: "Writer · round 2 · rewriting with 5 new passages", from `agent.step`.
- "Open run details" opens the canvas with every stage, each paper as it is screened, and the agent timeline (section [6.7](#67-run-progress)).
- One strip per run. A rerun adds a new strip; the old one folds to L0.
- Source: SSE events `stage.*`, `agent.step`, `gate.*`, `job.finished`.

#### 5.3.6 Gate request

A gate pauses the job until a person acts. It appears as a chat card, in the run strip, and in "Needs you" (section [7.1](#71-home)).

```text
L1  ┌ ⏸ Needs you · Privacy review ────────────────────────────────┐
    │ 2 uploaded documents need a masking check before any model   │
    │ reads them. The run continues when you approve.              │
    │                                                              │
    │ field_report_march.docx   14 masked · 3 to check             │
    │ meeting_minutes.txt        6 masked · 0 to check             │
    │                                                              │
    │ [Review masking]                       Only owners can do this│
    └──────────────────────────────────────────────────────────────┘
```

- Gate kinds: `vocabulary` (only when a run is started with an unconfirmed vocabulary; normally the draft card covers it) and `privacy_review`.
- Members who can't resolve the gate see who can.
- Source: `gate.required {gate_id, kind, payload}`; `POST /api/v1/gates/{gate_id}`.

#### 5.3.7 Screening summary

Posted once, when the Screen stage finishes.

```text
L1  ┌ Screening · 340 papers ──────────────────────────────────────┐
    │ ▮ 18 Highly relevant  ▮ 21 Relevant  ▮ 13 Possibly relevant   │
    │ ▯ 241 Excluded  ▯ 39 Skipped  ⚠ 8 Failed (retried next run) │
    │                                                              │
    │ [Open papers]   PRISMA diagram   Download                    │
    └──────────────────────────────────────────────────────────────┘
```

- The counts are also a small stacked bar (with labels, never colour alone).
- "Failed" uses the amber warning style, so it is not missed.
- Source: `stage.finished` summary for `screen`; `GET /projects/{id}/papers`.

#### 5.3.8 Brief card

```text
L1  ┌ ◆ Decision brief · round 2 of 3 · accepted ──────────────────┐
    │ Cash transfers are likely to improve food security where     │
    │ markets work, but evidence for camps with weak markets is    │
    │ thin. Vouchers limit price risk but cost more to run.        │
    │                                                              │
    │ Cash ●●○ Moderate · Vouchers ●○○ Weak · In-kind ●●○ Moderate │
    │ 2 disagreements · 3 gaps · 24 sources                        │
    │                                                              │
    │ ⚠ An aid to a decision, not the decision. Check the sources. │
    │                                                              │
    │ [Open brief]   How this was made   Ask a follow-up           │
    └──────────────────────────────────────────────────────────────┘
```

- The bottom line is shown whole; it is the one place the chat carries the answer itself.
- Options are listed with their evidence strength (icon and word). There is no ranking and no highlight on any option.
- The short caveat line is always present and links to the full caveat.
- If checks still fail (`warnings`), an amber line replaces the counts line: "⚠ 1 check still failing · see the brief".
- "How this was made" opens the trace (section [6.11](#611-how-this-was-made)).
- Source: `GET /projects/{id}/brief`.

#### 5.3.9 Follow-up answer (ask)

```text
L1  ┌ Answer ──────────────────────────────────────────────────────┐
    │ Two studies report price rises of 5–12% in the first month   │
    │ after cash distributions in small markets [P0012] [P0044];   │
    │ one found no effect in larger towns [P0031].                 │
    │                                                              │
    │ 3 sources · ⛨ 1 item masked                                  │
    │ Open sources   Add to brief notes                            │
    └──────────────────────────────────────────────────────────────┘
```

- Answers longer than 60 words show the first 60 and "Read all" (expands in place). The full answer is never moved to the canvas, because it belongs to the conversation.
- Citation chips work the same as in the brief.
- "Add to brief notes" pins the answer to the brief's notes section **(new, needs API)**.
- Source: `POST /projects/{id}/questions`.

**Built so far (Wave 2):** the answer card ("Answer") shows its claims with the brief's citation chips and reasoning marks, then one line: the number of sources and how many items of the question were masked. A chip opens the source panel over the chat, with the masked passages the answer drew on (an answer may cite passages the brief never cited, so it does not reuse the brief's evidence); for a paper it also shows the title and year, read from `GET /reviews/{id}/papers/{paper_id}?run_id=` with the answer's `run_id`. Closing the panel, with Close or Esc, gives focus back to the chip. "Nothing in this project answers that." shows as plain text, with **Change the search** for owners and reviewers (see 6.12). A failed answer is a problem card, "The answer couldn't be made", with the API's reason and **Try again**, which asks again in the same thread. See [6.12](#612-ask-follow-up-questions).

#### 5.3.10 Error

```text
L1  ┌ ✗ The search plan couldn't be made ──────────────────────────┐
    │ The model server isn't answering. Your question is saved.    │
    │                                                              │
    │ [Try again]   Details                                        │
    └──────────────────────────────────────────────────────────────┘
```

- The headline and message come from the problem document's `title` and `detail` (RFC 9457). They are written in plain language by the API.
- "Details" shows the error code and request id, for support. Never stack traces or raw text.

#### 5.3.11 System note

One-line notes for things that happened outside the chat: "Maya K. joined as reviewer", "Labels changed by new rules: 4 papers moved", "Project moved to Sahel 2026". They are L0 only, grey, and can be hidden with a filter.

### 5.4 The composer

- One text box at the bottom of the chat. What it does depends on the moment, and its placeholder says so:

  | Moment | Placeholder | Sends to |
  |---|---|---|
  | New project, no review yet | "What decision do you need evidence for?" | Starts a review (`POST …/plan`) |
  | A clarifying question is open | "Your answer, or pick a suggestion above" | `…/plan/answers` |
  | A draft is open and not accepted | "Say what to change in the search" | `…/plan/changes` |
  | A run is going | "Ask about what's already indexed, or wait for the brief" | `…/questions` (disabled until the index exists) |
  | A brief exists | "Ask a follow-up" | `…/questions` |

- A small label above the box names the target ("Changing the search · draft 2"), with `✕` to switch to a follow-up question instead.
- **Review before sending** (optional, per project setting): the composer shows the masked version and asks for a confirm before it goes. It is the "review before sending" step in the brief.
- Enter sends; Shift+Enter adds a line. The box grows to 6 lines, then scrolls.
- Attach (📎) uploads documents to the project (owners only), which then go through the privacy review.

### 5.5 A whole review, condensed

This is what the chat looks like after a full deep review. Everything not shown is one click away.

```text
Fiifi D.   Should we switch from in-kind food aid to cash transfers in the camps near Gao?
           Deep search
⛨          Sent with 1 item masked · Show what was sent
✓          4 clarifying questions answered · Show answers
           Draft 1 · 3 categories · replaced by draft 2
Fiifi D.   Add mobile money as a synonym for cash.
✓          Search plan accepted · version 2 · 3 categories · Open
✓          Run finished · 27 min · 52 relevant of 340 papers                ▸
           Screening · 18 highly relevant · 21 relevant · 13 possibly · 8 failed
◆          Decision brief · round 2 of 3 · accepted
           Cash transfers are likely to improve food security where markets work, …
           Cash ●●○ Moderate · Vouchers ●○○ Weak · In-kind ●●○ Moderate
           2 disagreements · 3 gaps · 24 sources
           ⚠ An aid to a decision, not the decision.
           [Open brief]  How this was made  Ask a follow-up
Fiifi D.   How fast do prices go up after a cash distribution?
           Answer · Two studies report price rises of 5–12% … [P0012] [P0044] · 3 sources
```

Twelve lines summarise an hour of machine work. The terminal version of CHITCHAT prints several hundred.

---

## 6. Screens

Each screen lists its purpose, how people reach it, its layout, its states, and where its data comes from. States use the same five names everywhere:

| State | Meaning | Default look |
|---|---|---|
| **Empty** | Nothing exists yet | A short sentence on what goes here and one action |
| **Loading** | Waiting for the first data | Skeleton lines in the shape of the content, never a full-page spinner |
| **Partial** | Some data, more coming (a job is running) | The data so far, with a "still running" line |
| **Error** | The request failed | The plain message from the problem document, and a retry where it makes sense |
| **No access** | The role can't do this | Say who can ("Only owners can …") |

### 6.1 Sign-in, MFA and re-authentication

**Purpose.** Get people in safely, with no hints about which accounts exist.

- **Sign-in:** email and password, then the second factor. One error message for every failure: "That email and password don't match." Throttling shows "Too many attempts. Try again in 40 seconds" with a live countdown (from `Retry-After`).
- **Second factor:** the passkey prompt starts by itself. Links below it: "Use an authenticator code instead" and "Use a recovery code". No SMS.
- **First sign-in from an invitation:** set a password (15 characters or more, shown with a strength hint and the blocklist message if it fails), then add a passkey (recommended) or TOTP, then save recovery codes. The recovery-code step shows the codes once, with "Download" and "I've saved them" (required to continue).
- **Re-authentication:** a modal over the current screen, not a page change, when the API returns 401 `reauthentication_required` for a sensitive action (delete a project, change members or credentials). After success the original action is retried once. The modal says why: "Deleting a project needs you to sign in again."
- **Session expired** (idle 1 hour): a banner "You've been signed out. Sign in again to continue." Unsent text in the composer is kept in memory and restored after sign-in.

Source: [api.md](api.md) auth endpoints; [architecture.md](architecture.md) §5.1–5.2.

### 6.2 Home and Projects

See section [7](#7-history-and-project-grouping).

### 6.3 New review

**Purpose.** Start from a question with as few choices as possible.

**Entry:** "+ New project" in the rail, the empty state of a new project, or "Search for this" on a gap in a brief.

```text
┌ New project ─────────────────────────────────────────────────────────┐
│                                                                      │
│ What decision do you need evidence for?                              │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Should we switch from in-kind food aid to cash transfers …       │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ Personal details are masked before anything is sent to the model.   │
│                                                                      │
│ How thorough?                                                        │
│ ( ) Quick    About 90 papers, abstracts only, no questions first.    │
│              Usually 10–20 minutes.                                  │
│ (•) Deep     About 4 questions first, hundreds of papers, full       │
│              texts where open. Up to an hour or more.                │
│                                                                      │
│ ▸ More options                                                       │
│     Project name     [Cash vs vouchers, Gao        ]                 │
│     Collection       [Sahel 2026               ▾]                    │
│     Tags             [Mali ×] [cash ×] [+]                           │
│     Your documents   [Upload files]  They are masked and checked     │
│                      before any model reads them.                    │
│     Your view first  [ ] Ask me which option I lean towards before   │
│                          showing the brief                           │
│     Search sources   OpenAlex ✓  Europe PMC ✓  arXiv ✓              │
│                                                                      │
│                                              [Start]                 │
└──────────────────────────────────────────────────────────────────────┘
```

- The UI calls the modes **Quick** and **Deep**; the API keeps `shallow` and `deep`.
- The time estimates come from the deployment (they depend on its hardware) **(new, needs API: `GET /info` estimates)**. Until then, show the ranges above.
- The project name defaults to a short version of the question, made in the browser (first 6 words); it can be changed any time.
- "Start" creates the project and sends the question in one step. The user lands in the workspace with their question as the first chat turn.
- "Bring your own vocabulary" (route B, architecture §4.3) is in "More options" as "I have my own search terms" → upload. Then the clarifying questions and the model draft are skipped, and the draft card says "Your terms, used exactly as given".

### 6.4 Clarifying questions

Covered by the chat card ([5.3.3](#533-clarifying-questions-deep-mode)). The "answer all at once" canvas is a simple form: each question, its suggestion chips, a text box, and "Skip". A progress line on top: "2 of 4 answered".

### 6.5 Search draft canvas

**Purpose.** Let people check and change the search without knowing Boolean syntax.

```text
┌ Search draft · version 2 (not accepted yet) ────────── Compare with 1 ┐
│ Your question: Should we switch from in-kind food aid to cash …       │
│ A paper must match every concept in a category (AND). Words in a     │
│ concept are alternatives (OR).                                        │
│                                                                       │
│ ┌ Category 1 · Cash and food security ─────────── OpenAlex: fits ✓ ┐ │
│ │ ┌ cash transfers ◆ ─────┐ AND ┌ food security ─┐ AND ┌ displaced ┐│ │
│ │ │ or: cash assistance,  │     │ or: hunger,     │     │ or: IDP,  ││ │
│ │ │ mobile money ✎ you    │     │ nutrition       │     │ refugees  ││ │
│ │ └───────────────────────┘     └─────────────────┘     └───────────┘│ │
│ │ 812 characters                                                    │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│ ┌ Category 2 · Markets and prices ──────────── OpenAlex: shortened ⓘ ┐│
│ │ ┌ cash transfers ◆ ─┐ AND ┌ local markets ─┐ AND ┌ prices ─────────┐││
│ │ …                                                                  ││
│ └────────────────────────────────────────────────────────────────────┘│
│                                                                       │
│ ⓘ Changes made automatically (2)                                      │
│   · "prices" and "price changes" merged                               │
│   · 3 synonyms dropped from category 2 to fit OpenAlex's limit         │
│                                                                       │
│ Searches: OpenAlex, Europe PMC, arXiv                                 │
│                                                                       │
│ [Accept and search]      Ask for changes in the chat                  │
└───────────────────────────────────────────────────────────────────────┘
```

- **Concept pills** keep the same colour in every category, so a shared concept stands out (the terminal already does this). Shared concepts also carry a ◆ mark and "in 2 categories" on hover, so colour isn't the only signal.
- **Origin** (architecture §4.3): terms added by a person carry "✎ you" (or the person's name); model terms carry nothing. A filter "Show who added each term" turns on a label for every term.
- **Compare with draft N** shows added terms in green with "+", removed in red with "−", and moved terms with "→". Words, not only colour.
- **Editing.** The first version changes the draft through the chat only, as CHITCHAT does. A later version can allow direct edits on pills (add, remove, move a synonym) **(new, needs API)**.
- **Fit indicator** per category: "fits", "shortened" (with what was dropped), or "too long" (blocks Accept, with a message).
- **After accept** the canvas becomes read-only and shows "Version 2 · accepted by Fiifi D. at 10:49 · used in run 3".

Source: `SearchPlan`, notes from `plan_problems`, `normalize_plan`, `fit_to_limit`; `plan.json`.

### 6.6 Gates: vocabulary and privacy review

**Vocabulary gate.** The search draft is the vocabulary review. A separate gate screen appears only when a run starts with a vocabulary that wasn't confirmed, and it is the draft canvas with a "Confirm and continue" button.

**Privacy review** (architecture §3.9.1). Owners only. It opens in the canvas at full width.

```text
┌ Masking review · field_report_march.docx ─────────────── 1 of 2 ─────┐
│ ⚠ Masking is not anonymity. Codes, numbers and rare details can     │
│   identify people. Text that is only in images is masked only if it │
│   was read by OCR.                                                   │
│                                                                      │
│ To check first (3)                │ Text                              │
│ ┌──────────────────────────────┐  │ … the team met ⟦PERSON⟧ at the    │
│ │ ? "Dr Traoré" · PERSON · low │  │ clinic in ⟦LOCATION⟧ on 12 March. │
│ │   [Keep masked] [Not personal]│ │ MRN 448-1207 was referred to …    │
│ │ ? "MRN 448-1207" · not masked │  │      ▲ select text to mask it    │
│ │   [Mask as ID ▾]             │  │                                    │
│ └──────────────────────────────┘  │                                    │
│ Masked (14) by type ▾             │                                    │
│ PERSON 6 · LOCATION 5 · PHONE 2 · ID 1                               │
│                                                                      │
│ [Approve this document]   Make "Traoré" a project rule               │
└──────────────────────────────────────────────────────────────────────┘
```

- Low-confidence spans first, then the rest grouped by type.
- Select text → a small menu: "Mask as … (type)" and "Always mask in this project" (a `MaskingRule`).
- "Not personal" is owner-only, audited, and not offered for structured identifiers. It asks for a reason (one line).
- Raw text is shown only here, only to the owner, and every opening writes an audit event. The header says so: "You're seeing original text. This view is recorded."
- Approving the last document resumes the run. The chat's gate card turns into "✓ Privacy review done by Fiifi D. · 2 documents".

### 6.7 Run progress

**Purpose.** Show that work is happening, how far along it is, and what came out, without a wall of logs.

The run strip (L0 and L1) is in [5.3.5](#535-run-strip). The canvas (L2) shows:

```text
┌ Run 3 · Deep · started 10:51 · running 14 min ─────────── [Cancel] ┐
│ Stages                                                              │
│ ✓ Queries   ✓ Discover   ✓ Ingest   ✓ Mask   ◐ Screen   ○ Export … │
│                                                                     │
│ Screen · 34 of 90 · ~6 min left                                     │
│ ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░                                            │
│                                                                     │
│ Filter: [All] [Relevant] [Excluded] [Failed]                        │
│ HIGH     Cash transfers and food prices in Mali, 2019–2023          │
│          matched: population, intervention, outcome                 │
│ EXCLUDE  A review of school feeding programmes                      │
│          rule: intervention not matched                             │
│ LOW      Vouchers in protracted crises: a scoping review            │
│          abstract only                                              │
│ …                                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

- **Stage row:** each stage with its state icon (✓ done, ◐ running, ○ waiting, ✗ failed, ⏸ waiting for a gate, ■ cancelled) and its duration once done. Clicking a finished stage shows its summary box (the same facts as the terminal's summary boxes).
- **Live list:** during Screen, one row per paper as it finishes, newest at the top, with its label and the one-line reason. It holds the last 200 rows; "Open papers" shows the rest.
- **Brief stage:** the live list becomes the agent timeline (section [6.11](#611-how-this-was-made)), filling in as steps finish.
- **Cancel** asks once: "Stop this run? Finished stages are kept, and you can run the rest later." Then the strip shows ■.
- **Failure:** the stage turns ✗ with the plain message and the actions "Try again from this stage" and "Details".
- **Connection lost:** a thin banner "Reconnecting…", then the strip catches up from the last event id (section [11.2](#112-live-events)). Never a blank screen.

### 6.8 Screening results

**Purpose.** Let people see which papers count, why, and change the rules without a new LLM run.

```text
┌ Papers · run 3 ──────────────────────────────────────── Download ▾ ┐
│ [Highly relevant 18] [Relevant 21] [Possibly 13] [Excluded 241]     │
│ [Skipped 39] [⚠ Failed 8]          ⌕ Search titles    Year ▾  ⋯   │
│                                                                     │
│ Label        Title                                   Year  Source   │
│ ▸ HIGH       Cash transfers and food prices in Mali   2023  OpenAlex│
│ ▾ HIGH       Mobile money in displacement settings    2021  EuropePMC│
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Population      ✓ Yes   "…internally displaced households…"  │  │
│   │ Intervention    ✓ Yes   "…unconditional cash via mobile…"    │  │
│   │ Outcome         ~ Partly "…food consumption score…"          │  │
│   │ Rule: all three met → Highly relevant                        │  │
│   │ Full text read · Open paper · Cited in the brief as [P0031]  │  │
│   └──────────────────────────────────────────────────────────────┘  │
│ ▸ MEDIUM     …                                                      │
│                                                                     │
│ PRISMA diagram ▸        Edit rules ▸                                │
└─────────────────────────────────────────────────────────────────────┘
```

- **Labels** keep the pipeline's names but show a plain name first: Highly relevant (HIGH), Relevant (MEDIUM), Possibly relevant (LOW), Excluded (EXCLUDE), Skipped, Failed. Each has an icon and a colour (section [9.2](#92-colour)).
- **Expanded row:** each criterion's answer with its quote, the rule that set the label, whether the full text or only the abstract was read, and whether the brief cites it. This mirrors Elicit's per-criterion view, which reviewers trust because it shows the quote.
- **Seen marks:** a dot on papers the user hasn't opened yet; papers opened in another project show "seen in Biometrics review" (section [7.5](#75-seen-and-saved-marks)).
- **Edit rules:** opens a side form with the label rules. "Preview" shows how many papers would move ("4 would move from Relevant to Highly relevant") before "Apply". Applying makes no LLM calls and posts a system note in the chat.
- **PRISMA diagram:** drawn as an accessible SVG with the counts as text, and a table version below for screen readers and small screens.
- **Downloads:** `dataset.csv`, `dataset.jsonl`, `prisma.md`.
- **Coverage** (later): an estimate of how much of the relevant literature was found, always labelled "estimate" (Undermind shows that such numbers are uncertain) **(new, needs API)**.

Source: `GET /projects/{id}/papers`, `PUT /projects/{id}/rules`, `GET …/exports/{name}`.

### 6.9 Decision brief

**Purpose.** Help a person weigh options with the evidence in front of them. It is the core screen.

The order of parts follows `agents/render.py::brief_markdown`. The canvas has a sticky section bar at the top: **Summary · Options · Trade-offs · Disagreements · If this matters most · Gaps · Sources**. On wide screens it sits at the left of the canvas as a table of contents.

#### Header

```text
┌───────────────────────────────────────────────────────────────────────┐
│ DECISION BRIEF · version 2 · 10:58 · qwen3.5:9b · round 2 of 3 · ✓    │
│                                                                       │
│ Should we switch from in-kind food aid to cash transfers in the       │
│ displacement camps near ⟨LOCATION 1⟩?                                 │
│                                                                       │
│ Bottom line                                                           │
│ Cash transfers are likely to improve food security where markets      │
│ work [P0012] [P0031], but evidence for camps with weak markets is     │
│ thin. Vouchers limit price risk but cost more to run [P0007].         │
│                                                                       │
│ How this was made ▸     Download ▾     Share link                     │
│                                                                       │
│ ⚠ This brief is an aid to a decision, not the decision. It covers     │
│   only the papers and documents in this run; check the cited sources  │
│   before acting.                                                      │
└───────────────────────────────────────────────────────────────────────┘
```

- The question is shown as it was sent (masked).
- The meta line: version, time, model, the round kept and the number of rounds, and the stop reason as an icon with a tooltip ("accepted by the Critic", "no new evidence and no better score", "reached the round limit (3)").
- The caveat sits in the header, always visible, never collapsible. Failed checks (`warnings`) appear under it in the same amber box.

#### Option cards

```text
┌ Cash transfers ─────────────── ●●○ Moderate evidence ┐ ┌ Vouchers ──── ●○○ Weak evidence ┐
│ Unconditional cash, by mobile money where possible.  │ │ …                               │
│                                                      │ │                                 │
│ Benefits                                             │ │                                 │
│ + Households buy what they most need [P0012] [P0031] │ │                                 │
│ + Lower delivery cost than in-kind food [P0044]      │ │                                 │
│ + Supports local traders  ⟡ reasoning, not a source  │ │                                 │
│                                                      │ │                                 │
│ Risks                                                │ │                                 │
│ − Prices can rise in small markets [P0012] [P0044]   │ │                                 │
│ − Protection risks for women receiving cash [D0002]  │ │                                 │
│                                                      │ │                                 │
│ 5 sources                                            │ │                                 │
└──────────────────────────────────────────────────────┘ └─────────────────────────────────┘
```

- **Layout:** side by side (2 to 4 cards). Below 900 px of canvas width, they stack, each collapsed to its name, strength and counts ("3 benefits · 2 risks"), and open on tap.
- **Benefits** start with `+` and use the blue `benefit.*` style; **risks** start with `−` and use the yellow `risk.*` style (§9.2.3). The words "Benefits" and "Risks" head each list.
- **Claims marked `hypothesis`** are in italics with "⟡ reasoning, not a source". They never carry citation chips.
- **Evidence strength** uses the four words and a three-dot meter (section [8.1](#81-evidence-strength)). The meter's meaning is on hover and in the "?" next to it.
- **No ranking.** Cards keep the order the Writer gave. No card is highlighted unless the user picks a priority (below).

#### Trade-off matrix

A table of criteria (rows) by options (columns). Each cell is the Writer's short assessment with its source chips.

```text
┌ Trade-offs ─────────────────────── What matters most to you? [Do no harm ▾] ┐
│                        │ Cash transfers       │ Vouchers           │ In-kind food        │
│────────────────────────┼──────────────────────┼────────────────────┼─────────────────────│
│ ◈ Do no harm           │ Protection risks if  │ Lower cash-theft   │ Lowest risk to      │
│   (humanitarian)       │ cash is visible      │ risk [P0007]       │ recipients [P0019]  │
│                        │ [D0002]              │                    │                     │
│ Food security          │ Better diet          │ Similar to cash    │ Fixed basket        │
│                        │ diversity [P0031]    │ where shops stock  │ [P0019]             │
│ Cost to deliver        │ Lowest [P0044]       │ Middle             │ Highest [P0044]     │
│ ◈ Impartiality         │ …                    │ …                  │ …                   │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **Humanitarian criteria** (humanity, impartiality, neutrality, independence, do no harm) carry ◈ and the word "humanitarian", and are listed first.
- **"What matters most to you?"** lets the user pick one or more criteria. The picked rows move to the top and get a highlight bar. If a consideration exists for that criterion ("If do no harm matters most → In-kind food, because …"), its option card gets a thin outline and the consideration shows above the matrix. This is the ValueCharts idea of putting the user's priorities on screen, **without any arithmetic**: the brief has no numeric scores per cell, and the interface must not invent them.
- **Sorting and filtering:** "Show humanitarian criteria only"; sort by original order or by the user's picks.
- **Narrow screens:** one card per criterion, with the options as rows inside it.
- **Screen readers:** a real `<table>` with row and column headers; the highlight is announced ("priority").

#### Where the evidence disagrees

```text
┌ Where the evidence disagrees (2) ─────────────────────────────────────┐
│ Do cash transfers raise local food prices?                             │
│ Different settings, different results ⓘ                               │
│ ┌──────────────────────────────┐ ┌──────────────────────────────┐      │
│ │ Yes, 5–12% in small markets  │ │ No measurable effect in      │      │
│ │ [P0012] [P0044]              │ │ larger towns [P0031]         │      │
│ └──────────────────────────────┘ └──────────────────────────────┘      │
│ What this means: the effect likely depends on market size. Camps      │
│ near Gao have small markets.  ⟡ reasoning, not a source               │
└───────────────────────────────────────────────────────────────────────┘
```

- The conflict type in plain words (section [8.3](#83-why-sources-disagree)), with an ⓘ that explains what it means for the decision.
- Positions side by side, each a claim with chips. On phones they stack with "Position 1", "Position 2".
- The "what this means" line appears only if the Writer gave a hypothesis claim for it; it is marked as reasoning.

#### If this matters most

```text
┌ If this matters most ─────────────────────────────────────────────────┐
│ Do no harm        → In-kind food   because recipients carry no cash   │
│                                    [P0019] [D0002]                     │
│ Cost to deliver   → Cash transfers because delivery costs are lowest  │
│                                    [P0044]                             │
└───────────────────────────────────────────────────────────────────────┘
```

- Clicking a row selects that priority in the trade-off matrix and outlines the option card.

#### Gaps in the evidence

```text
┌ Gaps in the evidence (3) ─────────────────────────────────────────────┐
│ · No studies from camps with fewer than 5 traders     Search for this │
│ · Little on effects after 12 months                    Search for this │
│ · Nothing on people with disabilities                  Search for this │
└───────────────────────────────────────────────────────────────────────┘
```

- "Search for this" starts a new review in the same project, with the gap as the question (it goes to the New review form, filled in, so the user can edit it first).

#### Sources

A list of every cited tag: `[P0012]` title, year, authors (first 3), DOI or link, screening label, and how many times the brief cites it. `D` tags show the document name only to members who can see documents. Sorted by first appearance. "Show passages" opens each one.

#### Quality checks

Collapsed by default, at the end.

```text
▸ Quality checks · the model's own review of this brief
  Coverage ●●  Groundedness ●●  Disagreements ●○  Uncertainty ●●
  Humanitarian awareness ●●  Specificity ●○
  These scores come from the same model that wrote the brief. They catch
  gaps and weak citations, not every error. They are not a verification.
```

- Each score is 0, 1 or 2, shown as two dots and a word on hover (poor, partly, good). No total is shown, so it can't become a trust badge.

#### Your view first (optional)

If the project turned on "Your view first", the brief canvas opens with one question before the brief:

```text
┌ Before you read the brief ────────────────────────────────────────────┐
│ Which option do you lean towards now?                                  │
│ (Cash transfers) (Vouchers) (In-kind food) (Not sure)                  │
│ Your answer is saved in the project and shown next to the brief.      │
│ Skip                                                                   │
└───────────────────────────────────────────────────────────────────────┘
```

Afterwards, the header shows "You leaned towards Cash transfers before reading." It is optional, off by default, and can be skipped every time. Research on cognitive forcing (Buçinca et al.) shows it reduces over-reliance, but people like it less, so it is a project choice **(new, needs API to store)**.

#### Versions

Each run of the Brief stage makes a new version. The header has a version menu: "Version 2 (latest) · Version 1, 10:31". "Compare with version 1" shows options added or removed, strength changes ("Cash: Weak → Moderate"), and new or removed sources **(new, needs API: brief versions are not stored separately today)**.

#### Downloads and sharing

- Download as Markdown (`brief.md`), PDF and JSON.
- PDF and print keep the caveat on every page footer.
- "Share link" copies the canvas URL. It works only for project members.

Source: `GET /projects/{id}/brief`, `…/brief/evidence/{tag}`, `…/brief/trace`, sources from `…/papers`.

### 6.10 Source panel

**Purpose.** Answer "where does this come from?" in one click, without losing your place.

```text
┌ P0012 ──────────────────────────────────────────── ✕ ┐
│ Cash transfers and food prices in Mali, 2019–2023    │
│ Diallo, Keita, Smith · 2023 · Food Policy            │
│ doi:10.1016/… ↗          HIGH · Highly relevant       │
│──────────────────────────────────────────────────────│
│ Cited for                                            │
│ "Prices can rise in small markets" (Cash, risk)      │
│                                                      │
│ Passage 1 of 2 · found by: "cash transfers prices"   │
│ "…in markets with fewer than ten traders, staple     │
│ prices rose by 5 to 12 percent in the four weeks     │
│ after distribution…"                                 │
│                                    ‹ 1 / 2 ›         │
│──────────────────────────────────────────────────────│
│ Why it was included ▸                                │
│ Save ☆     Seen in 2 other projects                  │
└──────────────────────────────────────────────────────┘
```

- The passage is the exact masked text the Writer saw (`evidence.json`), with the search that found it.
- "Cited for" lists every claim that cites this tag, so the user can check each one against the passage.
- "Why it was included" opens the paper's screening evidence (the same view as the expanded paper row).
- Keyboard: `‹ ›` move between passages; `Esc` closes.
- A chip whose tag isn't in the evidence must not happen (the checks forbid it). If it does, the chip shows "⚠ source missing" and the panel says "This citation has no passage. Report it." It is logged as an error.

### 6.11 How this was made

**Purpose.** Show the agent loop as a short story, for people who want to know how the brief was built.

```text
┌ How this brief was made ──────────────────────────────────────────────┐
│ Stopped because: accepted by the Critic · kept round 2 of 3           │
│                                                                       │
│ ● Planner          1 call · 41 s                                      │
│   Decision: choose between cash, vouchers or in-kind food in camps    │
│   Options: Cash transfers · Vouchers · In-kind food                    │
│   Criteria: do no harm, food security, cost, impartiality, …          │
│   6 sub-questions ▸                                                   │
│ ● Retriever        7 searches · 31 passages                           │
│ ─ Round 1 ────────────────────────────────────────────────────────── │
│ ● Writer           draft 1 · 3 options · 2 min                        │
│ ● Checks           1 problem: an option has no sourced benefit        │
│ ● Critic           revise · 8 of 12 ▸                                  │
│   Asked for: "price effects of cash in small markets", +2 more         │
│ ● Retriever        3 searches · 9 new passages (40 in all)            │
│ ─ Round 2 ────────────────────────────────────────────────────────── │
│ ● Writer           draft 2 · 3 options · 2 min                        │
│ ● Checks           no problems                                         │
│ ● Critic           accept · 10 of 12 ▸                                 │
│                                                                       │
│ Model: qwen3.5:9b · 9 calls · 61,200 tokens in, 8,900 out             │
└───────────────────────────────────────────────────────────────────────┘
```

- A vertical timeline, one row per `agent.step`. Each row opens to its detail: the plan, the queries, the Critic's issues and fixes, the checks' problems.
- Steps that failed (`refused`, `invalid`, `error`) show ✗ and the short error, and the stop reason explains what was kept.
- Totals at the bottom. Tokens are shown here only, never in the chat.
- Linear timelines can overwhelm when agents run in parallel; this loop is sequential, so a single column is enough. If parallel agents come later, group them in lanes.

Source: `GET /projects/{id}/brief/trace`; streamed live as `agent.step`.

**Instructions from the project team** (ADR 0015): after the totals, the trace shows the project's writing instructions exactly as the Writer received them, masked where masking applies (placeholders drawn as tags), with their version: "Version 2, as the Writer received them, masked where masking applies." When the Writer had none: "None. The Writer had only the built-in instructions." "What this used" lists "Instructions version" when there was one.

### 6.12 Ask (follow-up questions)

- Follow-up questions live in the project chat, after the brief. Questions belong to threads: a follow-up carries its thread's last few questions and answers, and `POST …/questions` names the `thread_id` to continue, or none to start one.
- A thread can be opened on its own (`/p/:project/ask/:thread`) from the history.
- Each answer: card per [5.3.9](#539-follow-up-answer-ask), privacy receipt per [5.3.2](#532-privacy-receipt).
- **Answers only from this project.** The composer hint says so: "Answers use only the papers and documents in this project."
- When nothing relevant is found, the answer card says "Nothing in this project answers that" and offers "Start a new search".

**Built so far (Wave 2):** once the latest run has a decision brief, two pressed-or-not buttons sit above the box: **Ask about the evidence** (chosen at first) and **Change the search**. Without a brief the box only changes the search. A question continues the latest thread, or starts one when there is none (`POST /reviews/{id}/questions`). Once a thread exists, an outlined **New thread** button sits beside the choice while asking is chosen; pressed (it shows as pressed, and the box's placeholder says "Ask a question to start a new thread"), the next question starts a thread, and the one after continues it. Pressed again, it goes back to continuing the latest thread. A new thread need not wait for the last answer, since the API answers one question at a time per thread. In the chat, every thread but the oldest opens with a quiet "New thread" divider. Under the conversation, a disclosure ("2 threads") lists the threads, newest first, each with its first question, how many questions it has and when it started; choosing one scrolls to its first question and moves focus there, and its **Open** link goes to the thread's own page (`/p/:project/ask/:thread`, from `GET /threads/{id}`): its questions and answers in order, a link back to the review, and, for owners and reviewers, a box that asks in that thread, so an older thread can be continued there. Viewers see the list, the dividers and the thread page too. An answer longer than 60 words shows its first 60 and **Read all**, which expands it in place. Your question shows at once with "Sending your question…", then as the API stored it (masked words as tags) with "Searching the evidence…" while the browser polls `GET /questions/{id}` every 2 seconds. The box waits until the last question is answered, unless New thread is pressed. Questions and answers sit in the chat by the time they were asked, so they come after the brief they follow. Viewers read them; their box says "You can read the answers. Only owners and reviewers can ask." When nothing in the project answers, the answer card offers **Change the search** rather than "Start a new search": it switches the box to changing the search and fills it with the question as a starting point, unless something is already typed there, which is kept. A new search is a new review; most often the question shows the current search missed something, which a change fixes. Not built yet: "Add to brief notes" and the full privacy receipt.

### 6.13 Documents

**Purpose.** Bring the team's own files in, and show what the model will see.

```text
┌ Documents · 7 ─────────────────────────────────────────── [Upload] ┐
│ Name                         Type   Status                  Masked │
│ field_report_march.docx      DOCX   ⏸ Needs review (owner)    14   │
│ meeting_minutes.txt          TXT    ✓ Approved · indexed       6   │
│ budget_2026.xlsx             XLSX   ◐ Reading…                 –   │
│ scan_registration.pdf        PDF    ⚠ Approved · images not read  2│
└────────────────────────────────────────────────────────────────────┘
```

- Accepted types: PDF, DOCX, XLSX, PPTX, EML, HTML, MD, TXT. Size limit and file checks come from the API.
- Status per file: Uploading, Reading, Masking, Needs review, Approved (and indexed), Failed.
- "What the model will see" opens the masked text for any member; the original only for the owner, in the review screen.
- A `D` tag (`D0002`) is shown next to each approved file, so people can match citations.
- Deleting a document asks for confirmation and says the brief may cite it.

**Built so far (Wave 2).** `/p/:project/documents`, opened from a Documents link in the workspace header, beside Project settings ([`features/documents/Documents.tsx`](../apps/web/src/features/documents/Documents.tsx)).

- **Upload, owners only:** choose a file or drag one onto the upload area. The accepted types and the largest file come from the API's listing; a file over the limit is refused before it is sent, and any refusal from the API (wrong type, empty, quota, rate limit, uploads switched off) is shown in the API's own words. Reviewers and viewers read "Only owners can upload documents." The upload is the client's one non-JSON body: `api/client.ts` allows multipart on the upload path only, as the API does.
- **The list:** tag, name, size, who uploaded it ("You", a member's name, or "A former member"), and the status in words: Checking for viruses, Reading the text, Waiting for privacy review, Ready, Rejected, Failed (with the API's reason). The list is fetched again every 3 seconds while any file is still being processed, and not at all otherwise.
- **The privacy review:** an owner opens a document waiting for review (`?document=<id>`, also linked from the chat's gate card). The API's notice says the original is shown and the view is recorded. The original text, with masked parts highlighted, sits beside what the model will see; below, the masks in text order with their type and what made them. "Not personal" (only where the API allows it) asks for a one-line reason; selecting words in the original offers "Mask as …" and "Always mask in this project", which adds a masking rule and masks the document again at once. Approve or reject sends the masked version on screen. Any member can open "See masked text" for a masked document, without the original or the decision buttons; the original (`…/review`) is requested only for an owner's review.
- **Delete, owners only:** a Delete button on every row, whatever the status, opens a dialog: "This deletes the file and its text. A brief that already cites D1 keeps the passages it quoted. This can't be undone." After **Delete** the list is fetched again and the page says "D1 report.txt is deleted."; if another owner deleted it first, "… was already deleted." The API asks for no recent sign-in here, so neither does the screen. Reviewers and viewers see no Delete button.
- **Not built yet:** the Type and Masked columns of the sketch above; low-confidence spans first (detectors report no confidence yet).

### 6.14 Project settings

**Built so far (Wave 2):** the page at `/p/:project/settings`, opened from the settings icon ("Project settings") in the workspace header, holds the danger zone. Owners see **Delete project…**. Its dialog lists what becomes unreadable, says it can't be undone, and enables **Delete project** only once the project's name is typed exactly. A stale sign-in brings up "Sign in again" first. Afterwards the person lands on Home, and the project leaves the rail. Others read "Only owners can delete a project." Above it, **General** lets owners rename the project; the API's refusal of a name shows at the field, and others read "Only owners can rename the project." **Members** lists everyone by name and role, with a "You" badge on your own row. Owners also see each member's email under the name, to tell people apart; the API sends emails to owners only, so reviewers and viewers see names alone. Owners change a role from a menu on each row, remove someone after a dialog that says they lose access straight away, and add someone by email with a role; an email with no account here gets the API's message at the field. Each of these asks for the password first when the sign-in is not recent ("Changing members needs you to sign in again."). When the list shows a single owner, that owner's role menu is locked and the page says "A project needs at least one owner. Make someone else an owner first."; if the API refuses a change for that reason anyway, its message shows above the list. Reviewers and viewers see the list read-only and "Only owners can change who's in the project." In **General**, owners also see **Archive project**, with one sentence on what it does: it hides the project from Home and the project list, nothing is deleted, and members can still open it. No dialog, because it can be undone. Once archived, General shows an "Archived" badge with the date and **Unarchive project**; reviewers and viewers see the badge and date only. Home, the rail and the default project list already leave archived projects out (`GET /projects` without `include_archived`), so archiving or unarchiving refreshes those lists. In the **Danger zone**, every member sees **Leave project…**, whose dialog says they lose access straight away; leaving needs no recent sign-in. When the list shows you are the only owner, the button is disabled and the page says "You're the only owner, so you can't leave. Make someone else an owner first."; if the API refuses anyway, its message shows in the dialog. After leaving, the project leaves the cached lists and the rail, and the person lands on Home. A `404` from archiving or leaving means access had already gone, so it also goes to Home. **Privacy** lists the project's masking rules (term and type) for owners, adds one with a type (the API's refusal, such as a term already masked, shows at the field) and removes one; reviewers and viewers read "Only owners can see and change the masking rules, because they hold names." and the rules are never requested for them. Above the rules, owners see the switch **Mask names, places and identifiers before text reaches the model** and a sentence on what applies now ("Masking is on: what is found is replaced with placeholders such as [PERSON_1] before the model reads it." or "Masking is off: the model reads this project's questions, papers and documents as written. The masking rules below still apply."). Turning it off opens "Turn masking off?", which warns that names, places, email addresses and other identifiers will reach the model as written and only the masking rules still apply; a stale sign-in asks for the password first ("Turning masking off needs you to sign in again."). Turning it on needs no dialog. When the deployment requires masking, the switch is locked on and the page says "This deployment sends text to a hosted model, so masking stays on." Reviewers and viewers read the state and "Only owners can change this." The state shown is the API's `is_masking_effective`, never worked out in the browser (ADR 0014). "How this brief was made" lists "Masking: on" or "Masking: off for this run (the owner's choice)" under **What this used**. **Instructions for the model** follows Privacy, as described below. "Review before sending" is not built. Collection, tags and pinning come later in Wave 2.

- **General:** name, collection, tags, pinned (per user), archive.
- **Members:** list with roles; invite by email; change role; remove. Changing members needs a recent sign-in. The last owner can't leave or be demoted (the UI explains why).
- **Privacy:** masking on or off for the project, within what the deployment allows (ADR 0014); "Review before sending" on or off; project masking rules (list, add, remove).
- **Instructions for the model** (ADR 0015): two labelled boxes. "How to judge papers" ("Added to every screening call.", example "Treat studies in refugee camps as displaced settings.") and "How to write" ("Added when the brief and answers to follow-up questions are written.", example "Write for a field manager: short sentences, no jargon."). Each shows "N of 2,000 characters". One sentence says they are added after the model's own instructions, never in place of them, masked like questions, and that a run uses the version saved when it started. **Save instructions** waits for the API (not optimistic), then the line beside it reads "Version N, saved by Name on 13 Nov 2026". The API's refusal (too long, a term the project always masks, someone else saved first) shows in its own words above the button, and what was typed is kept. Reviewers and viewers see both texts ("None." when blank), the version line and "Only owners can change the instructions."
- **Model:** read-only. "This deployment uses qwen3.5:9b, served on your organisation's server." Admins set it.
- **Danger zone:** delete the project. A dialog lists what will be destroyed (papers, documents, index, briefs, history), asks to type the project name, and needs a recent sign-in. It says that backups become unreadable after the backup retention period.

### 6.15 Admin

- **Users and invitations:** invite (email, instance role), resend, revoke; list users with status, last sign-in, MFA set up or not; disable a user.
- **Evaluations** (CHITCHAT reference §7):
  - "New evaluation": pick a project (its index), upload a question file, optionally upload other systems' answers (one file per question), pick the judge model.
  - Results: a table per system pair with the six metrics (mean, difference), and wins, ties and losses.
  - Each judgement: the question, the two answers side by side as A and B in the order the judge saw them, the scores and the reason, then a "Reveal systems" toggle.
  - A note on fairness: "The judge should be from a different model family than the writer."

### 6.16 Account

- Signed-in devices: list with browser, place (approximate, from IP if the deployment allows), last active; "Sign out" per device and "Sign out all others".
- Password change; passkeys and TOTP (add, rename, remove; the last one can't be removed); recovery codes (regenerate, with re-auth).
- Preferences: theme (system, light, dark), text size, reduced motion, "low data mode" (section [13](#13-low-bandwidth-and-field-use)).

---

## 7. History and project grouping

People come back to humani-t over weeks. They need to find a project, see what changed, and pick up where they left off. Chat products show a flat list of conversations; research tools show folders of files. humani-t uses the project as the unit, and gives three ways to find one: **what needs you**, **how you grouped it**, and **search**.

### 7.1 Home

```text
┌ Home ────────────────────────────────────────────────────────────────────┐
│ ⌕ Search projects, briefs, papers…                               Ctrl K  │
│                                                                          │
│ Needs you (3)                                                            │
│ ⏸ Cash vs vouchers, Gao      Privacy review · 2 documents      [Review]  │
│ ✎ Biometric registration     Draft 2 waiting for your accept    [Open]   │
│ ✗ Market assessment          Run stopped at Screening · 2 h ago [Open]   │
│                                                                          │
│ Pinned                                                                   │
│ ┌ Cash vs vouchers, Gao ──────┐ ┌ Biometric registration ──┐ ┌ …       ┐ │
│ │ Sahel 2026 · Mali · cash    │ │ Protection · data        │ │         │ │
│ │ ◆ Brief v2 · 2 h ago        │ │ ✎ Draft 2 · yesterday    │ │         │ │
│ │ 52 relevant of 340          │ │                          │ │         │ │
│ │ ● ● ○  3 members            │ │ ● 1 member               │ │         │ │
│ └─────────────────────────────┘ └──────────────────────────┘ └─────────┘ │
│                                                                          │
│ Recent activity                                                          │
│ Today                                                                    │
│  10:58  ◆ Brief v2 ready · Cash vs vouchers, Gao                         │
│  10:31  ✓ Run 3 finished · Cash vs vouchers, Gao                         │
│ Yesterday                                                                │
│  16:20  Maya K. asked "How fast do prices rise…" · Cash vs vouchers      │
│  …                                                                       │
│                                                                          │
│ Collections                                                              │
│ Sahel 2026 (4) · Protection (3) · Health data (2) · Not in a collection (5)│
└──────────────────────────────────────────────────────────────────────────┘
```

- **Needs you** lists open gates, drafts waiting for accept, failed runs, and briefs not yet opened by the user. Each row has one action. The count also shows in the rail. Items leave the list when resolved, or with "Dismiss".
- **Pinned** is per user. Up to 8 cards; more scroll sideways.
- **Recent activity** is the user's own activity and that of their projects, grouped by day ("Today", "Yesterday", "This week", then month names). It shows L0 lines only.
- Empty home (new user): "Start with a question" and a short three-step explanation (Ask, Review the search, Read the brief).

### 7.2 All projects

```text
┌ Projects ────────────────────────────────────────── [+ New project] ┐
│ ⌕ Filter…   Group by: [Collection ▾]  Sort: [Last activity ▾]  ▦ ☰ │
│ Status: [Any ▾]  Tags: [Mali ×] [+]  Owner: [Anyone ▾]  ☐ Archived │
│                                                                     │
│ ▾ Sahel 2026 · 4 projects                                           │
│   ┌ Cash vs vouchers, Gao ──────┐ ┌ Market assessment, Mopti ─┐      │
│   │ ◆ Brief v2 · 2 h ago         │ │ ✗ Run stopped · 2 h ago  │      │
│   └──────────────────────────────┘ └──────────────────────────┘      │
│ ▸ Protection · 3 projects                                           │
│ ▸ Not in a collection · 5 projects                                  │
└─────────────────────────────────────────────────────────────────────┘
```

- **Two views:** cards (▦) and a dense list (☰). The list has columns: name, collection, tags, status, last activity, members, relevant papers, brief version.
- **Group by:** Collection (default), Tag, Status, Owner, or None. A project with several tags appears under each tag group, marked "also in …".
- **Sort:** Last activity (default), Name, Created, Most relevant papers.
- **Filters:** status (Planning, Running, Needs you, Brief ready, Failed, Archived), tags, owner, member, created date. Filters show as chips and are kept in the URL, so a filtered view can be shared or bookmarked.
- **Bulk actions** (list view, owners): move to a collection, add a tag, archive.
- **Project status** is derived, not set by hand: the latest review's state (section [11.1](#111-review-and-run-states)).

### 7.3 Collections and tags

| | Collections | Tags |
|---|---|---|
| What for | The main grouping: an operation, a programme, a team, a year | Cross-cutting labels: a country, a theme, a method |
| How many per project | 0 or 1 | Any number |
| Nesting | None (one level) | None |
| Who manages | Project owners move their project; any user can create a collection **(question for the team: should collections have their own owners?)** | Project owners |
| Shown | Rail, Home, project header | Project cards, filters |

**Why one level and both kinds.** Deep folder trees hide things and force one way of thinking. ChatGPT and Claude Projects use a single level of projects; Perplexity Spaces use a single level too. One level of collections plus tags covers "everything for the Sahel operation" and "everything about cash, anywhere" without a tree to maintain.

**Rail behaviour.** Collections in the rail fold and unfold, remember their state, and show the number of projects. Dragging a project onto a collection moves it (with an undo toast). A keyboard alternative is in the project's `⋯` menu: "Move to collection…".

Collections and tags are **(new, needs API)**.

### 7.4 Inside a project: the timeline

The project's history view (`/p/:project/history`) shows everything that happened, grouped by review.

```text
┌ History · Cash vs vouchers, Gao ─────────────────────────────────────────┐
│ Show: [Reviews ✓] [Runs ✓] [Briefs ✓] [Questions ✓] [Documents ✓] [Notes]│
│                                                                          │
│ ▾ Review: Should we switch from in-kind food aid to cash …   started 3 Nov│
│   │                                                                      │
│   ├─ 10:42  Question asked · Deep · 1 item masked                         │
│   ├─ 10:49  Search plan v2 accepted · 3 categories                        │
│   ├─ 10:51  Run 3 · 27 min · 52 relevant of 340 · qwen3.5:9b   ▸          │
│   ├─ 10:58  ◆ Brief v2 · accepted in round 2                  [Open]      │
│   ├─ 11:20  Question: "How fast do prices rise…" · 3 sources  [Open]      │
│   ├─ 14:05  Labels changed by new rules · 4 papers moved                  │
│   │                                                                      │
│   ├─ 2 Nov  Run 2 · stopped at Screening (model server down)  ▸           │
│   └─ 2 Nov  Search plan v1 accepted                                       │
│                                                                          │
│ ▸ Review: Do vouchers reduce protection risks?               started 28 Oct│
│                                                                          │
│ Compare runs: [Run 3 ▾] with [Run 1 ▾]  [Compare]                        │
└──────────────────────────────────────────────────────────────────────────┘
```

- Newest first inside each review; reviews ordered by latest activity.
- Each line is an L0 summary with one action. Runs expand to their stages.
- The model is named on every run and brief line.
- Filters hide kinds of events; the choice is remembered per user.

**Compare runs.** Built on the `RunManifest` (architecture §4.2). It shows, for two runs:

| Part | Shows |
|---|---|
| Setup | Vocabulary version, queries (added, removed), sources, model, prompt versions, settings that changed |
| Discovery | Papers found, after duplicates; papers only in A, only in B |
| Screening | Label counts side by side; papers whose label changed, with the reason |
| Brief | Options added or removed, evidence strength changes, sources added or removed |

If one run has no manifest, the screen says "Run 1 can't be compared: it has no record of its setup."

### 7.5 Seen and saved marks

Borrowed from CiteSee (CHI 2023): people lose track of which papers they have already read across many reviews.

- A paper or passage the user has opened gets a small "seen" mark everywhere it appears, in any project the user can access.
- **Save** (☆) adds a paper to the user's saved list, visible from the rail and in search.
- "Seen in 2 other projects" links to those projects (only those the user is a member of).
- Seen and saved marks are per user and stored by the API **(new, needs API)**.

### 7.6 Search across projects

```text
┌ ⌕ price rises after cash ─────────────────────────────── Esc ┐
│ In: [All ▾]  Projects · Briefs · Papers · Passages · Questions│
│                                                               │
│ Cash vs vouchers, Gao                                         │
│  ◆ Brief v2 · "Prices can rise in small markets"              │
│  ▣ P0012 Cash transfers and food prices in Mali · HIGH        │
│  ? "How fast do prices rise after a distribution?"            │
│ Market assessment, Mopti                                      │
│  ▣ P0007 Price monitoring in Sahel markets · MEDIUM           │
│                                                               │
│ ↑↓ to move · Enter to open · Tab to change scope              │
└───────────────────────────────────────────────────────────────┘
```

- **Ctrl K** (⌘K on Mac) opens it from anywhere. With an empty box it shows recent projects and "Needs you".
- Results are grouped by project, and only from projects the user is a member of.
- Scopes: projects (name, question, tags), briefs (bottom lines, claims), papers (titles, abstracts), passages, questions.
- It also accepts commands: "new project", "go to settings", "toggle dark mode".
- Search runs on the server over masked text **(new, needs API: `GET /search`)**. Raw document text is never searched.

### 7.7 Archive, delete, retention

- **Archive** hides a project from Home and the default project list. It keeps everything and can be undone. Archived projects show with "☐ Archived" ticked.
- **Delete** destroys the project's key (crypto-shredding). It can't be undone. Section [6.14](#614-project-settings).
- If the deployment sets a retention period **(new, needs API)**, projects near it show "Deleted in 14 days unless kept" in Needs you.

---

## 8. Trust, uncertainty and privacy patterns

These patterns are shared by every screen. They are written once here, so the wording stays the same everywhere.

### 8.1 Evidence strength

The brief uses four values (`Strength` in `schemas.py`). The interface always shows the word with a three-dot meter:

| Value | Meter | Word | Hover text |
|---|---|---|---|
| `strong` | ●●● | Strong evidence | Several good studies agree. New studies are unlikely to change this much |
| `moderate` | ●●○ | Moderate evidence | Some good studies, with gaps or some disagreement. New studies could change this |
| `weak` | ●○○ | Weak evidence | Few or weaker studies. Treat as a lead, not a finding |
| `insufficient` | ○○○ | Not enough evidence | The sources don't say enough to judge this option |

- The scale follows the spirit of GRADE's certainty levels (high, moderate, low, very low), which people already know from Cochrane summaries. It is not a GRADE rating, and the "?" help says so.
- Colour follows the meter (section [9.2](#92-colour)) but is never the only cue.
- When evidence is weak or insufficient, the option card's claims use hedged words. That is the Writer's job; the UI adds nothing that sounds more certain.

### 8.2 Evidence or reasoning

Every claim has `support`:

- **evidence:** "the cited passages say this". Shown in normal text with citation chips.
- **hypothesis:** "the model's inference, not a source". Shown in italics with "⟡ reasoning, not a source", and no chips.

The legend sits at the top of the options section: "+ benefit · − risk · [P0012] a cited source · ⟡ reasoning, not a source".

### 8.3 Why sources disagree

| `conflict_type` | Plain words | Help text |
|---|---|---|
| `hierarchy` | Stronger and weaker studies disagree | Trials or large studies point one way, smaller or weaker designs the other. Give more weight to the stronger ones |
| `context` | Different settings, different results | The studies looked at different places, people or times. Ask which setting is closest to yours |
| `noise` | Small or unclear studies | The disagreement comes from small, old or unclear studies. It may not be real |

### 8.4 Citations

- A citation chip shows its tag: `P0012` (paper) or `D0002` (your document). Paper chips have a page icon; document chips a folder icon.
- Hover (or long press) shows title and year. Click opens the source panel.
- Chips are buttons with a label for screen readers: "Source P0012, Cash transfers and food prices in Mali, 2023".
- The UI never shows "24 sources" as a badge of quality. Counts appear only as facts in lists.
- Chips the user hasn't opened yet carry a small dot, so people can see which sources they have checked.

### 8.5 Privacy receipts

- Every piece of user text that goes to a model has a receipt (section [5.3.2](#532-privacy-receipt)).
- Masked items are pills: `⟨PERSON 1⟩`. The number keeps the same person apart from another in the same text.
- The receipt counts what was masked by type. It never shows the original.
- The phrase "Masking is not anonymity" appears on every receipt where something was masked, linking to a short help page.
- Answers carry the privacy line from CHITCHAT: "Privacy: question 1 masked (LOCATION 1) · context: passages already masked".

### 8.6 Model disclosure

- Every brief, run and evaluation names the model and version. Screens with model output have a footer line: "Made by qwen3.5:9b"
- If a deployment uses a hosted provider, the line says so: "Made by … at Infomaniak (no training, no retention)".

### 8.7 The caveat

The fixed text from `schemas.py`:

> This brief is an aid to a decision, not the decision. It covers only the papers and documents in this run; check the cited sources before acting.

- Full text in the brief header, always visible.
- Short form on the brief card in the chat: "An aid to a decision, not the decision. Check the sources."
- On every page of a PDF export, in the footer.

---

## 9. Visual design system

humani-t is a sub-project of the LiGHT lab and wears the lab's brand (LiGHT Brand Guidelines, August 2026), so that it reads as part of the lab's family next to [the lab's website](https://www.light-laboratory.org/). This section turns the guidelines into tokens, adds what the guidelines leave open (a dark theme, meaning colours, data), and records every colour pair the interface uses with its measured contrast.

### 9.1 Tokens

Tokens live in one place: the Chakra UI system in [`apps/web/src/theme/system.ts`](../apps/web/src/theme/system.ts) (`createSystem`). Raw brand colours are `colors.light.*`; components read only **semantic tokens**, which say what a colour is for (`fg.muted`, `border.emphasized`, `danger.subtle`) and switch with the theme. A component never names a hex value; [`QrCode`](../apps/web/src/features/auth/QrCode.tsx) is the one exception, because phone cameras need black on white in both themes.

Chakra's style props become class names, and Emotion writes the rules into `<style>` elements at run time. **The CSP forbids inline styles and allows those elements only with the page's nonce** ([ADR 0004](adr/0004-chakra-ui-with-a-nonce-csp.md)), so components still never use a `style` attribute or write `element.style` in script; `src/csp.test.ts` fails if they do. Dynamic values use a `data-*` attribute that CSS reads (the composer's grow mechanism) or an SVG attribute.

The theme is a class on `<html>`: `dark` or `light`, resolved from the person's choice (`data-theme`) or, for "System", from `prefers-color-scheme`, and re-resolved when the system's setting changes ([`preferences.ts`](../apps/web/src/app/preferences.ts)).

### 9.2 Colour

**The brand palette.** Base colours are for backgrounds, accent colours for highlights, and one colour family is used per view.

| Name | HEX | Role in the brand | Use in humani-t |
|---|---|---|---|
| Dark Blue | `#0C43A0` | primary base | Primary buttons, links, focus ring, the current page in the rail, the sign-in cover (light theme) |
| Light Blue | `#68AFE7` | primary accent | The light-beam on the cover; in the dark theme, primary buttons, links and focus. **Never text or essential UI on a light background** (2.35:1 on Eggshell) |
| Dark Yellow | `#EBA547` | secondary base | Caveat and risk borders, the "waiting for you" run strip border (decoration only on light backgrounds: 2.08:1); risk text in the dark theme |
| Light Yellow | `#F6D54D` | secondary accent | Caveat text in the dark theme. Never text on Eggshell (1.43:1) |
| Dark Purple | `#79123F` | secondary base | Reserved; see 9.2.3 |
| Light Purple | `#AE3156` | secondary accent | Errors and destructive actions (light theme) |
| Eggshell | `#FEFFF7` | neutral | All text in the dark theme, text on brand colour. The light theme's page background is pure white instead, by the owner's choice |

#### 9.2.1 Semantic tokens

The brand defines no dark theme. The dark one here is derived: deep blue-black surfaces from the blue family, Eggshell text, and the light accents taking over where the dark bases would disappear. Colours marked *derived* are tints or shades of a brand colour, made because no brand colour reaches the contrast needed in that place.

| Token | Light | Dark | Use |
|---|---|---|---|
| `bg` | `#FFFFFF` white | `#060E22` derived | Page background |
| `bg.panel` | `#FFFFFF` | `#0B1631` derived | Cards, dialogs, inputs |
| `bg.subtle` | `#EEF2F9` derived | `#12204A` derived | The chat column, wells, table header row |
| `bg.muted` | `#E6EAF5` (the lab site's hover tint) | `#1A2B5C` derived | Hover, the current rail item |
| `fg` | `#0A1633` derived ink | `#FEFFF7` Eggshell | Main text |
| `fg.muted` | `#4A5570` derived | `#B7C0D6` derived | Secondary text, small labels |
| `border` | `#D9DEEA` derived | `#24325A` derived | Hairlines (decorative: not relied on to identify anything) |
| `border.emphasized` | `#7A8499` derived | `#6A79A3` derived | Edges that identify a control: inputs, checkboxes, radios, the composer, outline buttons |
| `focusRing`, `link`, `brand.solid` | `#0C43A0` Dark Blue | `#68AFE7` Light Blue | Focus outline, links, primary buttons |
| `brand.contrast` | `#FEFFF7` | `#06102A` derived | Text on a primary button |
| `marker` | `#000000` | `#FEFFF7` | The section dot |
| `fg.error`, `danger.solid` | `#AE3156` Light Purple | `#F08BA8` derived | Error text and icons, destructive actions |
| `danger.subtle` | `#F8E9EE` derived | `#2A0E1A` derived | Error notice background |
| `caveat.fg` | `#5C3D00` derived | `#F6D54D` Light Yellow | Caveat and "not available yet" text |
| `caveat.subtle` | `#FDF4DC` derived | `#241D08` derived | Caveat boxes, the signed-out banner |
| `border.warning` | `#EBA547` Dark Yellow | `#EBA547` | Caveat border (decorative) |
| `benefit.fg` / `benefit.bg` | `#0C43A0` / `#E6EAF5` | `#68AFE7` / `#12204A` | Benefit text and fill |
| `risk.fg` / `risk.bg` | `#7A4800` derived / `#FBEFDA` | `#EBA547` / `#2A1E08` | Risk text and fill |
| `masked.fg` / `masked.bg` / `masked.line` | `#0C43A0` / `#E8F1FB` / `#68AFE7` | `#68AFE7` / `#0F2550` / `#68AFE7` | Masked-item pills (dashed border) |
| `scrim` | black at 60% (the lab site's menu overlay) | black at 70% | Behind dialogs and the drawer |

#### 9.2.2 Measured contrast

**Text must reach 4.5:1 on its background, large text (24 px, or 18.66 px bold) and the edges and icons that identify UI 3:1** (WCAG 2.2 AA, §12). Every pair the interface uses is listed, measured with the WCAG 2.x relative-luminance formula. Disabled controls are exempt and not listed.

| Pair | Theme | Ratio | Needs | Where |
|---|---|---|---|---|
| `fg` `#0A1633` on `bg` `#FEFFF7` | light | 17.75 | 4.5 | Body text |
| `fg` on `bg.panel` `#FFFFFF` | light | 17.87 | 4.5 | Text in cards and dialogs |
| `fg` on `bg.subtle` `#EEF2F9` | light | 15.91 | 4.5 | Chat column, table header |
| `fg` on `bg.muted` `#E6EAF5` | light | 14.85 | 4.5 | Hovered rail item |
| `fg` on `danger.subtle` `#F8E9EE` | light | 15.21 | 4.5 | Error notice text |
| `fg.muted` `#4A5570` on `bg` | light | 7.39 | 4.5 | Secondary text |
| `fg.muted` on `bg.panel` | light | 7.43 | 4.5 | Secondary text in cards |
| `fg.muted` on `bg.subtle` | light | 6.62 | 4.5 | Hints in the chat column |
| `fg.muted` on `bg.muted` | light | 6.18 | 4.5 | Secondary text on hover |
| `fg.muted` on `caveat.subtle` `#FDF4DC` | light | 6.78 | 4.5 | Request ids in a caveat |
| `link` `#0C43A0` on `bg` | light | 8.97 | 4.5 | Links |
| `link` on `bg.panel` | light | 9.03 | 4.5 | Links in cards |
| `link` on `bg.subtle` | light | 8.04 | 4.5 | Links in the chat column |
| `link` on `bg.muted` | light | 7.50 | 4.5 | The current rail item |
| `brand.contrast` `#FEFFF7` on `brand.solid` `#0C43A0` | light | 8.97 | 4.5 | Primary button label; Eggshell text on the cover |
| Eggshell on the cover's light-beam at its strongest (`#316EBC`) | both | 5.09 | 4.5 | Cover text crossing the beam |
| `fg.error` `#AE3156` on `bg` | light | 6.18 | 4.5 | Error text |
| `fg.error` on `bg.panel` | light | 6.23 | 4.5 | Field error |
| `fg.error` on `danger.subtle` | light | 5.30 | 3 (icon) | Error icon |
| `caveat.fg` `#5C3D00` on `caveat.subtle` | light | 9.02 | 4.5 | Caveat, signed-out banner |
| `risk.fg` `#7A4800` on `risk.bg` `#FBEFDA` | light | 6.70 | 4.5 | Risk text |
| `risk.fg` on `bg` | light | 7.57 | 4.5 | Risk text on the page |
| `benefit.fg` `#0C43A0` on `benefit.bg` `#E6EAF5` | light | 7.50 | 4.5 | Benefit text |
| `masked.fg` `#0C43A0` on `masked.bg` `#E8F1FB` | light | 7.91 | 4.5 | Masked pill |
| `border.emphasized` `#7A8499` on `bg` | light | 3.73 | 3 | Input and checkbox edges |
| `border.emphasized` on `bg.panel` | light | 3.76 | 3 | Inputs in cards |
| `border.emphasized` on `bg.subtle` | light | 3.35 | 3 | The composer edge |
| `focusRing` `#0C43A0` on `bg` / `bg.panel` | light | 8.97 / 9.03 | 3 | Focus outline |
| `marker` `#000000` on `bg` | light | 20.86 | 3 | Section dot |
| `fg` `#FEFFF7` on `bg` `#060E22` | dark | 19.07 | 4.5 | Body text |
| `fg` on `bg.panel` `#0B1631` | dark | 17.78 | 4.5 | Cards and dialogs |
| `fg` on `bg.subtle` `#12204A` | dark | 15.67 | 4.5 | Chat column |
| `fg` on `bg.muted` `#1A2B5C` | dark | 13.51 | 4.5 | Hovered rail item |
| `fg` on `danger.subtle` `#2A0E1A` | dark | 17.74 | 4.5 | Error notice text |
| `fg.muted` `#B7C0D6` on `bg` | dark | 10.54 | 4.5 | Secondary text |
| `fg.muted` on `bg.panel` | dark | 9.82 | 4.5 | Secondary text in cards |
| `fg.muted` on `bg.subtle` | dark | 8.66 | 4.5 | Hints in the chat column |
| `fg.muted` on `bg.muted` | dark | 7.46 | 4.5 | Secondary text on hover |
| `link` `#68AFE7` on `bg` | dark | 8.11 | 4.5 | Links |
| `link` on `bg.panel` | dark | 7.56 | 4.5 | Links in cards |
| `link` on `bg.subtle` | dark | 6.67 | 4.5 | Links in the chat column |
| `link` on `bg.muted` | dark | 5.75 | 4.5 | The current rail item |
| `brand.contrast` `#06102A` on `brand.solid` `#68AFE7` | dark | 7.96 | 4.5 | Primary button label |
| `fg.error` `#F08BA8` on `bg` / `bg.panel` | dark | 8.20 / 7.64 | 4.5 | Error text |
| `fg.error` on `danger.subtle` | dark | 7.62 | 3 (icon) | Error icon |
| `caveat.fg` `#F6D54D` on `caveat.subtle` `#241D08` | dark | 11.60 | 4.5 | Caveat, signed-out banner |
| `risk.fg` `#EBA547` on `risk.bg` `#2A1E08` | dark | 7.77 | 4.5 | Risk text |
| `masked.fg` `#68AFE7` on `masked.bg` `#0F2550` | dark | 6.34 | 4.5 | Masked pill |
| `border.emphasized` `#6A79A3` on `bg` / `bg.panel` | dark | 4.46 / 4.16 | 3 | Input and checkbox edges |
| `border.emphasized` on `bg.subtle` | dark | 3.66 | 3 | The composer edge |
| `focusRing` `#68AFE7` on `bg` / `bg.panel` | dark | 8.11 / 7.56 | 3 | Focus outline |
| `marker` `#FEFFF7` on `bg` | dark | 19.07 | 3 | Section dot |

Pairs that **fail and are therefore never used** for text or essential UI: Light Blue on Eggshell (2.35), Dark Yellow on Eggshell (2.08), Light Yellow on Eggshell (1.43), Dark Blue on the dark background (2.13), Light Purple on the dark background (3.08). Hairlines (`border`, about 1.3:1) are decoration; anything that identifies a control uses `border.emphasized`.

axe runs in every page test but cannot measure contrast in jsdom, so this table is the contrast check. Re-measure any pair you add, and add it here.

#### 9.2.3 Meaning colours and their partner cues

Colour never carries meaning alone (principle 4): every meaning has a word and an icon, and colour repeats them. Families are kept apart so a meaning cannot be mistaken for another: **blue** for the product and for good news, **yellow** for caution, **purple** for errors.

| Meaning | Colour | Always with |
|---|---|---|
| Benefit | `benefit.*` (blue) | "+" and the heading "Benefits" |
| Risk | `risk.*` (yellow) | "−" and the heading "Risks" |
| Strong / Moderate / Weak / Not enough | `benefit.fg` / `link` / `risk.fg` / `fg.muted` | The meter and the word |
| Highly relevant / Relevant / Possibly / Excluded / Skipped | `benefit.fg` / `benefit.fg` / `risk.fg` / `fg.muted` / `fg.muted` | The label word and an icon (▲ ▲ △ ✕ –) |
| Failed, waiting, caveat | `caveat.*` (yellow) | `triangle-alert` or `pause` and the word |
| Error | `danger.*` (purple) | `circle-x` and a message |
| Masked | `masked.*` (blue) | Dashed border and the type name |

Risk is yellow, not purple, so "a risk of this option" never looks like "something broke". Dark Purple is held back so a future meaning (or a humani-t sub-project colour, 9.8) has a family of its own.

### 9.3 Type

Both fonts are self-hosted from Fontsource packages and bundled with the app, so the CSP's `font-src 'self'` holds and nothing loads from Adobe or a font service. Variable fonts, split by `unicode-range`, so a page in English downloads only the Latin files.

- **Titles:** Ivy Presto Headline, semibold, sentence case, behind the single family name **`"Brand Headline"`** ([`src/theme/fonts.css`](../apps/web/src/theme/fonts.css)). Until the lab's licensed `.woff2` files are dropped in, that name points at **Playfair Display** (SIL Open Font Licence), the closest open high-contrast editorial serif. Switching is a change to that one file ([`public/brand/README.md`](../apps/web/public/brand/README.md)).
- **Everything else:** **Manrope** (`@fontsource-variable/manrope`, SIL OFL), replacing Inter. Regular for body, Medium for subtitles, labels and buttons, Regular in all caps for small labels.

| Use | Family | Size / line height | Weight |
|---|---|---|---|
| Page title (h1) | Brand Headline | 36 / 1.2 (30 on phones) | 600 |
| Sign-in title | Brand Headline | 30 / 1.2 | 600 |
| Workspace title | Brand Headline | 24 / 1.2 (20 on phones) | 600 |
| Cover line, empty-chat prompt, project card name | Brand Headline | 48, 30, 20 | 600 |
| Section label (h2) | Manrope, all caps, 0.08em tracking | 12 / 1.4 | 400 |
| Body | Manrope | 16 / 1.6 (the lab site's body) | 400 |
| Secondary text, tables | Manrope | 14 / 1.5 | 400 |
| Labels, buttons | Manrope | 14–16 | 500 |
| Small labels, meta | Manrope, all caps for labels | 12 | 400 |

- Numbers in tables and counters use tabular figures.
- Line length in the brief is capped at about 75 characters.
- Text size can be raised in Account preferences (100%, 112%, 125%); every layout must work at 200% browser zoom.

### 9.4 Space, shape and depth

"If it feels cramped, it probably is": pages use generous space, close to the lab site's.

- Spacing steps of 4 px. Pages have 32–48 px above the title, 40 px between sections, 16 px side gutters on phones and 32 px from tablets up; content is at most 1152 px wide (672 px for forms and Account).
- **Section pattern:** a small black dot, the section label in small capitals at the top left, then a hairline rule under it ([`SectionHeading`](../apps/web/src/ui/SectionHeading.tsx)).
- **Radii:** 8 px for cards, inputs, dialogs, notices and rail items (the brand's rounded cards); pill (300 px, as on the lab site) for buttons and badges; full for avatars and dots.
- Depth by hairlines and background steps. Cards are white (`bg.panel`) on Eggshell with a hairline, no shadow; the only shadows are on things that float (the drawer, dialogs).
- **The light-beam diagonal** is for covers and title areas only. Today it appears once, on the sign-in and enrolment cover: Light Blue at 40% over Dark Blue, decorative and hidden from assistive technology.
- The canvas divider is a 1 px line with an 8 px wide drag handle that shows on hover and focus.

### 9.5 Motion

- Hover and state changes fade in 0.2 s, as on the lab site. Panels (the drawer) slide in 0.2 s. Nothing bounces.
- Cards enter with a fade and 6 px rise, 350 ms. The run strip updates without motion except its spinner.
- `prefers-reduced-motion` cuts every animation and transition to nothing (a global rule in the system), and Account preferences can force it.
- The streaming of an answer is not typed out letter by letter; text appears in sentences, which is easier to read and cheaper on slow links.

### 9.6 Icons

**Lucide** through `lucide-react` (ISC licence): one React component per icon, tree-shaken, drawn as inline SVG, so nothing is requested and nothing needs a font. Lucide marks an icon `aria-hidden="true"` unless it is given an accessible name, so a decorative icon is skipped by screen readers; an icon that carries meaning always sits next to a word or inside a control with an `aria-label`. Main icons:

| Meaning | Icon |
|---|---|
| Brief | `diamond` |
| Privacy, masking | `shield-check` |
| Running / done / failed / waiting / cancelled | `loader-circle` / `check` / `x` / `pause` / `square` |
| Benefit / risk | `plus` / `minus` |
| Reasoning | `sparkle` |
| Humanitarian criterion | `heart-handshake` |
| Paper / document | `file-text` / `folder` |
| Warning, signed out | `triangle-alert` |
| Error | `circle-x`, `circle-alert` (field) |
| Navigation | `house`, `layout-grid`, `plus`, `panel-left`, `menu`, `log-out` |
| Theme | `monitor` / `sun` / `moon` |

### 9.7 Charts

Few charts, all simple, all with a text or table version:

- Stacked bar for screening labels, with labels on the bar segments or beside them.
- PRISMA diagram as SVG boxes and arrows, with numbers as text.
- Progress bars with the numbers beside them.
- No pie charts, no gauges, no "confidence %" dials.
- Series colours come from one family per chart (blue tints for a sequence); yellow and purple are kept for caution and error.

### 9.8 Logos and the sub-project rule

humani-t's logo sits on the **left** and LiGHT's on the **right**: in the app header, on the sign-in cover, and in the footer. The LiGHT wordmark is used only as supplied (original dark and light blue, black or white) with its safe area, and is never redrawn. Until the official SVGs are in [`apps/web/public/brand/`](../apps/web/public/brand/README.md), [`Brand.tsx`](../apps/web/src/ui/Brand.tsx) renders "humani-t" and "by LiGHT" as Manrope text in those slots.

The guidelines let a sub-project have its own colour pair, generated from one hue: light at 55% saturation and 91% brightness, dark at 93% saturation and 63% brightness. humani-t has none yet; if the lab assigns one, it becomes a palette next to `brand` and its pairs are measured into 9.2.2 before use.

### 9.9 Brand sources

What was taken from where, so it can be checked against the originals.

- **LiGHT Brand Guidelines (August 2026):** the seven colours and their base/accent roles; one colour family at a time; Ivy Presto Headline semibold titles in sentence case; Manrope for everything else (Regular body, Medium subtitles, Regular all-caps labels); the section label, hairline rule and black dot; 8 px rounded cards on Eggshell; generous space; the light-beam diagonal for covers and title areas only; logo colours, safe area and the sub-project placement rule.
- **[light-laboratory.org](https://www.light-laboratory.org/)** (a Wix site, read from its served HTML and theme variables on 3 November 2026):
  - *Type:* body in Manrope at 16 px with 1.6 line height, 18 px/1.6 for lead text; section titles in Ivy Presto Headline (`--font_2`, 60 px/1.3); Manrope subtitles at 38, 34, 28 and 22 px; no letter-spacing on body text. The app scales these down for a working tool, keeping the families, weights and line height.
  - *Colour usage:* black text on a white page, Dark Blue as the link and active colour (the site uses `#1E429A`, a near neighbour; the app uses the guidelines' `#0C43A0`), Light Blue `#68AFE7` only as an accent, a pale blue `rgb(230 234 245)` hover fill, kept as `bg.muted`.
  - *Navigation:* logo on the left, links to the right in Manrope 16 px, black, Dark Blue when current, the pale blue fill on hover; on phones a "Menu" button opens a white panel over a 60% black overlay. The app keeps its left rail (§4) but gives its items this typography and these states, puts the logos in a top header in the same left/right order, and uses the same drawer and 60% scrim on small screens.
  - *Buttons:* pill-shaped (`border-radius: 300px`), Manrope 16 px regular, an icon beside the label, 0.2 s transitions; a light grey fill when disabled. The app's buttons are pills with a Medium label and a 0.2 s transition, filled Dark Blue for the primary action so it reads as one.
  - *Cards:* 10 px radius with a thin border and no shadow. The app uses the guidelines' 8 px and the same border-not-shadow depth.
  - *Footer:* the LiGHT logo with the lab's affiliations, a short link list and contact. The app's footer is a hairline, then humani-t and its one-line purpose on the left and the LiGHT slot on the right; it leaves out the lab's addresses and links, which would point users of a self-hosted deployment to a third-party site.
  - *Not taken:* the site's photography, statistics rows and team cards (marketing, not tool, patterns), and anything loaded from Wix or its font servers.

---

## 10. Component inventory

### 10.1 Built on Chakra UI

The interface is built on **Chakra UI v3** ([ADR 0004](adr/0004-chakra-ui-with-a-nonce-csp.md)) with the brand system of 9.1. Only the recipes listed in `system.ts` are registered; a Chakra component outside that list renders unstyled until its recipe is added. These are the Chakra parts in use and what they build:

| Chakra component | Used for |
|---|---|
| `Box`, `Flex`, `Stack`, `HStack`, `Grid`, `chakra.*` | All layout: the shell, pages (`ui/Page`), the auth cover, rows, the composer |
| `Heading` | Page titles (`ui/PageHeading`), section labels (`ui/SectionHeading`) |
| `Text`, `Code`, `VisuallyHidden` | Copy, recovery codes and the TOTP key, screen-reader-only text |
| `Button`, `IconButton` | Every action (pill-shaped); icon-only buttons always carry `aria-label` or hidden text |
| `Link` (with `asChild` around the router's `Link`) | Text links, project cards, the header's home link |
| `Input` | `ui/Field` (labels, hints and errors are wired by `ui/Field`, not by Chakra's `Field`, so the description is right on first render) |
| `Checkbox` | "Show archived projects", "I've saved my recovery codes" |
| `RadioGroup` | The theme choice on Account |
| `Dialog` (with `Portal`) | `ui/Dialog`, used by the re-authentication dialog: focus trap, the page behind hidden from assistive technology, Esc closes |
| `Alert` | `ui/ErrorNotice`, the signed-out banner |
| `Badge` | Role, "Archived", "This device" |
| `Table` | All projects |
| `Skeleton` | `ui/Loading` |

Not from Chakra, on purpose: the composer's textarea grows by the hidden-copy grid technique rather than Chakra's `autoresize`, which measures in script and writes `style.height`; the QR code is a single SVG path; the rail's items are router links styled from `shell/railStyles.ts`.

### 10.2 Planned components

Props are named after the API types (generated into `apps/web/src/api/schema.gen.ts`). Each is composed from the parts in 10.1.

| Component | Purpose | Main props | States | Accessibility |
|---|---|---|---|---|
| `AppShell` | Header, rail, main area, footer, canvas slots | `railCollapsed`, `canvas` | — | Landmarks: `banner`, `nav`, `main`, `contentinfo`, `complementary` |
| `Rail` | Search, Needs you, pinned, collections, recent | `collections`, `pinned`, `needsYou` | collapsed, expanded | `nav` with a label; tree for collections |
| `CollectionTree` | Collections with projects | `collections`, `onMove` | folded, unfolded, drop target | `role="tree"`, arrow keys |
| `ProjectCard` | One project on Home or Projects | `project`, `status`, `tags`, `members` | normal, needs you, archived | The whole card is one link; actions inside are separate buttons |
| `ChatTimeline` | The project's cards in order | `turns` | loading, empty | `role="log"`, `aria-live="polite"` for new turns only |
| `ChatTurn` | Base for every card | `kind`, `level`, `author`, `at` | L0, L1, replaced, warning | Heading per card; expand button with `aria-expanded` |
| `QuestionTurn`, `PrivacyReceipt`, `ClarifyCard`, `DraftCard`, `RunStrip`, `GateCard`, `ScreeningCard`, `BriefCard`, `AnswerCard`, `ErrorCard`, `SystemNote` | The card types in [5.3](#53-card-types) | per type | per type | per type |
| `Composer` | Text box with its mode label | `mode`, `target`, `reviewBeforeSending` | idle, sending, disabled | Label names the mode; Shift+Enter hint |
| `Canvas` | Holds one L2 view | `view`, `width` | open, full width, closed | `role="region"` with the view's title; focus moves to its heading on open |
| `CategoryCard` | One search category | `category`, `fit`, `notes` | normal, shortened, too long | Concepts read as "cash transfers, or cash assistance, mobile money; and food security, …" |
| `ConceptPill` | One concept with synonyms | `concept`, `colorIndex`, `origin`, `shared` | normal, added, removed | Origin and "shared" as text |
| `DraftDiff` | Changes between drafts | `before`, `after` | — | "+ added", "− removed" in text |
| `MaskReview` | Masking review | `document`, `spans` | loading, reviewing, approved | Spans are buttons; type in label |
| `StageList` | Stages of a run | `stages` | per stage state | List with each stage's state as text |
| `PaperRow` | One paper, expandable | `paper`, `label`, `criteria`, `seen` | collapsed, expanded | Row header is a button |
| `LabelBadge` | Screening label | `label` | — | Word plus icon |
| `PrismaDiagram` | PRISMA flow | `counts` | — | SVG with `<title>`; table fallback |
| `RulesEditor` | Label rules with preview | `rules`, `preview` | editing, previewing, applying | Form labels; preview announced |
| `BriefHeader` | Question, bottom line, meta, caveat | `brief` | — | Caveat is a `role="note"` |
| `OptionCard` | One option | `option`, `highlighted` | normal, highlighted, collapsed | Heading with strength in text |
| `ClaimLine` | One claim | `claim` | evidence, hypothesis | "Benefit:" or "Risk:" read out; hypothesis read as "reasoning, not a source" |
| `CitationChip` | A source tag | `tag`, `seen`, `missing` | normal, seen, missing | Button with title in the label |
| `EvidenceBadge` | Strength meter and word | `strength` | — | Word is the label; meter is hidden from screen readers |
| `TradeoffMatrix` | Criteria × options | `tradeoffs`, `options`, `priorities` | normal, prioritised, narrow | Real table; priority rows announced |
| `ConflictBlock` | One disagreement | `conflict` | — | Positions as a list |
| `ConsiderationRow` | "If this matters most" | `consideration` | normal, selected | Button that sets the priority |
| `GapItem` | One gap with "Search for this" | `gap` | — | — |
| `SourcePanel` | Passage and source (L3) | `tag`, `passages`, `citedFor` | loading, loaded, missing | Dialog on mobile, complementary panel on desktop; focus trapped only on mobile |
| `TraceTimeline` | How this was made | `steps`, `stoppedBecause` | live, done | Ordered list |
| `ScoreDots` | One Critic score | `name`, `value` | — | "Groundedness: good (2 of 2)" |
| `CommandPalette` | Search and commands | `scopes` | empty, results, no results | Combobox pattern |
| `HistoryTimeline` | Project history | `events`, `filters` | — | List grouped by review |
| `RunCompare` | Diff of two runs | `a`, `b` | comparable, not comparable | Tables with headers |
| `Toast` | Short confirmations, undo | `message`, `action` | — | `role="status"` |
| `Banner` | Connection, session, maintenance | `kind` | — | `role="alert"` only for errors |
| `ReauthDialog` | Sign in again | `reason` | — | Chakra `Dialog` with focus trap |

Reused from `chitchat-ui`: the accordion pattern (option cards on mobile, quality checks), the skeleton style, the mobile bottom sheet (source panel), the CSV helper (client-side exports of filtered tables).

---

## 11. Interaction and state

### 11.1 Review and run states

```text
Review:  new ──► clarifying ──► drafting ◄──► changes requested
                                   │
                                   ▼ accept (vocabulary frozen, version N)
                               accepted ──► running ──► done (brief ready)
                                              │  ▲          │
                                    gate.required │ gate.resolved
                                              ▼  │          ▼
                                            waiting      rerun ──► running
                                              │
                                   failed ◄───┴───► cancelled
```

| Project status shown | When |
|---|---|
| Planning | Latest review is new, clarifying, drafting or changes requested |
| Running | A run is going |
| Needs you | A gate is open, or a draft waits for accept |
| Brief ready | The latest run finished with a brief |
| Done, no brief | The latest run finished without the Brief stage |
| Failed | The latest run failed |
| Cancelled | The latest run was cancelled |
| Archived | Archived by an owner |

### 11.2 Live events

One SSE stream per run (`GET /api/v1/runs/{run_id}/events`, [architecture.md](architecture.md) §4.4). Events and their effect:

| Event | Fields | UI effect |
|---|---|---|
| `stage.started` | `stage`, `position`, `total`, `at` | Run strip shows the stage; stage list marks it ◐ |
| `stage.progress` | `stage`, `done`, `total`, `item` | Counter and bar; latest item line. Throttle UI updates to 2 per second |
| `stage.finished` | `stage`, `seconds`, `summary` | ✓ and duration; for `screen`, the screening card; for `answer`, the brief card |
| `agent.step` | the trace event | Brief-stage line in the strip; a row in the trace timeline |
| `gate.required` | `gate_id`, `kind`, `payload` | Gate card, ⏸ strip, Needs you |
| `gate.resolved` | `gate_id`, `by`, `decision` | Gate card folds; strip resumes |
| `job.finished` | `outcome` (`done`, `failed`, `cancelled`), `seconds`, `error` | Strip's final state; a browser notification if the tab is hidden and the user allowed it |

- **Reconnect.** Each event has an `id`. The browser's `EventSource` resends `Last-Event-ID`; the API replays missed events ([architecture.md](architecture.md) §4.4). A replay can repeat an event, so the interface applies each event by its id and ignores one it has already seen. While disconnected, the strip shows "Reconnecting…" and keeps the last state.
- **Several tabs.** All tabs on the same project show the same state from the same stream. Actions in one tab (accept, cancel) update the others through the stream.
- **Several people.** Members see each other's actions as they happen: a system note "Maya K. accepted draft 2". Two people accepting at once: the second gets 409 and the message "Someone else already accepted this draft. Showing the latest."

### 11.3 Optimistic or confirmed

| Action | Behaviour |
|---|---|
| Pin, save, seen, filters, collapse | Optimistic: change at once, roll back with a toast on failure |
| Move to collection, add tag | Optimistic with an undo toast |
| Send a question, answer, change request | Shows the user's turn at once with "Sending…"; the receipt and reply follow |
| Accept a draft, start a run, cancel, apply rules | Confirmed: button shows progress; the UI changes when the API confirms |
| Delete, change members, approve masking | Confirmed, with a dialog; re-auth when needed |

### 11.4 Errors by status

| Status | Meaning | UI |
|---|---|---|
| 400, 422 | Bad input | Message by the field it concerns |
| 401 | Signed out | Session banner; keep unsent text |
| 401 `reauthentication_required` | Needs recent sign-in | Re-auth dialog, then retry once |
| 404 | Not found or not a member | "This project doesn't exist or you don't have access." Same text for both |
| 409 | Changed by someone else | Reload the part, show what changed, ask to try again |
| 429, 503 with `Retry-After` | Too many requests, or busy | Countdown on the button; retry by itself once at zero |
| 500 | Server error | Error card with request id |
| Network down | Offline | Banner "You're offline. Changes will be sent when you're back" for queued safe actions; risky ones are disabled |

All error text comes from the problem document's `title` and `detail`, written in plain language by the API.

### 11.5 Keyboard

| Keys | Does |
|---|---|
| Ctrl K / ⌘K | Search and commands |
| `[` | Fold or unfold the rail |
| `]` | Open or close the canvas |
| Esc | Close the source panel, then the canvas, then dialogs |
| `g` then `h` | Go home |
| `g` then `p` | Go to projects |
| `/` | Focus the composer |
| `j` / `k` | Next or previous card in the chat (when the chat has focus) |
| `Enter` on a card | Open its main action |
| `?` | Show shortcuts |

Single-key shortcuts are off when focus is in a text field, and can be turned off in preferences (WCAG 2.1.4).

---

## 12. Accessibility

The target is **WCAG 2.2 level AA**, which is also what the ICRC public website aims for.

- **Never colour alone** (1.4.1): every meaning colour has a word or icon (section [9.2](#92-colour)).
- **Contrast** (1.4.3, 1.4.11): text 4.5:1, large text and UI parts 3:1, in light and dark.
- **Zoom and reflow** (1.4.4, 1.4.10): works at 200% zoom and at 320 px width without sideways scrolling, except the trade-off matrix, which switches to cards.
- **Keyboard** (2.1.1): everything works with the keyboard. The focus order is rail → chat → composer → canvas → source panel. A "Skip to chat" and "Skip to canvas" link sit at the top.
- **Focus** (2.4.7, 2.4.11): a visible 2 px ring (`--primary-text`) on every focusable part, never hidden by sticky bars.
- **Target size** (2.5.8): at least 24 × 24 px; chips get extra padding to reach it.
- **Live updates** (4.1.3): new chat cards are announced politely. The run strip announces stage changes and the end of a run, not every count (at most one announcement every 10 seconds).
- **Tables**: the trade-off matrix and the paper list are real tables with headers.
- **Charts**: each has a text or table version.
- **Language**: `lang` set on the page; quoted passages in another language get their own `lang` when known.
- **Plain language**: short sentences, common words, terms explained on first use (section [14](#14-microcopy)).
- **Dragging** (2.5.7): every drag (moving a project to a collection, resizing the canvas) has a click or keyboard alternative.
- **Testing**: automatic checks (axe) in the test suite; a manual pass with NVDA on Windows and VoiceOver on macOS and iOS before each release.

---

## 13. Low bandwidth and field use

Some users will be in field offices with slow or costly connections, often on phones. The aim is that the brief is readable on a slow link, even if the rest loads later.

### 13.1 Budgets

| Measure | Budget |
|---|---|
| Time to interactive, low-end Android on slow 3G (400 ms RTT, 400 kbps) | Under 5 s for Home and the brief |
| JavaScript for the first screen | Under 170 KB compressed |
| Fonts | One variable font file, under 100 KB; none in low data mode |
| Images | None needed for any screen; logos as SVG under 5 KB |
| A brief's first paint | The header, bottom line and caveat in the first response |

Budgets are checked in CI with Lighthouse on a throttled profile.

### 13.2 How

- **Text first.** The brief endpoint returns the whole brief as JSON; the screen renders the header and bottom line first, then the rest. Passages load when a chip is opened, not before.
- **Split code by route.** The masking review, evaluation, compare and PRISMA code load only when used.
- **SSE, not polling.** One small event at a time.
- **Low data mode** (Account preferences, and offered automatically when the browser reports `Save-Data` or a slow connection): system fonts, no animations, no avatars, paper rows without abstracts, and live progress updated every 10 seconds instead of every event.
- **Resume after a drop.** Unsent composer text is kept in memory (and in `sessionStorage` if the user allows it, never question text in `localStorage` for privacy). The event stream resumes from the last id.
- **Offline reading.** The brief can be downloaded as a single-file PDF or Markdown, with the caveat and sources. A full offline mode (service worker cache of opened briefs) is a later phase and must respect `Cache-Control: no-store` rules; it needs a security review first.

---

## 14. Microcopy

### 14.1 Voice

- Short sentences. Common words. Active voice.
- Say what happened and what to do next. "The model server isn't answering. Your question is saved. Try again in a minute."
- No hype. Not "AI-powered insights". Say "the model", not "AI", and never give it a personality ("I think…").
- Don't blame the user. "That file type isn't supported", not "You uploaded an invalid file".
- Numbers as digits. Times as "27 min", "about 6 min left".
- British or American spelling: pick one for the whole UI (the docs use British "organisation").

### 14.2 Glossary

Words that mean one thing everywhere in the interface:

| Word | Means | Don't say |
|---|---|---|
| Project | A space with its members, documents, reviews, briefs | Workspace, folder |
| Collection | A group of projects | Folder, space |
| Review | One question, its search, its runs and brief | Session, chat |
| Search plan, draft | The categories and concepts for the search | Query (in the UI) |
| Run | One job that runs the stages | Pipeline, execution |
| Brief | The decision brief | Report, synthesis, answer |
| Option | One course of action the brief compares | Choice, alternative |
| Evidence | What the cited passages say | Fact, proof |
| Reasoning | The model's inference, not from a source | Hypothesis (except in tooltips) |
| Source | A paper or document cited by tag | Reference |
| Passage | The piece of text a claim is drawn from | Chunk |
| Masked | Personal details replaced before sending | Anonymised, redacted |
| Needs you | Something waiting for a person | Pending, blocked |
| Gate | (Internal only.) In the UI: "needs your review" | — |

### 14.3 Examples

| Situation | Write | Not |
|---|---|---|
| Run failed | "Run stopped at Screening. The model server isn't answering." | "Error 503: upstream LLM unavailable" |
| Nothing relevant | "No paper matched every concept. Try fewer concepts in a category." | "0 results" |
| Masking | "1 location was masked before sending." | "PII redaction applied" |
| Weak evidence | "Weak evidence: treat as a lead, not a finding." | "Low confidence (32%)" |
| Brief ready | "Decision brief ready." | "Your AI analysis is complete!" |
| Cancel | "Stop this run? Finished stages are kept." | "Are you sure?" |

---

## 15. Delivery phases

Each phase is usable on its own. Endpoints marked **new** are not in the API yet.

| Phase | Screens and parts | API it needs |
|---|---|---|
| **P1: read a brief** | App shell, sign-in and MFA, re-auth, Home (pinned, recent), project list (no grouping), workspace with chat (question, receipt, draft, run strip, screening, brief cards), brief canvas read-only, source panel, how this was made, light and dark | Auth endpoints; `POST /projects`; `…/plan`, `/plan/answers`, `/plan/changes`, `/plan/accept`; runs and their events (`POST /reviews/{id}/runs`, `GET /runs/{run_id}/events`, `POST /runs/{run_id}/cancel`); `…/brief`, `/brief/evidence/{tag}`, `/brief/trace`; `…/status` |
| **P2: run a review well** | Search draft canvas with diff and origin, gates (vocabulary, privacy review), documents, screening table with expanded rows, rules editor, PRISMA, downloads, ask with threads, project settings and members | `…/documents`; `…/papers`; `PUT …/rules`; `…/exports/{name}`; `…/questions` (+ **new** thread id); `gate.*` events and `POST /gates/{gate_id}`; members endpoints |
| **P3: find and compare** | Needs you, collections and tags, grouping and filters, project timeline, compare runs, search and command palette, seen and saved marks, trade-off priorities, brief versions and compare, your view first | **new**: collections, tags, pins, seen and saved, `GET /search`, run manifests endpoint, brief versions, "your view" storage |
| **P4: field and admin** | Low data mode, offline brief download, evaluation admin, retention notices, coverage estimate | `/admin/evaluations`; **new**: retention settings, coverage estimate |

---

## 16. Open questions

1. **Collections:** should a collection have its own owner and members, or is it only a label that each project owner sets? (This document assumes a label, with no access rules of its own.)
2. **Several reviews per project:** first version with one review per project, or several from the start?
3. **"Your view first":** off by default, as proposed, or on for new projects?
4. **Sharing outside the organisation:** never, or a read-only export only (PDF)? This document assumes export only.
5. **Phones:** full features, or read-only (briefs, answers, Needs you) for the first version?
6. **Direct editing of the search draft** (pills) versus chat only.
7. **Languages:** English only first? French and Arabic matter for ICRC field teams; Arabic needs right-to-left layouts, which affects the three-zone layout.
8. **Notifications:** browser notifications only, or email for "Needs you" and "Brief ready" (Mailpit is in the stack)?
9. **Who can see raw-text views in the audit log:** owners only, or also admins?

---

## 17. References

Sources found on 2 November 2026. Items marked *(not verified)* were found only through search results or second-hand descriptions.

### Labs named in the request

- ETH PEACH Lab (Prof. April Wang): https://peachlab.inf.ethz.ch/ (site renders with JavaScript; *not verified* in full). GitHub: https://github.com/eth-peach-lab. Focus: human-centred programming tools and AI literacy. No paper on LLM chat interfaces was found; its relevance here is AI literacy and avoiding over-delegation.
- ETH IVIA Lab (Prof. Mennatallah El-Assady): https://ivia.ethz.ch/
  - RELIC, claim-level consistency of LLM answers (CHI 2024): https://arxiv.org/abs/2311.16842
  - DxHF, claim decomposition for comparing responses (UIST 2025): https://arxiv.org/abs/2507.18802
  - generAItor, visual analytics of LLM outputs (TiiS 2024): https://arxiv.org/abs/2403.07627
  - explAIner, provenance and trust in XAI (TVCG 2019): https://arxiv.org/abs/1908.00087
- HIVE CAI Lab, Cambridge (Dr Malak Sadek): https://hive.chia.cam.ac.uk/
  - Value-Sensitive Conversational Agent framework: https://arxiv.org/abs/2310.11848 and https://www.vsca-framework.com/ (useful for co-design sessions with ICRC staff)
  - Research agenda: https://hive.chia.cam.ac.uk/Research%20Agenda%20%5BJuly%202026%5D.pdf

### Disclosure, trust and over-reliance

- Springer and Whittaker, progressive disclosure and transparency (TiiS 2020): https://arxiv.org/abs/1811.02164
- Vasconcelos et al., explanations reduce over-reliance only when checking is easy (CSCW 2023): https://arxiv.org/abs/2212.06823
- Buçinca et al., cognitive forcing functions (CSCW 2021): https://doi.org/10.1145/3449287
- Kim et al., expressing uncertainty in first person reduces over-reliance (FAccT 2024): https://arxiv.org/abs/2405.00623
- Ding et al., citations raise trust even when random (AAAI 2025): https://arxiv.org/abs/2501.01303

### Evidence display

- GRADE and Summary of Findings tables, user testing: https://pmc.ncbi.nlm.nih.gov/articles/PMC4608675
- DECIDE interactive Summary of Findings: https://www.decide-collaboration.eu/sites/decide-collaboration.eu/files/uploads/DECIDE%20health%20systems.pdf
- 3ie Evidence Gap Maps: https://www.3ieimpact.org/evidence-hub/evidence-gap-maps
- ALNAP, "Insufficient Evidence?" (quality criteria for humanitarian evidence): https://jliflc.com/wp-content/uploads/2016/12/alnap-study-evidence.pdf
- Scite Smart Citations: https://library.hkust.edu.hk/sc/trial-scite/
- Consensus Meter and its critique: https://aarontay.substack.com/p/a-2025-deep-dive-of-consensus-promises

### Agents, planning and screening

- OpenAI Deep Research FAQ: https://help.openai.com/en/articles/10500283-deepresearch-faq
- Gemini Deep Research plan editing *(not verified; third-party guide)*: https://www.ai-toolbox.co/gemini-management-and-productivity/how-to-use-gemini-deep-research-2026
- Magentic-UI, co-planning and co-tasking (2025): https://arxiv.org/abs/2507.22358
- Elicit systematic review: https://elicit.com/blog/systematic-review and https://support.elicit.com/en/articles/2601281
- Undermind coverage estimate and its limits: https://www.undermind.ai/whitepaper.pdf and https://library.smu.edu.sg/topics-insights/undermindai-platform-has-been-revamped-undermind-projects-feature
- Trace visualisation of multi-agent systems (IJCAI 2026) *(not verified in full)*: https://ijcai.org/proceedings/2026/987

### Sensemaking and literature tools

- Graphologue (UIST 2023): https://hci.ucsd.edu/papers/graphologue.pdf
- Sensecape (UIST 2023): https://sensecape.github.io
- Luminate (CHI 2024): https://arxiv.org/abs/2310.12953
- Supporting sensemaking of LLM outputs at scale (CHI 2024): https://arxiv.org/abs/2401.13726
- Synergi (UIST 2023): https://arxiv.org/abs/2308.07517
- Relatedly (CHI 2023): https://arxiv.org/abs/2302.06754
- CiteSee (CHI 2023): https://arxiv.org/abs/2302.07302
- Semantic Reader: https://arxiv.org/abs/2303.14334

### Trade-offs

- ValueCharts: https://www.cs.ubc.ca/group/iui/VALUECHARTS/ and https://www.cs.ubc.ca/~carenini/PAPERS/avi04published.pdf
- Evaluating uncertainty visualisation (InfoVis 2018): https://www.tableau.com/en-gb/research/publications/pursuit-error-survey-uncertainty-visualization-evaluation

### History and grouping

- ChatGPT Projects: https://openai.com/academy/projects
- Claude Projects *(link from a search result; check it)*: https://support.anthropic.com/en/articles/9517075-what-are-projects
- Perplexity Spaces: https://intercom.help/perplexity-ai/en/articles/10352961-what-are-spaces
- Conversations in Space, chat and canvas history (2026 draft) *(not peer reviewed)*: https://arxiv.org/abs/2605.15848

### Humanitarian context, access and bandwidth

- ICRC AI policy: https://www.icrc.org/en/publication/building-responsible-humanitarian-approach-icrcs-policy-artificial-intelligence
- ICRC website accessibility statement: https://www.icrc.org/en/accessibility
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- Low-bandwidth website guidelines (UK DFID / Aptivate): https://www.gov.uk/research-for-development-outputs/guidelines-for-low-bandwidth-websites
- GOV.UK progressive enhancement: https://www.gov.uk/service-manual/making-software/progressive-enhancement
- Performance budgets: https://web.dev/articles/performance-budgets-101
- Principles for Digital Development: https://crvs.unescap.org/sites/default/files/resources/Principles%20for%20Digital%20Development.pdf
