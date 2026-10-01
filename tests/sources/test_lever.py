import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.lever import LeverBoardSource


def test_lever_maps_workplace_type_and_falls_back_to_text(lever_source):
    modes = sorted(p.work_mode for p in lever_source.search(["backend engineer"], "US"))
    assert modes == ["hybrid", "remote"]


def test_lever_maps_company_url_and_employment(lever_source):
    found = lever_source.search(["backend engineer"], "US")
    assert {p.company_domain for p in found} == {"zeta.com"}
    assert all(p.url.startswith("https://jobs.lever.co/zeta/") for p in found)
    assert {p.employment_type for p in found} == {"full_time"}


def _source_serving(body: bytes):
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        return httpx.Response(200, content=body)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LeverBoardSource(Fetcher(client, sleep=lambda s: None), ("zeta",))


@pytest.mark.parametrize("body", [
    b"not json", b"{}", b'"str"', b'["x", null, 3]',
    b'[{"text": "Backend Engineer", "categories": "oops", "country": 7}]',
])
def test_lever_tolerates_malformed_payloads(body):
    assert _source_serving(body).search(["backend engineer"], "US") == []
