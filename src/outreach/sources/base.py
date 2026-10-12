from __future__ import annotations

from typing import Protocol, Sequence

from outreach.types import PostingRef


class JobBoardSource(Protocol):
    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]: ...
