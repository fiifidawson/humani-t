"""In-memory plan storage: the conversation, plan versions and privacy receipts.

Kept beside `memory.py` rather than in it so the plan tables travel together;
`memory.py` holds one `PlanState` in its state and copies it with the rest, so
plan writes commit and roll back with everything else in the unit of work.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.domain.errors import ConcurrentUpdateError
from app.domain.plans import StoredConversation, StoredPlanVersion, StoredReceipt


class PlanState:
    def __init__(self) -> None:
        self.conversations: dict[UUID, StoredConversation] = {}
        self.versions: dict[tuple[UUID, int], StoredPlanVersion] = {}
        self.receipts: dict[UUID, StoredReceipt] = {}

    def copy(self) -> PlanState:
        clone = PlanState()
        clone.conversations = dict(self.conversations)
        clone.versions = dict(self.versions)
        clone.receipts = dict(self.receipts)
        return clone


class MemoryPlanRepository:
    def __init__(self, state: PlanState) -> None:
        self._state = state

    async def get_conversation(self, review_id: UUID) -> StoredConversation | None:
        return self._state.conversations.get(review_id)

    async def add_conversation(self, conversation: StoredConversation, /) -> None:
        if conversation.review_id in self._state.conversations:
            raise ConcurrentUpdateError(
                f"review {conversation.review_id} already has a planning conversation"
            )
        self._state.conversations[conversation.review_id] = conversation

    async def save_conversation(
        self, conversation: StoredConversation, /
    ) -> StoredConversation:
        stored = self._state.conversations.get(conversation.review_id)
        if stored is None:
            raise ValueError(f"review {conversation.review_id} has no planning conversation")
        if stored.version != conversation.version:
            raise ConcurrentUpdateError(
                f"the conversation of review {conversation.review_id} changed concurrently"
            )
        saved = replace(conversation, version=conversation.version + 1)
        self._state.conversations[conversation.review_id] = saved
        return saved

    async def add_version(self, version: StoredPlanVersion, /) -> None:
        key = (version.review_id, version.version)
        if key in self._state.versions:
            raise ConcurrentUpdateError(
                f"review {version.review_id} already has plan version {version.version}"
            )
        self._state.versions[key] = version

    async def get_version(self, review_id: UUID, version: int) -> StoredPlanVersion | None:
        return self._state.versions.get((review_id, version))

    async def latest_version(self, review_id: UUID) -> StoredPlanVersion | None:
        versions = await self.list_versions(review_id)
        return versions[-1] if versions else None

    async def latest_accepted(self, review_id: UUID) -> StoredPlanVersion | None:
        accepted = [v for v in await self.list_versions(review_id) if v.is_accepted]
        return accepted[-1] if accepted else None

    async def list_versions(self, review_id: UUID) -> list[StoredPlanVersion]:
        versions = [v for (r, _), v in self._state.versions.items() if r == review_id]
        return sorted(versions, key=lambda v: v.version)

    async def accept(self, review_id: UUID, version: int, by: UUID, at: datetime) -> bool:
        latest = await self.latest_version(review_id)
        if latest is None or latest.version != version or latest.is_accepted:
            return False
        self._state.versions[(review_id, version)] = replace(
            latest, accepted_at=at, accepted_by=by
        )
        return True

    async def add_receipt(self, receipt: StoredReceipt, /) -> None:
        if receipt.id in self._state.receipts:
            raise ValueError(f"receipt {receipt.id} already exists")
        self._state.receipts[receipt.id] = receipt

    async def list_receipts(self, review_id: UUID) -> list[StoredReceipt]:
        receipts = [r for r in self._state.receipts.values() if r.review_id == review_id]
        # Stable: receipts written at the same instant keep the order they were written in.
        return sorted(receipts, key=lambda r: r.created_at)
