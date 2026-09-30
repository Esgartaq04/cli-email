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
    # Other paths this surface commonly lives at (/news for /press); tried only
    # if `url` is missing. Declared here; the runner decides how to sweep them.
    alternates: tuple[str, ...] = ()
