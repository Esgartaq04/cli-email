import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.hn import FakeHNSource, HNThreadSource, HNUnavailable
from outreach.types import HNThread

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _source(search=None, thread=None, status=200):
    search_body = json.dumps(search) if search is not None else (FIXTURES / "hn_search.json").read_text()
    thread_body = json.dumps(thread) if thread is not None else (FIXTURES / "hn_thread.json").read_text()

    def handler(request):
        path = request.url.path
        if path.endswith("robots.txt"):
            return httpx.Response(404)
        if status != 200:
            return httpx.Response(status)
        if path == "/api/v1/search_by_date":
            return httpx.Response(200, text=search_body)
        if path == "/api/v1/items/49522897":
            return httpx.Response(200, text=thread_body)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HNThreadSource(Fetcher(client, sleep=lambda s: None))


def test_picks_who_is_hiring_not_wants_to_be_hired():
    thread = _source().latest_thread()
    assert thread.item_id == 49522897
    assert thread.title == "Ask HN: Who is hiring? (September 2026)"


def test_keeps_only_top_level_posts_with_text():
    thread = _source().latest_thread()
    ids = [p.item_id for p in thread.posts]
    assert len(thread.posts) == 11
    assert 109 not in ids and 1011 not in ids
    assert ids[0] == 101


def test_posted_at_parses():
    post = _source().latest_thread().posts[0]
    assert post.posted_at == datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    assert "Acme Robotics" in post.html


def test_no_thread_returns_none():
    search = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired? (September 2026)",
                        "created_at_i": 5}]}
    assert _source(search=search).latest_thread() is None


def test_fetch_failure_raises_hn_unavailable():
    with pytest.raises(HNUnavailable) as info:
        _source(status=500).latest_thread()
    assert "author_whoishiring" not in str(info.value)
    assert "500" in str(info.value)


def test_malformed_json_raises_hn_unavailable():
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        return httpx.Response(200, text="not json")
    source = HNThreadSource(Fetcher(httpx.Client(transport=httpx.MockTransport(handler)),
                                    sleep=lambda s: None))
    with pytest.raises(HNUnavailable):
        source.latest_thread()


def test_fake_counts_calls_and_raises():
    thread = HNThread(1, "Ask HN: Who is hiring? (September 2026)", ())
    fake = FakeHNSource(thread)
    assert fake.latest_thread() is thread and fake.calls == 1
    failing = FakeHNSource(error=RuntimeError("down"))
    with pytest.raises(RuntimeError):
        failing.latest_thread()
    assert failing.calls == 1
