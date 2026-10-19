"""Companies Hunter knows, found and checked through its Discover endpoint.

Discover costs no credits, but it is rate-limited (the docs give 5 requests a
second and 50 a minute), so a search is one request per sector x size rather
than one per company. Each request carries one industry and one headcount band
rather than lists of them: a list would merge the matches, and every company
found is meant to carry exactly the sector and size of the request that found it.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterator, Mapping, Protocol, Sequence

import httpx

from outreach.contacts.hunter import hunter_post
from outreach.core.dedupe import canonical_domain
from outreach.core.filters import hunter_location
from outreach.types import Location

# Discover answers with at most 100 companies per request, so a request naming
# more domains than that could not be answered in full.
_DOMAINS_PER_REQUEST = 100


@dataclass(frozen=True)
class CompanyQuery:
    """What a company search is narrowed to; every field is optional.

    `hiring_titles` selects companies with an open role matching any of them.
    """
    sectors: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    location: Location | None = None
    hiring_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class FoundCompany:
    domain: str
    name: str
    sector: str | None = None
    size: str | None = None


class CompanySearch(Protocol):
    def search(self, query: CompanyQuery) -> list[FoundCompany]: ...

    def check(self, domains: Sequence[str], query: CompanyQuery) -> dict[str, FoundCompany | None]:
        """Which of `domains` fit the query's sectors and sizes (nothing else is applied).

        A domain Hunter knows maps to a `FoundCompany` carrying the sector and
        size it matched, or to None when it is known but does not fit. A domain
        Hunter does not know is absent.
        """
        ...


def _sector_and_size(sector: str | None, size: str | None) -> dict:
    body: dict = {}
    if sector:
        body["industry"] = {"include": [sector]}
    if size:
        body["headcount"] = [size]
    return body


def _combinations(query: CompanyQuery) -> list[tuple[str | None, str | None]]:
    """Every (sector, size) to ask about, sectors first; (None, None) when neither is given."""
    return list(product(query.sectors or (None,), query.sizes or (None,)))


def _canonical(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return canonical_domain(value)
    except ValueError:
        return None


def _discovered(payload: dict) -> Iterator[tuple[str, str]]:
    """(domain, name) for each `data[]` entry of a Discover response that has a usable domain."""
    data = payload.get("data")
    for item in data if isinstance(data, list) else ():
        if not isinstance(item, dict):
            continue
        domain = _canonical(item.get("domain"))
        if domain is None:
            continue
        organization = item.get("organization")
        name = organization.strip() if isinstance(organization, str) else ""
        yield domain, name or domain


def _chunks(domains: Sequence[str]) -> Iterator[list[str]]:
    for start in range(0, len(domains), _DOMAINS_PER_REQUEST):
        yield list(domains[start:start + _DOMAINS_PER_REQUEST])


class HunterCompanySearch:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)

    def _discover(self, body: dict) -> Iterator[tuple[str, str]]:
        return _discovered(hunter_post(self._client, "discover", self._api_key, body))

    def search(self, query: CompanyQuery) -> list[FoundCompany]:
        """Companies matching the query, each carrying the sector and size it was found under.

        One request per (sector, size). A company several of them find is kept
        once, as the first one found it.
        """
        shared: dict = {}
        if query.location is not None:
            shared["headquarters_location"] = {"include": [hunter_location(query.location)]}
        if query.hiring_titles:
            shared["job_openings"] = {"job_title": {"include": list(query.hiring_titles)}}
        if not (query.sectors or query.sizes or shared):
            raise ValueError("a company search needs at least one filter")

        found: dict[str, FoundCompany] = {}
        for sector, size in _combinations(query):
            for domain, name in self._discover({**_sector_and_size(sector, size), **shared}):
                found.setdefault(domain, FoundCompany(domain, name, sector, size))
        return list(found.values())

    def check(self, domains: Sequence[str], query: CompanyQuery) -> dict[str, FoundCompany | None]:
        wanted = list(dict.fromkeys(d for d in map(_canonical, domains) if d is not None))

        # First, which domains does Hunter know at all?
        known: dict[str, str] = {}
        for chunk in _chunks(wanted):
            for domain, name in self._discover({"organization": {"domain": chunk}}):
                if domain in chunk:
                    known.setdefault(domain, name)
        known_domains = [d for d in wanted if d in known]

        # Then, of those, which fit? With no sector or size to fit, all of them.
        fits: dict[str, FoundCompany] = {}
        if not (query.sectors or query.sizes):
            fits = {d: FoundCompany(d, known[d]) for d in known_domains}
        else:
            for sector, size in _combinations(query):
                for chunk in _chunks(known_domains):
                    body = {"organization": {"domain": chunk}, **_sector_and_size(sector, size)}
                    for domain, name in self._discover(body):
                        if domain in known:
                            fits.setdefault(domain, FoundCompany(domain, name, sector, size))
        return {d: fits.get(d) for d in known_domains}


def _fits(company: FoundCompany, query: CompanyQuery) -> bool:
    return ((not query.sectors or company.sector in query.sectors)
            and (not query.sizes or company.size in query.sizes))


class FakeCompanySearch:
    """A `CompanySearch` over fixed lists, recording what it was asked.

    `companies` answer a plain search and `hiring` a search with hiring titles;
    both are narrowed by the query's sectors and sizes when it gives them, the
    way Hunter narrows them. `known` is every company `check` can see, by
    domain. `error` makes `search` raise it, as a Hunter outage would.
    """

    def __init__(self, companies: Sequence[FoundCompany] = (), *,
                 hiring: Sequence[FoundCompany] = (),
                 known: Mapping[str, FoundCompany] | None = None,
                 error: Exception | None = None) -> None:
        self._companies = list(companies)
        self._hiring = list(hiring)
        self._known = dict(known or {})
        self._error = error
        self.queries: list[CompanyQuery] = []
        self.checks: list[tuple[tuple[str, ...], CompanyQuery]] = []

    def search(self, query: CompanyQuery) -> list[FoundCompany]:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        pool = self._hiring if query.hiring_titles else self._companies
        return [company for company in pool if _fits(company, query)]

    def check(self, domains: Sequence[str], query: CompanyQuery) -> dict[str, FoundCompany | None]:
        self.checks.append((tuple(domains), query))
        return {domain: (company if _fits(company, query) else None)
                for domain in domains if (company := self._known.get(domain))}
