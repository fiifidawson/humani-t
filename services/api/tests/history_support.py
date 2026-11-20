"""Helpers for the history and marks specs: records the pipeline would have written.

Papers and drafts are stored as their own stages store them, sealed under the project's
key through the same `PaperStore`, so the services read them exactly as they read real
ones. Runs, gates and briefs come from a real worker (`run_support`, `brief_support`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.application.papers import PaperStore
from app.domain.discovery import StoredPaper, identity_digest
from app.domain.paper import PaperRecord
from app.domain.plans import PlanAuthor, StoredPlanVersion
from app.domain.reviews import ReviewTrigger
from app.domain.runs import JsonValue
from app.domain.vocabulary import VocabularyOrigin
from app.runs.base import StageContext
from tests.run_support import RunTeam, create_review
from tests.support import AuthHarness

DOI = "10.1016/j.foodpol.2026.102345"
TITLE = "Cash transfers and food prices in small Sahel markets"
MODEL = "qwen3.5:9b"


class ModelStage:
    """A stage that reports which model it used, as the screen and answer stages do."""

    def __init__(self, model_id: str = MODEL) -> None:
        self.model_id = model_id

    async def run(self, context: StageContext) -> Mapping[str, JsonValue]:
        await context.progress(1, 1, "Item 1")
        return {"items": 1, "manifest": {"model_id": self.model_id}}


def run(harness: AuthHarness, work: Any) -> Any:
    async def inside() -> Any:
        async with harness.providers.storage.unit_of_work() as uow:
            result = await work(uow)
            await uow.commit()
            return result

    return asyncio.run(inside())


def add_paper(
    harness: AuthHarness,
    project_id: str,
    review_id: str,
    *,
    doi: str | None = DOI,
    title: str = TITLE,
    year: int | None = 2026,
) -> str:
    """Store a paper in the review as the discover stage would; return its id."""
    store = PaperStore(harness.providers.encryption)
    record = PaperRecord(
        source_id="openalex",
        external_id=f"W{uuid.uuid4().hex[:8]}",
        title=title,
        year=year,
        doi=doi,
    )
    paper_id = uuid.uuid4()

    async def work(uow: Any) -> None:
        project = UUID(project_id)
        key = await store.identity_key(uow, project)
        await uow.papers.add(
            StoredPaper(
                id=paper_id,
                project_id=project,
                review_id=UUID(review_id),
                first_run_id=uuid.uuid4(),
                identity_digest=identity_digest(key, record.identity),
                source_id=record.source_id,
                record_sealed=await store.seal_record(uow, project, paper_id, record),
                created_at=harness.clock(),
            )
        )

    run(harness, work)
    return str(paper_id)


def add_draft(harness: AuthHarness, review_id: str, version: int = 1) -> None:
    """Store a drafted plan version waiting for accept, and move the review to drafting."""

    async def work(uow: Any) -> None:
        review = await uow.reviews.get(UUID(review_id))
        await uow.plans.add_version(
            StoredPlanVersion(
                review_id=review.id,
                project_id=review.project_id,
                version=version,
                origin=VocabularyOrigin.LLM_ASSISTED,
                author=PlanAuthor.model(),
                created_at=harness.clock(),
                body_sealed=b"sealed plan body",
            )
        )
        if version == 1:
            await uow.reviews.save(review.transitioned(ReviewTrigger.DRAFT))

    run(harness, work)


def accept_draft(harness: AuthHarness, review_id: str, user_id: str, version: int = 1) -> None:
    async def work(uow: Any) -> None:
        review = await uow.reviews.get(UUID(review_id))
        assert await uow.plans.accept(review.id, version, UUID(user_id), harness.clock())
        await uow.reviews.save(review.transitioned(ReviewTrigger.ACCEPT))

    run(harness, work)


def second_project(rt: RunTeam, name: str) -> tuple[str, str]:
    """Another project the owner creates, with its own review; return both ids."""
    from tests.project_support import create_project

    project_id: str = create_project(rt.owner, name)["id"]
    review_id: str = create_review(rt.owner, project_id)["id"]
    return project_id, review_id
