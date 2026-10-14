"""Small helpers for the links and file names a report is built from."""
from __future__ import annotations

import re
from urllib.parse import quote_plus


def is_http_url(url: str | None) -> bool:
    """Whether a third-party URL is safe to put in an href.

    Posting URLs come from job-board APIs and profile URLs from Hunter: data,
    not ours. Autoescaping keeps them from breaking out of the attribute but
    not from being `javascript:...`, so anything that does not start with a
    plain web scheme is rendered as text rather than as a link. Checked on
    the raw string, so leading junk a browser might strip also fails.
    """
    return bool(url) and url.lower().startswith(("http://", "https://"))


def linkedin_search_url(full_name: str, company_name: str) -> str:
    """A LinkedIn people search for this person at this company.

    Offered when no profile was found, labelled as a search so a reader never
    mistakes it for a confirmed profile of the right person. It is built when
    the report is rendered and never stored.
    """
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote_plus(f"{full_name} {company_name}"))


def slugify(text: str) -> str:
    """Collapse anything that isn't filesystem-safe (slashes, ampersands,
    commas, whitespace, ...) into single hyphens, so the result is always a
    single path segment rather than an accidental subdirectory."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
