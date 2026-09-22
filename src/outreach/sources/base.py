from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from outreach.types import PostingRef, SourceClass


class JobBoardSource(Protocol):
    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]: ...


@dataclass(frozen=True)
class SurfaceTarget:
    source_class: SourceClass
    url: str
