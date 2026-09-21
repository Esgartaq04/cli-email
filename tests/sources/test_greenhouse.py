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


def _handler_for(payload):
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        if "acme" in str(request.url):
            return httpx.Response(200, json=payload)
        return httpx.Response(404)
    return handler


def test_non_list_jobs_value_does_not_raise():
    client = httpx.Client(transport=httpx.MockTransport(_handler_for({"jobs": "not-a-list"})))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))
    assert source.search(["backend engineer"], region="US") == []


def test_null_jobs_value_does_not_raise():
    client = httpx.Client(transport=httpx.MockTransport(_handler_for({"jobs": None})))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))
    assert source.search(["backend engineer"], region="US") == []


def test_malformed_job_entries_are_skipped_without_raising():
    client = httpx.Client(
        transport=httpx.MockTransport(_handler_for({"jobs": ["a string", None]})))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))
    assert source.search(["backend engineer"], region="US") == []


def test_matches_region_accepts_us_state_code_suffix():
    source = GreenhouseBoardSource(fetcher=None, tokens=())
    assert source._matches_region("Chicago, IL", "US") is True
    assert source._matches_region("San Francisco, CA", "US") is True


def test_matches_region_rejects_non_us_city_sharing_a_state_code_suffix():
    """", IL" and ", CA" are also ISO country codes (Israel, Canada) --
    a non-US city name must win over an incidental trailing state code."""
    source = GreenhouseBoardSource(fetcher=None, tokens=())
    assert source._matches_region("Tel Aviv, IL", "US") is False
    assert source._matches_region("Toronto, CA", "US") is False


def test_matches_region_rejects_explicit_non_us_country():
    source = GreenhouseBoardSource(fetcher=None, tokens=())
    assert source._matches_region("London, UK", "US") is False


def test_matches_region_rejects_empty_location():
    source = GreenhouseBoardSource(fetcher=None, tokens=())
    assert source._matches_region("", "US") is False


def test_job_with_missing_location_key_is_excluded():
    payload = {"jobs": [
        {"title": "Backend Engineer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/9",
         "company_name": "Acme"},
    ]}
    client = httpx.Client(transport=httpx.MockTransport(_handler_for(payload)))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))
    assert source.search(["backend engineer"], region="US") == []


def test_job_with_none_location_is_excluded():
    payload = {"jobs": [
        {"title": "Backend Engineer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/9",
         "location": None,
         "company_name": "Acme"},
    ]}
    client = httpx.Client(transport=httpx.MockTransport(_handler_for(payload)))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))
    assert source.search(["backend engineer"], region="US") == []
