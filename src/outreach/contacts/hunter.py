from __future__ import annotations

import re
from datetime import date
from typing import Sequence
from urllib.parse import urlparse

import httpx

from outreach.credits import tenths
from outreach.types import EmailStatus, FoundPerson

_BASE_URL = "https://api.hunter.io/v2"

# `profile_url` reaches an `href` in the report. Jinja's autoescape stops
# attribute breakout, but not a `javascript:` (or other non-http) scheme --
# that needs an explicit allowlist, and for LinkedIn the allowlist is the
# host: a Hunter payload (or a scraped source behind it) pointing a "profile"
# at some other site must not become a clickable link.
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


def _checked_on(value: object) -> date | None:
    """The day of a Hunter verification stamp: "2019-12-06", or a timestamp starting with one."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _found_person(entry: object) -> FoundPerson | None:
    """One `data.emails[]` entry of a Domain Search; None when it has no address."""
    if not isinstance(entry, dict):
        return None
    email = entry.get("value")
    if not isinstance(email, str) or not email.strip():
        return None
    email = email.strip()
    name = " ".join(part.strip() for part in (entry.get("first_name"), entry.get("last_name"))
                    if isinstance(part, str) and part.strip())
    position = entry.get("position")
    verification = entry.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    status = verification.get("status")
    return FoundPerson(
        full_name=name or email,
        title=position.strip() if isinstance(position, str) else "",
        email=email,
        profile_url=_safe_linkedin_url(entry.get("linkedin") or entry.get("linkedin_url")),
        hunter_status=status if isinstance(status, str) and status else None,
        hunter_checked_on=_checked_on(verification.get("date")),
    )


def _credit_counter(payload: dict) -> tuple[int, int] | None:
    """(available, used) in tenths from `data.requests.credits`, or None if unreadable."""
    node: object = payload
    for key in ("data", "requests", "credits"):
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return None
    try:
        return tenths(node.get("available")), tenths(node.get("used"))
    except ValueError:
        return None


# Hunter's own verifier statuses, mapped down to the three this pipeline
# ever acts on. "valid" is the only status Hunter itself is confident enough
# in to call deliverable; everything else (accept_all, webmail, unknown,
# risky, disposable, invalid, ...) is either a confirmed non-deliverable
# address or one Hunter could not confirm either way, and this pipeline
# never guesses in that gap -- see `verify`.
_NOT_FOUND_STATUSES = frozenset({"invalid", "disposable"})


class HunterProvider:
    """Finds people through Hunter's Domain Search and checks addresses through
    its Email Verifier.

    `verify` says `"verified"` only when Hunter itself calls the address
    deliverable; anything it could not confirm is `"unverified"`. That is one
    of three layers enforcing the verified-email rule; this is the layer
    closest to the data, so it is the one that must never shortcut it.
    """

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, **params: str) -> dict:
        data = hunter_get(self._client, path, self._api_key, **params)
        return data if data is not None else {}

    def count_people(self, domain: str) -> int:
        """Personal addresses Hunter holds for the company. Free."""
        data = self._get("email-count", domain=domain).get("data")
        count = data.get("personal_emails") if isinstance(data, dict) else None
        if isinstance(count, int) and not isinstance(count, bool):
            return max(count, 0)
        return 0

    def search_people(self, domain: str, titles: Sequence[str], limit: int) -> list[FoundPerson]:
        """Up to `limit` personal addresses whose job title matches any of `titles`.

        Hunter charges for each address it returns, so this never asks for
        nothing (no titles would be an unfiltered search) and never keeps more
        than `limit`.
        """
        if limit <= 0 or not titles:
            return []
        data = self._get("domain-search", domain=domain, type="personal",
                         job_titles=",".join(titles), limit=str(limit)).get("data")
        entries = data.get("emails") if isinstance(data, dict) else None
        people = [person for person in map(_found_person, entries if isinstance(entries, list) else ())
                  if person is not None]
        return people[:limit]

    def balance_tenths(self) -> int:
        """Credits left, in tenths. A balance that cannot be read is an error, so
        that a run buys nothing rather than spending blind."""
        counter = _credit_counter(self._get("account"))
        if counter is None:
            raise HunterAPIError("Hunter account response has no credit balance")
        available, used = counter
        return max(0, available - used)

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
