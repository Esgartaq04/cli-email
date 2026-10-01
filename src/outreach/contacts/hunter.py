from __future__ import annotations

import re
from typing import Sequence
from urllib.parse import urlparse

import httpx

from outreach.types import EmailStatus, PersonRef

_BASE_URL = "https://api.hunter.io/v2"

# `profile_url` reaches an `href` in the report. Jinja's autoescape stops
# attribute breakout, but not a `javascript:` (or other non-http) scheme --
# that needs an explicit allowlist, and for LinkedIn the allowlist is the
# host: a Hunter payload (or a scraped source behind it) pointing a "profile"
# at some other site must not become a clickable link. Evidence URLs don't
# need this: they are always built from `canonical_domain`, never taken
# verbatim from a third-party API response.
_SAFE_URL_SCHEMES = ("http://", "https://")
_UNSAFE_URL_CHARS = re.compile(r"[\\\s\x00-\x1f\x7f]")
_LINKEDIN_HANDLE = re.compile(r"^[A-Za-z0-9_-]{3,100}$")


def _safe_linkedin_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(_SAFE_URL_SCHEMES):
        # `urlparse` and browsers disagree about a backslash (browsers read it
        # as `/` in http(s) URLs), so `https://evil.example\@linkedin.com/`
        # parses as host linkedin.com yet navigates to evil.example. Nothing
        # legitimate needs a backslash, whitespace or a control character.
        if _UNSAFE_URL_CHARS.search(value):
            return None
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        # The authority must be exactly the host: no userinfo, no port.
        if parsed.netloc.lower() != host:
            return None
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            # Rebuilt from the validated parts rather than echoed back, so no
            # attacker-shaped authority, query or fragment survives.
            return f"{parsed.scheme}://{host}{parsed.path}"
        return None
    # Hunter sometimes returns just the handle rather than a URL.
    if _LINKEDIN_HANDLE.match(value):
        return f"https://www.linkedin.com/in/{value}"
    return None


class HunterAPIError(Exception):
    """A Hunter request answered with a non-2xx status.

    Hunter authenticates with `api_key` in the query string, and httpx's own
    `HTTPStatusError` message embeds the full request URL -- key included. The
    pipeline writes exception text to the database and the HTML report, so the
    original error must never escape. This one names only the endpoint path and
    the status.
    """


def hunter_get(client: httpx.Client, path: str, api_key: str, *,
               missing_ok: bool = False, **params: str) -> dict | None:
    """GET one Hunter endpoint and return its JSON object.

    `missing_ok` turns HTTP 404 into `None` for endpoints where "no such
    company/person" is an answer rather than a failure. Any other non-2xx
    raises `HunterAPIError`.
    """
    response = client.get(f"{_BASE_URL}/{path}", params={**params, "api_key": api_key})
    if missing_ok and response.status_code == 404:
        return None
    # Checked by hand rather than via `raise_for_status()` inside a try: that
    # exception would stay attached as `__context__` even under `from None`,
    # carrying the URL (and so the key) with it.
    if not response.is_success:
        raise HunterAPIError(f"Hunter {path} returned HTTP {response.status_code}")
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


# Hunter's own verifier statuses, mapped down to the three this pipeline
# ever acts on. "valid" is the only status Hunter itself is confident enough
# in to call deliverable; everything else (accept_all, webmail, unknown,
# risky, disposable, invalid, ...) is either a confirmed non-deliverable
# address or one Hunter could not confirm either way, and this pipeline
# never guesses in that gap -- see `verify`.
_NOT_FOUND_STATUSES = frozenset({"invalid", "disposable"})


class HunterProvider:
    """`find`, `verify` and `remaining_credits` against Hunter's REST API.

    `find` never returns an address as `"verified"` on its own -- domain
    search only tells you an address exists and Hunter's guess at its
    confidence, not that it was confirmed deliverable. Every address `find`
    returns comes back `"unverified"`, so the caller always routes it
    through `verify` (or an already-confirmed prior result) before it is
    ever printed as a real email. That is one of three layers enforcing the
    verified-email rule; this is the layer closest to the data, so it is
    the one that must never shortcut it.
    """

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, **params: str) -> dict:
        data = hunter_get(self._client, path, self._api_key, **params)
        return data if data is not None else {}

    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]:
        data = self._get("domain-search", domain=domain)
        raw = data.get("data", {})
        emails = raw.get("emails", []) if isinstance(raw, dict) else []
        people: list[PersonRef] = []
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if not value:
                continue
            first = entry.get("first_name") or ""
            last = entry.get("last_name") or ""
            full_name = f"{first} {last}".strip() or value
            people.append(PersonRef(
                full_name=full_name,
                title=entry.get("position") or "",
                # Domain search names this field `linkedin`; `linkedin_url` is
                # what the other endpoints call it, kept as a fallback.
                profile_url=_safe_linkedin_url(
                    entry.get("linkedin") or entry.get("linkedin_url")),
                email=value,
                # Never "verified" here -- see class docstring.
                email_status="unverified",
            ))
        return people

    def find_profile(self, domain: str, first_name: str, last_name: str) -> str | None:
        """LinkedIn URL for a named person at `domain`, via Hunter Email Finder."""
        data = hunter_get(self._client, "email-finder", self._api_key, missing_ok=True,
                          domain=domain, first_name=first_name, last_name=last_name)
        raw = data.get("data") if data else None
        return _safe_linkedin_url(raw.get("linkedin_url")) if isinstance(raw, dict) else None

    def verify(self, email: str) -> EmailStatus:
        data = self._get("email-verifier", email=email)
        raw = data.get("data", {})
        status = raw.get("status") if isinstance(raw, dict) else None
        if status == "valid":
            return "verified"
        if status in _NOT_FOUND_STATUSES:
            return "not_found"
        # accept_all, webmail, unknown, risky, or anything unrecognized:
        # Hunter could not confirm deliverability either way. Defaulting to
        # "unverified" rather than "verified" is the safe direction.
        return "unverified"

    def remaining_credits(self) -> int:
        data = self._get("account")
        raw = data.get("data", {})
        requests = raw.get("requests", {}) if isinstance(raw, dict) else {}
        searches = requests.get("searches", {}) if isinstance(requests, dict) else {}
        available = searches.get("available")
        used = searches.get("used")
        if isinstance(available, int) and isinstance(used, int):
            return max(0, available - used)
        return 0
