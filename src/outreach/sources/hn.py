"""HN's monthly "Ask HN: Who is hiring?" thread, through the Algolia HN API.

Two requests per run: find the newest thread, then fetch its whole comment
tree. The API is free and keyless; its robots.txt is a 404, which the
Fetcher treats as permissive.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from outreach.net.fetcher import Fetcher
from outreach.types import HNPost, HNThread

_API = "https://hn.algolia.com/api/v1"
_SEARCH_PATH = "/search_by_date"
_SEARCH_QUERY = "?tags=story,author_whoishiring&hitsPerPage=10"
# The same account also posts "Who wants to be hired?" and "Freelancer?"
# threads at the same moment; only this one lists companies.
THREAD_PREFIX = "Ask HN: Who is hiring?"


class HNUnavailable(RuntimeError):
    """The thread could not be read. Names the endpoint and outcome only."""


class HNSource(Protocol):
    def latest_thread(self) -> HNThread | None: ...


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HNThreadSource:
    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def _get_json(self, path: str, query: str = "") -> object:
        outcome = self.fetcher.get(f"{_API}{path}{query}")
        if outcome.outcome != "ok" or not outcome.body:
            raise HNUnavailable(
                f"HN {path} returned {outcome.outcome} {outcome.status or ''}".rstrip())
        try:
            return json.loads(outcome.body)
        except json.JSONDecodeError:
            raise HNUnavailable(f"HN {path} returned malformed JSON") from None

    def latest_thread(self) -> HNThread | None:
        search = self._get_json(_SEARCH_PATH, _SEARCH_QUERY)
        hits = search.get("hits", []) if isinstance(search, dict) else []
        stories = [h for h in hits if isinstance(h, dict)
                   and str(h.get("title", "")).startswith(THREAD_PREFIX)]
        if not stories:
            return None
        newest = max(stories, key=lambda h: h.get("created_at_i") or 0)
        try:
            item_id = int(newest["objectID"])
        except (KeyError, TypeError, ValueError):
            raise HNUnavailable("HN search returned a story without an id") from None

        tree = self._get_json(f"/items/{item_id}")
        if not isinstance(tree, dict):
            raise HNUnavailable("HN item returned malformed JSON")
        posts: list[HNPost] = []
        for child in tree.get("children") or []:
            # Only top-level comments are job posts; replies are questions.
            # Deleted and dead posts come back with no text.
            if not isinstance(child, dict) or not child.get("text"):
                continue
            try:
                posts.append(HNPost(int(child["id"]), _parse_time(child["created_at"]),
                                    str(child["text"])))
            except (KeyError, TypeError, ValueError):
                continue
        return HNThread(item_id, str(newest.get("title")), tuple(posts))


class FakeHNSource:
    def __init__(self, thread: HNThread | None = None,
                 error: Exception | None = None) -> None:
        self._thread = thread
        self._error = error
        self.calls = 0

    def latest_thread(self) -> HNThread | None:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._thread
