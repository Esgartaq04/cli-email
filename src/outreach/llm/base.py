from __future__ import annotations

from typing import Protocol

from outreach.types import ParsedPost


class LLMClient(Protocol):
    """Exactly two jobs, both for job runs: expand a role title into search
    terms, and read an HN post the rules can't. Neither decides who or what
    gets bought."""

    def expand_titles(self, role_title: str) -> list[str]: ...

    def parse_job_post(self, text: str) -> ParsedPost | None: ...
