from __future__ import annotations

from typing import Sequence

from outreach.llm.base import RawClaim


class FakeLLM:
    """Records fixed responses. No test touches a live API."""

    def __init__(self, claims: list[RawClaim] | None = None,
                 titles: list[str] | None = None, summary: str = "summary text") -> None:
        self._claims = claims or []
        self._titles = titles or []
        self._summary = summary
        self.extract_calls = 0

    def expand_titles(self, role_title: str) -> list[str]:
        return self._titles or [role_title]

    def extract_claims(self, text: str) -> list[RawClaim]:
        self.extract_calls += 1
        return list(self._claims)

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str:
        return self._summary
