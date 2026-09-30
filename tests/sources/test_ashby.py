import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.ashby import AshbyBoardSource


def test_ashby_maps_structured_fields(ashby_source):
    [p] = [p for p in ashby_source.search(["backend engineer"], "US") if "Intern" not in p.title]
    assert (p.work_mode, p.employment_type) == ("remote", "full_time")
    assert p.company_domain == "acme.com" and p.url.startswith("https://jobs.ashbyhq.com/")


def test_ashby_intern_is_tagged_other_and_unlisted_is_skipped(ashby_source):
    found = ashby_source.search(["backend engineer"], "US")
    assert {p.employment_type for p in found if "Intern" in p.title} == {"other"}
    assert len(found) == 2   # (a) and (c); (b) is Canada, (d) unlisted


@pytest.mark.parametrize("source_fixture", ["ashby_source", "lever_source"])
def test_remote_outside_the_us_is_still_excluded(source_fixture, request):
    found = request.getfixturevalue(source_fixture).search(["backend engineer"], "US")
    assert found
    assert not any("Canada" in p.location for p in found)


def _source_serving(payload_or_body):
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        if isinstance(payload_or_body, bytes):
            return httpx.Response(200, content=payload_or_body)
        return httpx.Response(200, json=payload_or_body)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AshbyBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))


@pytest.mark.parametrize("payload", [
    b"not json", [], {"jobs": "nope"}, {"jobs": None}, {"jobs": ["x", None, 3]},
    {"jobs": [{"title": "Backend Engineer", "location": 5, "address": "x"}]},
])
def test_ashby_tolerates_malformed_payloads(payload):
    assert _source_serving(payload).search(["backend engineer"], "US") == []


def test_ashby_without_workplace_type_falls_back_to_is_remote_then_text():
    jobs = [
        {"title": "Backend Engineer", "location": "Anywhere", "isRemote": True,
         "address": {"postalAddress": {"addressCountry": "US"}}},
        {"title": "Backend Engineer (Hybrid)", "location": "Austin, TX"},
    ]
    found = _source_serving({"jobs": jobs}).search(["backend engineer"], "US")
    assert [p.work_mode for p in found] == ["remote", "hybrid"]
