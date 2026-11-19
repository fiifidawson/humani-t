# P1 test prompts

What to type into the app to test a real review, and what should happen. Start the app first (API, worker and web; see [00-setup.md](walkthroughs/00-setup.md)), then open `http://localhost:5173`.

## 1. The main run: one clear question

Create a project, then ask:

> Do cash transfers improve food security more than in-kind food aid in humanitarian settings?

Answer any clarifying questions briefly, choose **Quick**, accept the draft and start the run.

**Expect:** the run goes Discover → Screen → Brief, and a brief appears with citations. Click a citation to see its source, and open "How this was made".

If it stops, note the message on screen and the last lines of the worker window.

## 2. More questions to try

Each in its own project:

> Does community-based management of acute malnutrition reduce child deaths in conflict-affected areas?

> How effective are oral cholera vaccination campaigns in refugee camps?

> Do mental health and psychosocial support programmes reduce depression among displaced adults?

**Expect:** the same as test 1.

## 3. A question that is too broad

> Does aid work?

**Expect:** clarifying questions before any draft.

## 4. A question with nothing to find

> What is the effect of hydroponic yak farming on refugee camp morale in Antarctica?

**Expect:** the run stops after Discover with "The search found no papers. Ask for changes to broaden it, then run it again." It doesn't screen or write a brief.

## 5. Masking

Ask, with masking on:

> Kofi Mensah (kofi.mensah@example.org) wants to know: did cash transfers in Gao improve school attendance?

**Expect:** the privacy receipt shows placeholders such as `[PERSON_1]` and `[EMAIL_1]`, not the name or email address. The place is masked too while the "mask countries and regions" setting is on.

Masking can be turned off in Project settings → Privacy only if the deployment allows it. With your hosted model (gpt-5-mini), the switch stays on and says why. That is correct.

## 6. Changes after a run

After test 1 finishes, type each of these into the chat box (it reads "Change the search"), one at a time. Each gives a new draft; accept it and say which run starts.

| Type | Expect the run to start from |
| --- | --- |
| Also search for mobile money transfers. | Discover (the search terms changed) |
| Only include randomised controlled trials. | Screen (it reuses the papers already found) |
| Make the brief shorter, with the key numbers first. | Brief |

## 7. Follow-up questions

Switch the chat box to **Ask**, then try:

> Which of the included studies were in sub-Saharan Africa?

> How large were the samples in the included trials?

**Expect:** an answer that cites papers from the project. When nothing answers it: "Nothing in this project answers that."

## 8. Instructions for the model

In Project settings → Instructions for the model:

- **Screening:** `Exclude studies published before 2010.`
- **Writing:** `Write for a programme manager. Keep it under 300 words.`

Then run again from Screen (test 6, row 2). **Expect:** older papers are excluded, the brief is shorter, and "How this was made" shows the instructions with their version.

## What to send back

For each test: passed, or where it stopped, with the on-screen message and the worker's last lines. For test 1, also say whether the brief reads sensibly.
