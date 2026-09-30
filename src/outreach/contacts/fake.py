from __future__ import annotations

from typing import Sequence

from outreach.types import EmailStatus, PersonRef


class FakeContactProvider:
    def __init__(self, people_by_domain: dict[str, list[PersonRef]], credits: int,
                 profiles: dict[str, str] | None = None) -> None:
        self._people = people_by_domain
        self._credits = credits
        # Keyed by "First Last", the way `find_profile` is asked.
        self._profiles = profiles or {}
        self.find_calls: list[str] = []
        self.profile_calls: list[str] = []

    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]:
        self.find_calls.append(domain)
        return list(self._people.get(domain, []))

    def find_profile(self, domain: str, first_name: str, last_name: str) -> str | None:
        name = f"{first_name} {last_name}"
        self.profile_calls.append(name)
        return self._profiles.get(name)

    def verify(self, email: str) -> EmailStatus:
        return "verified" if "@" in email else "not_found"

    def remaining_credits(self) -> int:
        return self._credits
