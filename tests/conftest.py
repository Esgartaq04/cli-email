"""Fixture-backed job-board sources shared by tests/sources and tests/contract."""
from pathlib import Path

import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.ashby import AshbyBoardSource
from outreach.sources.jobboards.lever import LeverBoardSource

FIXTURES = Path(__file__).parent / "fixtures"


def _fetcher_serving(token: str, fixture: str) -> Fetcher:
    body = (FIXTURES / fixture).read_bytes()

    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        if f"/{token}" in request.url.path:
            return httpx.Response(200, content=body,
                                  headers={"content-type": "application/json"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client, sleep=lambda s: None)


@pytest.fixture
def ashby_source():
    return AshbyBoardSource(_fetcher_serving("acme", "ashby_board.json"), ("acme",))


@pytest.fixture
def lever_source():
    return LeverBoardSource(_fetcher_serving("zeta", "lever_postings.json"), ("zeta",))
