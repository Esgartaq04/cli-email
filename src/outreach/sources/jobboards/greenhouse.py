from __future__ import annotations

import json
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.net.fetcher import Fetcher
from outreach.types import PostingRef

_US_HINTS = ("united states", "usa", ", ca", ", ny", ", il", ", tx", ", wa",
             ", ma", ", co", "remote - us", "remote (us")


class GreenhouseBoardSource:
    """Public Greenhouse board API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def _matches_region(self, location: str, region: str) -> bool:
        if region.upper() != "US":
            return True
        lowered = location.lower()
        return any(hint in lowered for hint in _US_HINTS)

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        terms = [t.lower() for t in role_terms]
        found: list[PostingRef] = []

        for token in self.tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            outcome = self.fetcher.get(url)
            if outcome.outcome != "ok" or not outcome.body:
                continue
            try:
                payload = json.loads(outcome.body)
            except json.JSONDecodeError:
                continue

            for job in payload.get("jobs", []):
                title = job.get("title", "")
                location = (job.get("location") or {}).get("name", "")
                if not any(term in title.lower() for term in terms):
                    continue
                if not self._matches_region(location, region):
                    continue
                found.append(PostingRef(
                    company_name=job.get("company_name") or token,
                    company_domain=canonical_domain(f"{token}.com"),
                    title=title,
                    url=job.get("absolute_url", url),
                    location=location,
                ))
        return found
