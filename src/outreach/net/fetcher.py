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
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            # RFC 9309: robots.txt could not be read at all (network error,
            # or a host too malformed to even request). Assume the whole
            # host is disallowed rather than silently granting full access.
            parser.disallow_all = True
            self._robots[host] = parser
            return parser

        if response.status_code >= 500:
            # RFC 9309: a server error means the rules could not be read.
            # Assume disallow, not allow.
            parser.disallow_all = True
        else:
            # 404 and other 4xx: no robots.txt to honour, treat as
            # permissive. This is deliberate: most small companies have no
            # robots.txt at all, and treating that as "disallow all" would
            # silently return zero documents for them.
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        self._robots[host] = parser
        return parser

    def get(self, url: str) -> FetchOutcome:
        try:
            parts = urlsplit(url)
        except ValueError:
            return FetchOutcome(url, None, None, "network_error")
        host = parts.netloc

        if not self._robots_for(parts.scheme, host).can_fetch(USER_AGENT, url):
            return FetchOutcome(url, None, None, "blocked_by_robots")

        try:
            self._throttle(host)
            response = self.client.get(url, headers={"User-Agent": USER_AGENT},
                                       timeout=20.0, follow_redirects=True)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return FetchOutcome(url, None, None, "network_error")

        if response.status_code >= 400:
            return FetchOutcome(url, response.status_code, None, "http_error")
        return FetchOutcome(url, response.status_code, response.text, "ok")
