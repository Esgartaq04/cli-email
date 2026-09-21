from __future__ import annotations

from collections import defaultdict
from typing import Sequence
from urllib.parse import urlsplit

from outreach.types import PostingRef


def canonical_domain(value: str) -> str:
    """Reduce any URL or bare domain to a comparable host.

    Domain variants are the main way one company gets researched twice and
    billed twice, so every path into the company table goes through here.
    """
    raw = value.strip().lower()
    if not raw:
        raise ValueError("cannot canonicalize an empty domain")

    if "//" not in raw:
        raw = "//" + raw
    host = urlsplit(raw).hostname or ""
    if not host:
        raise ValueError(f"no host found in {value!r}")

    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def dedupe_postings(postings: Sequence[PostingRef]) -> dict[str, list[PostingRef]]:
    grouped: dict[str, list[PostingRef]] = defaultdict(list)
    for posting in postings:
        grouped[canonical_domain(posting.company_domain)].append(posting)
    return dict(grouped)
