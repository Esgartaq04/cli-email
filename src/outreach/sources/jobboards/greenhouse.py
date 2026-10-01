from __future__ import annotations

import html
import json
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.core.workmode import classify_employment, classify_work_mode
from outreach.extraction.htmltext import html_to_text
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.region import matches_region
from outreach.types import PostingRef


class GreenhouseBoardSource:
    """Public Greenhouse board API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def _matches_region(self, location: str, region: str) -> bool:
        return matches_region(location, region)

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        terms = [t.lower() for t in role_terms]
        found: list[PostingRef] = []

        for token in self.tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            outcome = self.fetcher.get(url)
            if outcome.outcome != "ok" or not outcome.body:
                continue
            try:
                payload = json.loads(outcome.body)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            jobs = payload.get("jobs", [])
            if not isinstance(jobs, list):
                continue

            for job in jobs:
                if not isinstance(job, dict):
                    continue
                title = job.get("title", "")
                raw_location = job.get("location")
                location = (raw_location.get("name", "")
                            if isinstance(raw_location, dict) else "")
                if not any(term in title.lower() for term in terms):
                    continue
                if not self._matches_region(location, region):
                    continue
                # Board JSON carries the posting body HTML-escaped; unescape
                # before stripping tags. Missing/non-string content is "".
                raw_content = job.get("content")
                content = (html_to_text(html.unescape(raw_content))
                           if isinstance(raw_content, str) else "")
                found.append(PostingRef(
                    company_name=job.get("company_name") or token,
                    company_domain=canonical_domain(f"{token}.com"),
                    title=title,
                    url=job.get("absolute_url", url),
                    location=location,
                    work_mode=classify_work_mode(location, title, content),
                    employment_type=classify_employment(title, content),
                ))
        return found
