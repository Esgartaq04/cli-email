"""HunterFactsProvider against Hunter's documented Company Enrichment shape,
via MockTransport -- no live API. Plus the FakeFactsProvider contract the
pipeline tests lean on."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from outreach.contacts.facts import FakeFactsProvider, HunterFactsProvider, parse_band
from outreach.types import CompanyFacts, FundingRound

FIXTURE = Path(__file__).parent.parent / "fixtures" / "hunter_company.json"


def _provider(handler) -> HunterFactsProvider:
    return HunterFactsProvider("key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _serving(payload) -> HunterFactsProvider:
    return _provider(lambda request: httpx.Response(200, json=payload))


def test_hunter_facts_parse_the_documented_shape():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=json.loads(FIXTURE.read_text()))

    f = _provider(handler).company_facts("acme.com")
    assert seen[0].url.path == "/v2/companies/find"
    assert seen[0].url.params["domain"] == "acme.com"
    assert seen[0].url.params["api_key"] == "key"
    assert (f.headcount, f.headcount_band, f.founded_year) == (342, "201-500", 2019)
    assert f.funding_rounds[0] == FundingRound("series_b", date(2026, 3, 4))
    assert f.funding_rounds[1] == FundingRound("seed", date(2020, 1, 10))
    assert f.tags == ("fintech",) and f.source == "hunter"


def test_band_midpoint_is_used_without_an_exact_count():
    f = _serving({"data": {"metrics": {"employees": "51-200"}}}).company_facts("x.com")
    assert f.headcount == 125 and f.headcount_band == "51-200"


def test_open_band_falls_back_to_its_lower_bound():
    f = _serving({"data": {"metrics": {"employees": "10K+"}}}).company_facts("x.com")
    assert f.headcount == 10000


@pytest.mark.parametrize("count", [0, -5, "342", True, None])
def test_a_non_positive_or_non_int_count_is_ignored_for_the_band(count):
    f = _serving({"data": {"metrics": {"employees": "51-200", "employeesCount": count}}}
                 ).company_facts("x.com")
    assert f.headcount == 125


def test_no_size_information_leaves_headcount_unknown():
    f = _serving({"data": {"foundedYear": "soon"}}).company_facts("x.com")
    assert (f.headcount, f.headcount_band, f.founded_year) == (None, None, None)
    assert f.funding_rounds == () and f.tags == ()


def test_unknown_company_is_none_not_an_error():
    provider = _provider(lambda request: httpx.Response(404, json={"errors": []}))
    assert provider.company_facts("nobody.example") is None


def test_other_http_errors_propagate():
    provider = _provider(lambda request: httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        provider.company_facts("acme.com")


def test_malformed_funding_items_are_skipped():
    f = _serving({"data": {"fundingRounds": [5, {"type": "grant"}, {"round": "Series A"}]}}
                 ).company_facts("x.com")
    assert [r.kind for r in f.funding_rounds] == ["series_a"]


def test_funding_kind_and_date_fall_back_across_the_alternate_keys():
    f = _serving({"data": {"fundingRounds": [
        {"name": "Seed", "announced_on": "2021-06-01T00:00:00Z"},
        {"type": 3, "series": "Series C", "date": "not-a-date", "announcedOn": "2024-02-09"},
    ]}}).company_facts("x.com")
    assert f.funding_rounds == (
        FundingRound("seed", date(2021, 6, 1)),
        FundingRound("series_c", date(2024, 2, 9)),
    )


def test_a_round_with_no_parseable_date_is_kept_undated():
    f = _serving({"data": {"fundingRounds": [{"type": "Seed"}]}}).company_facts("x.com")
    assert f.funding_rounds == (FundingRound("seed", None),)


def test_non_dict_payloads_are_tolerated():
    assert _serving([1, 2]).company_facts("x.com").headcount is None
    assert _serving({"data": None}).company_facts("x.com").funding_rounds == ()


def test_parse_band_is_re_exported_from_core():
    assert parse_band("51-200") == (51, 200)


def test_fake_returns_the_given_facts_unknown_domains_none_and_records_calls():
    acme = CompanyFacts(10, None, 2020, (), (), "fake")
    fake = FakeFactsProvider({"acme.com": acme}, fail_on=frozenset({"bad.com"}))
    assert fake.company_facts("acme.com") is acme
    assert fake.company_facts("other.com") is None
    with pytest.raises(RuntimeError):
        fake.company_facts("bad.com")
    assert fake.calls == ["acme.com", "other.com", "bad.com"]
