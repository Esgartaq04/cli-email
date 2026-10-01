from __future__ import annotations

import json
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.core.workmode import classify_employment, classify_work_mode
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.region import matches_region
from outreach.types import PostingRef, WorkMode

# Ashby's structured workplaceType, when present, beats guessing from text.
_WORKPLACE_TYPES: dict[str, WorkMode] = {
    "remote": "remote", "hybrid": "hybrid", "onsite": "onsite",
}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


class AshbyBoardSource:
    """Public Ashby job-board API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        terms = [t.lower() for t in role_terms]
        found: list[PostingRef] = []

        for token in self.tokens:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
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
                if job.get("isListed") is False:
                    continue
                title = _str(job.get("title"))
                location = _str(job.get("location"))
                if not any(term in title.lower() for term in terms):
                    continue
                if not matches_region(location, region, self._country(job)):
                    continue
                found.append(PostingRef(
                    company_name=token,
                    company_domain=canonical_domain(f"{token}.com"),
                    title=title,
                    url=_str(job.get("jobUrl")) or url,
                    location=location,
                    work_mode=self._work_mode(job, location, title),
                    employment_type=classify_employment(
                        title, declared=_str(job.get("employmentType")) or None),
                ))
        return found

    @staticmethod
    def _country(job: dict) -> str | None:
        address = job.get("address")
        postal = address.get("postalAddress") if isinstance(address, dict) else None
        country = postal.get("addressCountry") if isinstance(postal, dict) else None
        return country if isinstance(country, str) else None

    @staticmethod
    def _work_mode(job: dict, location: str, title: str) -> WorkMode:
        declared = _WORKPLACE_TYPES.get(_str(job.get("workplaceType")).lower())
        if declared:
            return declared
        if job.get("isRemote") is True:
            return "remote"
        return classify_work_mode(location, title)
