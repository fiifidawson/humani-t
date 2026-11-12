"""The gate in front of every route inside a project.

`require(action)` runs before the body is read, so refusals come in the same order
everywhere: 401 or CSRF 403, then 404 for a missing project or a non-member (one body
for both), then 403 for the role, then 401 if the action needs a recent sign-in.
Use cases check membership again inside the transaction that writes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends

from app.api.dependencies import ProjectsDep
from app.api.session import CurrentDep
from app.application.accounts import Current
from app.domain.projects import Action


def require(action: Action, *, recent_sign_in: bool = False) -> Any:
    """A dependency that admits members allowed `action`, and returns the caller."""

    async def gate(project_id: UUID, current: CurrentDep, projects: ProjectsDep) -> Current:
        await projects.authorize(
            current, project_id, action, needs_recent_sign_in=recent_sign_in
        )
        return current

    return Depends(gate)
