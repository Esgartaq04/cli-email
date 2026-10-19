from __future__ import annotations

from outreach.types import ParsedPost


class FakeLLM:
    """Records fixed responses. No test touches a live API."""

    def __init__(self, titles: list[str] | None = None,
                 parsed_posts: dict[str, ParsedPost] | None = None) -> None:
        self._titles = titles or []
        self._parsed_posts = parsed_posts or {}
        self.parse_calls = 0

    def expand_titles(self, role_title: str) -> list[str]:
        return self._titles or [role_title]

    def parse_job_post(self, text: str) -> ParsedPost | None:
        self.parse_calls += 1
        return next((post for key, post in self._parsed_posts.items() if key in text), None)
