import httpx
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource

BOARD = {
    "jobs": [
        {"title": "Senior Backend Engineer, Payments",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "Chicago, IL"},
         "company_name": "Acme"},
        {"title": "Product Designer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
         "location": {"name": "Chicago, IL"},
         "company_name": "Acme"},
        {"title": "Backend Engineer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
         "location": {"name": "London, UK"},
         "company_name": "Acme"},
    ]
}


def build():
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        if "acme" in str(request.url):
            return httpx.Response(200, json=BOARD)
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))


def test_matching_titles_are_returned_and_others_dropped():
    postings = build().search(["backend engineer"], region="US")
    titles = [p.title for p in postings]
    assert "Senior Backend Engineer, Payments" in titles
    assert "Product Designer" not in titles


def test_region_filter_excludes_non_us_locations():
    postings = build().search(["backend engineer"], region="US")
    assert all("London" not in p.location for p in postings)


def test_unknown_token_yields_no_postings_and_does_not_raise():
    def handler(request):
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("ghost",))
    assert source.search(["backend engineer"], region="US") == []
