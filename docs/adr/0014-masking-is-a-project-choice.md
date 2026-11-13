# ADR-0014: Masking is a project's choice, within the operator's policy

- **Status:** Accepted
- **Date:** 2026-11-13
- **Changes:** "the one rule" in `CLAUDE.md`, which said data leaves only after it has been masked; [ADR-0011](0011-countries-and-regions-as-an-operator-setting.md) and the always-masked documents of walkthrough 16 hold only while a project's masking applies

## Context

Until now every text a model reads was masked: questions, clarifying answers, change requests and follow-up questions with the question list, papers with the narrow list, uploaded documents with the broad list and a person's review. The owner's supervisor asked for masking to be a choice per project. A team running its own model on its own machine may want the model to read names and places as written, because masking costs quality (ADR 0011 shows how much a question loses), and nothing leaves the deployment anyway.

Sending unmasked text to a model the deployment does not run itself is different: that is the case the one rule exists for.

## Decision

- Each project has a switch, `is_masking_on`. A new project starts on when the configured model is hosted, and off when the deployment runs it itself.
- "Hosted" is decided from settings alone (`Settings.is_inference_hosted`): the model server is outside the deployment (not loopback, a private network or a compose service name), or the model is an Ollama `*-cloud` model. The demo model is never hosted.
- A new operator setting, `allow_unmasked_hosted_inference` (default `false`), lets owners turn masking off while the model is hosted. It is refused at startup unless `allow_hosted_inference` is also true, and compose does not pass it through.
- Whether text is masked is decided each time text is about to leave, by one pure function, `is_masking_applied`: masked if the switch is on, or if the model is hosted and the operator has not allowed unmasked text. The stored switch is never trusted on its own, so a project switched off under a local model is masked again as soon as the operator points the deployment at a hosted model.
- Owners change the switch with `PUT /api/v1/projects/{project_id}/masking`. Turning it off needs a recent sign-in; turning it on never does. Turning it off while masking is required is refused with `409` and `masking_required`, as the last-owner refusal is: the request is valid, the deployment's state refuses it. Each change is audited as `project.masking_changed` with the new value.
- With masking effectively off:
  - typed text (the question, clarifying answers, change requests, follow-up questions) is sent without the name, place and pattern detectors;
  - the mask stage keeps paper text as written, recorded with the fingerprint `masking-off@1`;
  - uploaded documents are used once scanned and read, without the privacy review and its gate;
  - the project's own masking rules still apply everywhere, and the guard still refuses a rule term;
  - the guard does not refuse identifiers the owner chose to send.
- Masking on behaves exactly as before.
- What was sent stays honest: planning receipts and follow-up questions say `is_masked`, each stage that masks or sends text records `is_masked` in its manifest facts, the run manifest sums them up as `masking` (`on`, `off` or `mixed`), and the brief's trace says `is_masked`.
- Text kept with masking off is never sent once masking applies again: a paper kept that way is withheld until the mask stage masks it, a follow-up question asked that way fails with a plain message, and earlier turns and brief passages kept that way are left out of a later answer.

## Rejected

- **The owner always decides, whatever the model:** it would let any owner send names to a third party the operator chose for masked text only. The operator answers for that third party's terms (ADR 0010).
- **An operator-only switch for the whole deployment:** projects differ. One team's questions name no one; another's name staff and camps. The owner knows the project's text; the operator knows where it goes. Both get a say.
- **Trusting the stored switch:** a deployment that later moves to a hosted model would send every project switched off under the local model unmasked, without anyone deciding it.

## Consequences

- The one rule now reads: data leaves the deployment only to destinations the operator has configured, and text sent to a model the deployment does not run itself is masked first unless both the operator and the project's owner have chosen otherwise.
- A local deployment's new projects are unmasked by default. Their owners see that in Project settings → Privacy and can turn masking on.
- The document worker still makes a masked copy of each document with masking off. Nothing in it is sent while masking is off; it is there so turning masking back on needs no reprocessing, and the privacy review applies again from that moment.
- A plan drafted while masking was off may hold words the model copied from the unmasked question. With masking back on it is still sent as the plan always is: checked by the guard and the project's rules, not masked.
- Every existing project was masked, so migration 0016 backfills the switch on.
- Proven by `tests/test_masking_domain.py::TestMaskingChoice`, `tests/test_settings.py::TestUnmaskedHostedInference`, `tests/test_outbound_wiring.py::TestMaskingPolicy` and `tests/test_masking_choice.py`.
