from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Sequence

import httpx

from outreach.config import Config, PathsConfig
from outreach.llm.fake import FakeLLM
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.sources.hn import FakeHNSource
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.emailed import EmailedRepo
from outreach.store.runs import (CompanyInfoRepo, FetchAttemptRepo, HNPostRepo, PostingRepo,
                                 RunContactRepo, RunRepo)
from outreach.types import PostingRef, RunFilters

TODAY = date(2026, 9, 21)

CONFIG = Config(paths=PathsConfig(Path("db"), Path("reports")))


def _transport(routes: Mapping[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(404)
        for prefix, body in routes.items():
            if url.startswith(prefix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def build_context(*, postings: Sequence[PostingRef] = (),
                  routes: Mapping[str, str] | None = None) -> RunContext:
    """A context over a scratch database and fakes. `postings` is what the job
    board returns; the fetcher answers 200 with the text of the first route whose
    URL prefix matches, and 404 for anything else, so nothing reaches the network."""
    conn = connect(Path(tempfile.mkdtemp()) / "t.db")
    return RunContext(
        config=CONFIG, today=TODAY,
        companies=CompanyRepo(conn), contacts=ContactRepo(conn), runs=RunRepo(conn),
        postings=PostingRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        # No thread by default: pipeline tests see no HN companies.
        hn_posts=HNPostRepo(conn), hn=FakeHNSource(),
        company_info=CompanyInfoRepo(conn), run_contacts=RunContactRepo(conn),
        emailed=EmailedRepo(conn),
        fetcher=Fetcher(httpx.Client(transport=_transport(routes or {})),
                        sleep=lambda seconds: None),
        llm=FakeLLM(titles=["backend engineer"]),
        job_board=FakeJobBoardSource(list(postings)),
    )


def start_run(ctx: RunContext, kind: str = "companies", people: str = "hiring",
              filters: RunFilters = RunFilters()) -> int:
    """A run recorded the way the pipeline does it, on the fixed test date."""
    return ctx.runs.start(kind, "", filters, people, datetime(2026, 9, 21, 9))


def company_id(ctx: RunContext, domain: str) -> int:
    """The id a run gave `domain`, looked up rather than upserted so a test
    cannot accidentally create the company it is asserting about."""
    row = ctx.companies.conn.execute(
        "SELECT id FROM companies WHERE canonical_domain = ?", (domain,)).fetchone()
    assert row is not None, f"{domain} was never discovered"
    return int(row["id"])


def hn_thread(item_ids: list[int] | None = None, extra: list[tuple[int, str]] = ()):
    """The recorded "Who is hiring?" fixture as an HNThread, optionally only
    some of its posts (in fixture order) plus extra (item_id, html) posts."""
    from datetime import datetime, timezone

    from outreach.types import HNPost, HNThread
    raw = json.loads((Path(__file__).parent.parent / "fixtures" / "hn_thread.json")
                     .read_text(encoding="utf-8"))
    posts = [HNPost(c["id"], datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")),
                    c["text"])
             for c in raw["children"] if c.get("text")
             and (item_ids is None or c["id"] in item_ids)]
    posts += [HNPost(i, datetime(2026, 9, 1, 17, tzinfo=timezone.utc), html) for i, html in extra]
    return HNThread(raw["id"], raw["title"], tuple(posts))
