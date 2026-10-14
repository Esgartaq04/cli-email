from __future__ import annotations

from typing import Sequence

from outreach.types import PostingRef


class FakeJobBoardSource:
    def __init__(self, postings: list[PostingRef]) -> None:
        self._postings = postings
        self.calls = 0

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        self.calls += 1
        if not role_terms:
            return []
        return list(self._postings)
