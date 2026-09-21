from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

USER_AGENT = "outreach-pipeline/0.1 (+contact: esgartaq@gmail.com)"


@dataclass(frozen=True)
class FetchOutcome:
    url: str
    status: int | None
    body: str | None
    outcome: str


class Fetcher:
    """One polite request at a time, per host.

    These are companies the user intends to email, so an honest user agent,
    robots.txt and a real rate limit are requirements, not niceties.
    """

    def __init__(
        self,
        client: httpx.Client,
        rate_limit_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.rate_limit_seconds = rate_limit_seconds
        self.sleep = sleep
        self.now = now
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.rate_limit_seconds - (self.now() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[host] = self.now()

    def _robots_for(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        parser = urllib.robotparser.RobotFileParser()
        try:
            self._throttle(host)
            response = self.client.get(f"{scheme}://{host}/robots.txt",
                                       headers={"User-Agent": USER_AGENT}, timeout=10.0)
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        except httpx.HTTPError:
            parser.parse([])
        self._robots[host] = parser
        return parser

    def get(self, url: str) -> FetchOutcome:
        parts = urlsplit(url)
        host = parts.netloc
        if not self._robots_for(parts.scheme, host).can_fetch(USER_AGENT, url):
            return FetchOutcome(url, None, None, "blocked_by_robots")

        try:
            self._throttle(host)
            response = self.client.get(url, headers={"User-Agent": USER_AGENT},
                                       timeout=20.0, follow_redirects=True)
        except httpx.HTTPError:
            return FetchOutcome(url, None, None, "network_error")

        if response.status_code >= 400:
            return FetchOutcome(url, response.status_code, None, "http_error")
        return FetchOutcome(url, response.status_code, response.text, "ok")
