from __future__ import annotations

from typing import Protocol, Sequence

from outreach.types import EmailStatus, PersonRef


class ContactProvider(Protocol):
    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]: ...

    def find_profile(self, domain: str, first_name: str, last_name: str) -> str | None: ...

    def verify(self, email: str) -> EmailStatus: ...

    def remaining_credits(self) -> int: ...
