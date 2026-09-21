"""Every JobBoardSource implementation satisfies the same contract."""
import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
from outreach.types import PostingRef


def empty_greenhouse():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    return GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ())


@pytest.fixture(params=[lambda: FakeJobBoardSource([]), empty_greenhouse])
def source(request):
    return request.param()


def test_search_returns_a_list_of_posting_refs(source):
    result = source.search(["backend engineer"], region="US")
    assert isinstance(result, list)
    assert all(isinstance(p, PostingRef) for p in result)


def test_search_with_no_terms_does_not_raise(source):
    assert source.search([], region="US") == []
