from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class QuotaPlan:
    process: list[int]
    skipped: list[int]


def allocate_quota(company_ids: Sequence[int], remaining: int) -> QuotaPlan:
    """Split a rank-ordered queue against the credits actually available.

    Running out of credits is a normal condition, not an error: the remainder
    is reported so the report can say `skipped — quota` rather than going quiet.
    """
    budget = max(0, remaining)
    ordered = list(company_ids)
    return QuotaPlan(process=ordered[:budget], skipped=ordered[budget:])
