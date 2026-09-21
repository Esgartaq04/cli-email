from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class RawClaim:
    claim: str
    quote: str
    theme: str


class LLMClient(Protocol):
    """Exactly three jobs. The gate is not one of them."""

    def expand_titles(self, role_title: str) -> list[str]: ...

    def extract_claims(self, text: str) -> list[RawClaim]: ...

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str: ...
