"""The last check before any model call: the exact outgoing text, scanned again.

`GuardedInference` asks this guard about every request (architecture §3.9.1).
Anything a detector finds outside a placeholder means the text was not masked,
or not masked enough, and the call is refused rather than repaired: a prompt
fixed up here would be a prompt nobody reviewed. The refusal names entity types
and counts, never a value, because refusals are logged and shown.

The guard runs the same pattern detector the sanitiser does, so text the
sanitiser masked always passes. Project rules are added per call by
`GuardedInference.with_rules`, because this guard is shared by every project.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence

from app.domain.inference import ChatRequest, PromptVerdict
from app.domain.masking import EntityType, leak_reason, unmasked
from app.privacy.base import Detector


class LeakCheckPromptGuard:
    def __init__(self, detectors: Sequence[Detector]) -> None:
        if not detectors:
            raise ValueError("LeakCheckPromptGuard.detectors must not be empty")
        self._detectors = tuple(detectors)

    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        # The schema travels with the request too, so it is checked like a message.
        outgoing = [m.content for m in request.messages]
        outgoing.append(json.dumps(dict(request.output_schema), sort_keys=True))
        found: Counter[EntityType] = Counter()
        for text in outgoing:
            for detector in self._detectors:
                spans = unmasked(text, await detector.detect(text))
                found.update(span.entity_type for span in spans)
        if found:
            return PromptVerdict.refuse(leak_reason(found))
        return PromptVerdict.allow()
