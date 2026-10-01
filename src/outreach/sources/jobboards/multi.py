from __future__ import annotations

from typing import Sequence

from outreach.sources.base import JobBoardSource
from outreach.types import PostingRef


class MultiBoardSource:
    """Fans a search out over several job boards and concatenates the results.

    One board failing (network bug, changed schema) must not cost the postings
    the others found, so exceptions are recorded in `errors` for the caller to
    surface rather than raised. `errors` accumulates across calls.
    """

    def __init__(self, sources: Sequence[JobBoardSource]) -> None:
        self.sources = tuple(sources)
        self.errors: list[str] = []

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        found: list[PostingRef] = []
        for source in self.sources:
            try:
                found.extend(source.search(role_terms, region))
            except Exception as exc:  # noqa: BLE001 - isolating one board is the point
                self.errors.append(f"{type(source).__name__}: {exc}")
        return found
