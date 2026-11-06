"""What can go wrong when asking a paper source, in terms every layer can recognise.

Only failures belong here. How each source is queried is its provider's
business; the application only needs to tell "this source could not be searched"
apart from "this source found nothing".
"""

from __future__ import annotations


class PaperSourceUnavailableError(Exception):
    """A source could not be searched: unreachable, refusing, or answering unreadably.

    Distinct from an empty result on purpose. A run that silently treats an
    outage as "no papers" under-reports what exists, and the reviewer cannot tell.
    """

    def __init__(self, source_id: str, reason: str) -> None:
        super().__init__(f"{source_id}: {reason}")
        self.source_id = source_id
        self.reason = reason
