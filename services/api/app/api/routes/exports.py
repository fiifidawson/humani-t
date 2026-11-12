"""Downloads of a review's results.

Any member may download what they may read; a non-member gets the same 404 as a
missing review. Every download is audited, never cached, and named from ids only.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.api.access import require_review
from app.api.dependencies import ExportsDep
from app.api.exports import render
from app.api.session import request_id
from app.application.accounts import Current
from app.domain.accounts import NotFoundError
from app.domain.exports import ExportName, content_disposition, download_filename
from app.domain.projects import Action

router = APIRouter(tags=["exports"])

_NAMES = ", ".join(f"`{e.value}`" for e in ExportName)


_Reader = Annotated[Current, require_review(Action.READ_PROJECT)]


@router.get(
    "/reviews/{review_id}/exports/{name}",
    summary="Download one of a review's results as a file",
    response_class=Response,
    responses={
        200: {"description": f"The file. Names: {_NAMES}."},
        404: {"description": "No such review, run, export or brief"},
    },
)
async def download_export(
    review_id: UUID,
    name: str,
    request: Request,
    current: _Reader,
    exports: ExportsDep,
    run_id: Annotated[
        UUID | None,
        Query(
            description="The run to export; the one each screen shows by default if omitted."
        ),
    ] = None,
) -> Any:
    export = ExportName.parse(name)
    if export is None:
        raise NotFoundError("No such export.")
    content = await exports.export(current, review_id, export, run_id, request_id(request))
    filename = download_filename(export, content.run_id)
    return Response(
        render(content),
        media_type=export.media_type,
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
