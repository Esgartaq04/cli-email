from __future__ import annotations

from typing import Protocol, Sequence

from outreach.types import EmailStatus, FoundPerson


class PeopleFinder(Protocol):
    """Finds and checks the people at a company, and knows what is left to spend.

    Credits are whole tenths (see `outreach.credits`); the comments say what each
    call costs, which is what a run's budget is built from.
    """

    def count_people(self, domain: str) -> int:
        """How many personal addresses the company has. Free."""
        ...

    def search_people(self, domain: str, titles: Sequence[str], limit: int) -> list[FoundPerson]:
        """Up to `limit` people whose title matches any of `titles`. One credit per
        address returned."""
        ...

    def verify(self, email: str) -> EmailStatus:
        """Check one address. Half a credit."""
        ...

    def balance_tenths(self) -> int:
        """The credits left, in tenths. Free."""
        ...
