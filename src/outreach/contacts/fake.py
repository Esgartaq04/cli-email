from __future__ import annotations

from typing import Mapping, Sequence

from outreach.core.people import title_matches
from outreach.types import EmailStatus, FoundPerson


class FakePeopleFinder:
    """A `PeopleFinder` over a fixed table of people, recording what it was asked.

    `people` maps a domain to who works there. `counts` overrides the head count
    for a domain; otherwise it is the number of people held. `statuses` sets what
    `verify` answers for an address (anything else is "verified"). A search that
    names a title in `failing_titles` raises, as a Hunter outage would, and
    `balance_error` makes the balance unreadable.
    """

    def __init__(self, people: Mapping[str, Sequence[FoundPerson]] | None = None, *,
                 balance: int = 500, counts: Mapping[str, int] | None = None,
                 statuses: Mapping[str, EmailStatus] | None = None,
                 failing_titles: frozenset[str] = frozenset(),
                 balance_error: Exception | None = None) -> None:
        self._people = dict(people or {})
        self._balance = balance
        self._counts = dict(counts or {})
        self._statuses = dict(statuses or {})
        self._failing_titles = failing_titles
        self._balance_error = balance_error
        self.count_calls: list[str] = []
        self.search_calls: list[tuple[str, tuple[str, ...], int]] = []
        self.verify_calls: list[str] = []

    def count_people(self, domain: str) -> int:
        self.count_calls.append(domain)
        return self._counts.get(domain, len(self._people.get(domain, ())))

    def search_people(self, domain: str, titles: Sequence[str], limit: int) -> list[FoundPerson]:
        self.search_calls.append((domain, tuple(titles), limit))
        if any(title in self._failing_titles for title in titles):
            raise RuntimeError(f"search failed for {domain}")
        matching = [person for person in self._people.get(domain, ())
                    if any(title_matches(person.title, wanted) for wanted in titles)]
        return matching[:max(limit, 0)]

    def verify(self, email: str) -> EmailStatus:
        self.verify_calls.append(email)
        return self._statuses.get(email, "verified")

    def balance_tenths(self) -> int:
        if self._balance_error is not None:
            raise self._balance_error
        return self._balance
