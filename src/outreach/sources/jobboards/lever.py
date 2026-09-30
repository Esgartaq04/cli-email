from __future__ import annotations

import json
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.core.workmode import classify_employment, classify_work_mode
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.region import matches_region
from outreach.types import PostingRef, WorkMode

# "unspecified" is deliberately absent: it falls back to text classification.
_WORKPLACE_TYPES: dict[str, WorkMode] = {
    "remote": "remote", "hybrid": "hybrid", "on-site": "onsite",
}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


class LeverBoardSource:
    """Public Lever postings API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        terms = [t.lower() for t in role_terms]
        found: list[PostingRef] = []

        for token in self.tokens:
            url = f"https://api.lever.co/v0/postings/{token}?mode=json"
            outcome = self.fetcher.get(url)
            if outcome.outcome != "ok" or not outcome.body:
                continue
            try:
                postings = json.loads(outcome.body)
            except json.JSONDecodeError:
                continue
            if not isinstance(postings, list):
                continue

            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                title = _str(posting.get("text"))
                categories = posting.get("categories")
                if not isinstance(categories, dict):
                    categories = {}
                location = _str(categories.get("location"))
                if not any(term in title.lower() for term in terms):
                    continue
                country = _str(posting.get("country")) or None
                if not matches_region(location, region, country):
                    continue
                work_mode = _WORKPLACE_TYPES.get(_str(posting.get("workplaceType")).lower())
                found.append(PostingRef(
                    company_name=token,
                    company_domain=canonical_domain(f"{token}.com"),
                    title=title,
                    url=_str(posting.get("hostedUrl")) or url,
                    location=location,
                    work_mode=work_mode or classify_work_mode(location, title),
                    employment_type=classify_employment(
                        title, declared=_str(categories.get("commitment")) or None),
                ))
        return found
