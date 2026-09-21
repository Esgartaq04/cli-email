from __future__ import annotations

from typing import Sequence

from outreach.types import PostingRef


class FakeJobBoardSource:
    def __init__(self, postings: list[PostingRef]) -> None:
        self._postings = postings

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        return list(self._postings)
