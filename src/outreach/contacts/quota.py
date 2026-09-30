from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


class CreditBudget:
    """Shared in-run budget tracking paid Hunter API credits.

    Tracks remaining and spent credits for enrichment, contact search, and
    LinkedIn lookups so no stage can overspend the available budget.
    """

    def __init__(self, remaining: int):
        """Initialize budget with the given remaining credits.

        Negative budgets are treated as zero.
        """
        self.remaining = max(0, remaining)
        self.spent = 0

    def try_spend(self, cost: int) -> bool:
        """Attempt to spend credits from the budget.

        Returns True if the cost was deducted (or free call with cost <= 0).
        Returns False if the cost exceeds remaining budget; budget unchanged.
        Never goes negative.
        """
        if cost <= 0:
            return True
        if cost <= self.remaining:
            self.remaining -= cost
            self.spent += cost
            return True
        return False


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
