from __future__ import annotations

from datetime import date
from typing import Protocol

import httpx

from outreach.contacts.hunter import hunter_get
from outreach.core.stage import parse_band, parse_round
from outreach.types import CompanyFacts, FundingRound

# Re-exported so callers that already hold a facts provider import band parsing
# from one place; the logic itself stays in core/, which must not import from
# contacts/.
__all__ = ["CompanyFactsProvider", "FakeFactsProvider", "HunterFactsProvider", "parse_band"]

# Hunter's payloads are not consistent about naming the round or its date, so
# each is read from the first key that yields a usable value.
_KIND_KEYS = ("type", "round", "series", "name")
_DATE_KEYS = ("date", "announcedOn", "announced_on", "announced_at")


class CompanyFactsProvider(Protocol):
    def company_facts(self, domain: str) -> CompanyFacts | None: ...


def _positive_int(value: object) -> int | None:
    # bool is an int subclass; `True` is not a headcount.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _headcount(metrics: dict, band: str | None) -> int | None:
    exact = _positive_int(metrics.get("employeesCount"))
    if exact is not None:
        return exact
    parsed = parse_band(band)
    if parsed is None:
        return None
    low, high = parsed
    # An open band ("10K+") has no upper bound to average with; its lower bound
    # is the only honest number.
    return low if high is None else (low + high) // 2


def _first_date(item: dict) -> date | None:
    for key in _DATE_KEYS:
        value = item.get(key)
        if not isinstance(value, str):
            continue
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            continue
    return None


def _funding_round(item: object) -> FundingRound | None:
    if not isinstance(item, dict):
        return None
    text = next((item[k] for k in _KIND_KEYS if isinstance(item.get(k), str)), None)
    if text is None:
        return None
    announced = _first_date(item)
    # `parse_round` reads prose, where a bare "seed" is too ambiguous to count.
    # Here the value is a structured round label ("Seed"), so retry it in
    # round context before giving up.
    return parse_round(text, announced) or parse_round(f"{text} round", announced)


def _is_public(data: dict) -> bool:
    """Hunter marks a listed company by `type: "public"` or by its ticker."""
    kind, ticker = data.get("type"), data.get("ticker")
    return ((isinstance(kind, str) and kind.strip().lower() == "public")
            or (isinstance(ticker, str) and bool(ticker.strip())))


class HunterFactsProvider:
    """Headcount, founding year and funding rounds from Hunter Company Enrichment."""

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)

    def company_facts(self, domain: str) -> CompanyFacts | None:
        # An unknown company is an answer, not a failure (404 -> None).
        payload = hunter_get(self._client, "companies/find", self._api_key,
                             missing_ok=True, domain=domain)
        if payload is None:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}

        metrics = data.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        band = metrics.get("employees")
        band = band if isinstance(band, str) and band else None

        founded = data.get("foundedYear")
        founded_year = founded if isinstance(founded, int) and not isinstance(founded, bool) else None

        raw_rounds = data.get("fundingRounds")
        rounds = tuple(
            r for r in map(_funding_round, raw_rounds if isinstance(raw_rounds, list) else [])
            if r is not None)
        if _is_public(data):
            # A listed company's round history rarely records the listing
            # itself; without this its last Series B would sort it as a
            # growth-stage target.
            rounds += (FundingRound("ipo", None),)
        raw_tags = data.get("tags")
        tags = tuple(t for t in (raw_tags if isinstance(raw_tags, list) else [])
                     if isinstance(t, str))

        return CompanyFacts(
            headcount=_headcount(metrics, band),
            headcount_band=band,
            founded_year=founded_year,
            funding_rounds=rounds,
            tags=tags,
            source="hunter",
        )


class FakeFactsProvider:
    """Offline stand-in: returns exactly the facts it was given per domain."""

    def __init__(self, facts_by_domain: dict[str, CompanyFacts],
                 fail_on: frozenset[str] = frozenset()) -> None:
        self._facts = facts_by_domain
        self._fail_on = fail_on
        # Every domain asked, including failing ones, so tests can assert how
        # many lookups (and so how many credits) a run would have spent.
        self.calls: list[str] = []

    def company_facts(self, domain: str) -> CompanyFacts | None:
        self.calls.append(domain)
        if domain in self._fail_on:
            raise RuntimeError(f"facts lookup failed for {domain}")
        return self._facts.get(domain)
