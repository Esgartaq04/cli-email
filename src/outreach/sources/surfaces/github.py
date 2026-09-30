from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

# First path segments on github.com that are site pages, not organizations.
_RESERVED = frozenset({
    "features", "about", "pricing", "login", "join", "sponsors", "orgs",
    "topics", "marketplace", "enterprise", "site", "security",
})
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
# The org is interpolated into an API URL, so accept only what GitHub allows in a login.
_ORG = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


@dataclass(frozen=True)
class Repo:
    name: str
    description: str
    url: str
    pushed_at: date


def _org_from_href(href: str) -> str | None:
    parts = urlsplit(href.strip())
    # Compare the raw netloc, not .hostname: a userinfo trick such as
    # "https://github.com\@evil.example/x" must not pass as github.com.
    if parts.scheme not in ("http", "https") or parts.netloc.lower() not in _GITHUB_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if not 1 <= len(segments) <= 2:
        return None
    org = segments[0].lower()
    if org in _RESERVED or not _ORG.fullmatch(org):
        return None
    return org


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def find_github_org(html: str) -> str | None:
    """The GitHub org a page links to (lowercased), or None.

    Homepages link to the org from the footer or nav, so the first link that
    names an org wins. GitHub's own marketing paths (`/features`, `/about`, ...)
    are skipped: they are links to GitHub, not to the company's account.
    """
    collector = _HrefCollector()
    collector.feed(html)
    collector.close()
    return next((org for org in map(_org_from_href, collector.hrefs) if org), None)


def parse_repos(body: str, today: date, recency_days: int, limit: int) -> list[Repo]:
    """Recently active, original repos from `GET /orgs/{org}/repos`, newest push first.

    Forks and archived repos say nothing about what the company is building now,
    and a repo untouched for `recency_days` is not current work. Malformed
    bodies (including GitHub's `{"message": ...}` error object) yield no repos.
    """
    try:
        raw = json.loads(body)
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []

    cutoff = today - timedelta(days=recency_days)
    repos: list[Repo] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("fork") or item.get("archived"):
            continue
        name, url, pushed = item.get("name"), item.get("html_url"), item.get("pushed_at")
        if not (isinstance(name, str) and isinstance(url, str) and isinstance(pushed, str)):
            continue
        try:
            pushed_at = date.fromisoformat(pushed[:10])
        except ValueError:
            continue
        if pushed_at < cutoff:
            continue
        repos.append(Repo(name, item.get("description") or "", url, pushed_at))
    repos.sort(key=lambda r: r.pushed_at, reverse=True)
    return repos[:limit]
