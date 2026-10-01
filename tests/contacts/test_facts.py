"""HunterFactsProvider against Hunter's documented Company Enrichment shape,
via MockTransport -- no live API. Plus the FakeFactsProvider contract the
pipeline tests lean on."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from outreach.config import StageConfig
from outreach.contacts.hunter import HunterAPIError
from outreach.contacts.facts import FakeFactsProvider, HunterFactsProvider, parse_band
from outreach.core.stage import classify_stage
from outreach.types import CompanyFacts, FundingRound, Stage

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
    with pytest.raises(HunterAPIError, match="companies/find returned HTTP 429"):
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


@pytest.mark.parametrize("data", [
    {"type": "public"}, {"type": "Public"}, {"ticker": "ACME"},
    {"type": "private", "ticker": "ACME"},
])
def test_a_public_company_carries_an_ipo_round(data):
    """Hunter says it is listed: that is an IPO even with no round history,
    so the classifier puts it in maturity instead of sorting it as a target."""
    f = _serving({"data": {**data, "fundingRounds": [{"type": "Series B", "date": "2016-01-01"}]}}
                 ).company_facts("x.com")
    assert FundingRound("ipo", None) in f.funding_rounds
    assert FundingRound("series_b", date(2016, 1, 1)) in f.funding_rounds


@pytest.mark.parametrize("data", [
    {}, {"type": "private"}, {"type": "education"}, {"ticker": ""}, {"ticker": None},
    {"ticker": 5}, {"type": None},
])
def test_a_private_or_unlisted_company_carries_no_ipo_round(data):
    f = _serving({"data": data}).company_facts("x.com")
    assert all(r.kind != "ipo" for r in f.funding_rounds)


def _rounds_for(label: str):
    return _serving({"data": {"fundingRounds": [{"type": label, "date": "2021-05-01"}]}}
                    ).company_facts("x.com").funding_rounds


@pytest.mark.parametrize("label,kind", [
    ("IPO", "ipo"), ("ipo", "ipo"), ("Post-IPO Equity", "ipo"), ("post_ipo_equity", "ipo"),
    ("Initial Public Offering", "ipo"),
    ("Acquired", "acquired"), ("Acquisition", "acquired"), ("M&A", "acquired"),
    ("Merger", "acquired"), ("Mergers and Acquisitions", "acquired"),
    ("Series B", "series_b"),
])
def test_structured_round_labels_map_to_their_kind(label, kind):
    """Hunter's round label is a category, not prose: "IPO" alone is a
    listing, where the prose parser rightly demands completed wording."""
    assert _rounds_for(label) == (FundingRound(kind, date(2021, 5, 1)),)


@pytest.mark.parametrize("label", ["Planned IPO", "Pre-IPO"])
def test_a_planned_or_pre_ipo_label_is_not_an_ipo(label):
    assert all(r.kind != "ipo" for r in _rounds_for(label))


def test_a_listed_ipo_round_classifies_as_maturity_without_a_ticker():
    f = _serving({"data": {"fundingRounds": [
        {"type": "Series B", "date": "2019-03-01"}, {"type": "IPO", "date": "2021-05-01"},
    ]}}).company_facts("x.com")
    assert classify_stage(f, [], date(2026, 10, 1), StageConfig()).stage is Stage.MATURITY
