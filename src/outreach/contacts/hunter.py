from __future__ import annotations

from typing import Sequence

import httpx

from outreach.types import EmailStatus, PersonRef

_BASE_URL = "https://api.hunter.io/v2"

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
        response = self._client.get(
            f"{_BASE_URL}/{path}", params={**params, "api_key": self._api_key})
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

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
                profile_url=entry.get("linkedin_url") or None,
                email=value,
                # Never "verified" here -- see class docstring.
                email_status="unverified",
            ))
        return people

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
