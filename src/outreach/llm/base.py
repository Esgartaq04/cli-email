from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from outreach.types import ParsedPost


@dataclass(frozen=True)
class RawClaim:
    claim: str
    quote: str
    theme: str


class LLMClient(Protocol):
    """Exactly four jobs. The gate, stage and size are not among them;
    parse_job_post only extracts fields that must appear in the post."""

    def expand_titles(self, role_title: str) -> list[str]: ...

    def extract_claims(self, text: str) -> list[RawClaim]: ...

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str: ...

    def parse_job_post(self, text: str) -> ParsedPost | None: ...
