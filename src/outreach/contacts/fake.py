from __future__ import annotations

from typing import Sequence

from outreach.types import EmailStatus, PersonRef


class FakeContactProvider:
    def __init__(self, people_by_domain: dict[str, list[PersonRef]], credits: int) -> None:
        self._people = people_by_domain
        self._credits = credits
        self.find_calls: list[str] = []

    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]:
        self.find_calls.append(domain)
        return list(self._people.get(domain, []))

    def verify(self, email: str) -> EmailStatus:
        return "verified" if "@" in email else "not_found"

    def remaining_credits(self) -> int:
        return self._credits
