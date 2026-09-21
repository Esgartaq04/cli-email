from __future__ import annotations

import json
import re
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.net.fetcher import Fetcher
from outreach.types import PostingRef

# Explicit, unambiguous markers that a location is in the US. Bare country-
# code-shaped hints (", CA", ", IL", ...) are deliberately NOT used as
# blanket US signals here -- they alias ISO country codes (Ontario "CA",
# Tel Aviv "IL") and a false accept would defeat V1's US-only scoping.
_US_MARKERS = (
    "united states", "usa", "u.s.", "remote - us", "remote (us", "us remote",
)

# Recognized non-US markers. Checked before the trailing state-code check
# and before the US markers, so a non-US city/country name always wins over
# an incidental two-letter suffix that happens to match a US state code.
_NON_US_MARKERS = (
    "uk", "united kingdom", "london", "canada", "ontario", "toronto", "vancouver",
    "israel", "tel aviv", "india", "bengaluru", "bangalore", "germany", "berlin",
    "munich", "ireland", "dublin", "netherlands", "amsterdam", "poland", "warsaw",
    "krakow", "singapore", "australia", "sydney", "melbourne", "brazil",
    "são paulo", "sao paulo", "mexico", "spain", "madrid", "barcelona", "france",
    "paris", "japan", "tokyo", "emea", "apac",
)

_US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})

# A state code only counts when it is the trailing ", XX" of the string
# (e.g. "Chicago, IL"), not a bare substring anywhere in it.
_TRAILING_STATE_CODE = re.compile(r",\s*([a-zA-Z]{2})\s*$")


def _contains_marker(lowered: str, markers: Sequence[str]) -> bool:
    """True if any marker appears in `lowered` at a word boundary.

    Boundary-checked against ASCII letters/digits so short markers (e.g.
    "uk") don't false-match inside an unrelated word (e.g. "Milwaukee").
    """
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", lowered)
        for marker in markers
    )


class GreenhouseBoardSource:
    """Public Greenhouse board API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def _matches_region(self, location: str, region: str) -> bool:
        if region.upper() != "US":
            return True
        if not location:
            # Fail closed: no declared location is not evidence of a US
            # location, and admitting it risks emailing someone outside
            # the region V1 is scoped to.
            return False
        lowered = location.lower()
        if _contains_marker(lowered, _NON_US_MARKERS):
            return False
        if _contains_marker(lowered, _US_MARKERS):
            return True
        match = _TRAILING_STATE_CODE.search(lowered)
        return bool(match and match.group(1).upper() in _US_STATE_CODES)

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
            if not isinstance(payload, dict):
                continue

            jobs = payload.get("jobs", [])
            if not isinstance(jobs, list):
                continue

            for job in jobs:
                if not isinstance(job, dict):
                    continue
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
