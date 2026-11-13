"""Calling a model with the leak check in front: the only way use cases reach a model.

The guard lives here, not in each provider, so that no provider can be written
or configured without it. Use cases are given a `GuardedInference`, never a bare
`InferenceProvider`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from app.domain.inference import ChatRequest, ChatResult, PromptRefusedError, PromptVerdict
from app.domain.masking import EntityType, MaskingRule, ProjectPrivacy, leak_reason, rule_spans
from app.inference.base import InferenceProvider, PromptGuard


class GuardedInference:
    def __init__(self, provider: InferenceProvider, guard: PromptGuard) -> None:
        self._provider = provider
        self._guard = guard

    def with_rules(self, rules: Sequence[MaskingRule]) -> GuardedInference:
        """The same model and guard, also refusing any of a project's always-masked terms.

        The guard is shared by every project, so a use case that knows its
        project adds that project's rules here before calling.
        """
        if not rules:
            return self
        return GuardedInference(self._provider, _ProjectRuleGuard(self._guard, rules))

    def for_project(self, privacy: ProjectPrivacy) -> GuardedInference:
        """The guard as the project's masking choice needs it, its rules always included.

        With masking off the identifier patterns are not checked: the owner chose to send
        the text as written (ADR 0014). The rule terms are refused either way.
        """
        if privacy.is_masked:
            return self.with_rules(privacy.rules)
        return GuardedInference(self._provider, _ProjectRuleGuard(_NoPatterns(), privacy.rules))

    @property
    def model_id(self) -> str:
        return self._provider.model_id

    async def chat(self, request: ChatRequest) -> ChatResult:
        """Inspect the exact request, then send it. Fails closed.

        A refusal raises `PromptRefusedError`, and a crash in the guard propagates: either
        way nothing is sent, because a check that crashed has not checked anything.
        """
        verdict = await self._guard.inspect(request)
        if not verdict.is_allowed:
            raise PromptRefusedError(verdict.reason)
        return await self._provider.chat(request)


class _NoPatterns:
    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        return PromptVerdict.allow()


class _ProjectRuleGuard:
    def __init__(self, inner: PromptGuard, rules: Sequence[MaskingRule]) -> None:
        self._inner = inner
        self._rules = tuple(rules)

    async def inspect(self, request: ChatRequest) -> PromptVerdict:
        verdict = await self._inner.inspect(request)
        if not verdict.is_allowed:
            return verdict
        found: Counter[EntityType] = Counter(
            span.entity_type
            for message in request.messages
            for span in rule_spans(message.content, self._rules)
        )
        return PromptVerdict.refuse(leak_reason(found)) if found else verdict
