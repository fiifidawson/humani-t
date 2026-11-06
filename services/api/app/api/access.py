"""The gate in front of every route inside a project.

`require(action)` is a dependency that runs before the request body is read,
so the order of refusals is the same on every project route:

1. 401 if not signed in, 403 if a write lacks its CSRF token (`api/session.py`);
2. 404 if the project does not exist or the caller is not a member, with one
   body for both;
3. 403 if the caller's role does not allow the action;
4. 401 with `reauthentication_required` if the action needs a recent sign-in.

A non-member therefore learns nothing, not even whether their body was valid.
The gate does not replace the use case's own check: each use case checks again
inside the transaction that writes, because membership can change in between.
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
