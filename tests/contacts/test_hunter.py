"""HunterProvider against recorded Hunter response shapes, via MockTransport.

`HunterProvider` is the layer closest to the data for the verified-email
rule, and `verify()`'s status mapping is the one place a single mis-mapped
branch (e.g. `accept_all` -> "verified") would ship guessed addresses as
verified with nothing to catch it. Injectable client, no live API touched --
same pattern as `tests/test_fetcher.py`.
"""
from __future__ import annotations

import httpx
import pytest

from outreach.contacts.hunter import HunterAPIError, HunterProvider, _safe_linkedin_url


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _verifier(status: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/email-verifier"
        return httpx.Response(200, json={"data": {"status": status}})
    return handler


def test_verify_maps_valid_to_verified():
    provider = HunterProvider("key", client=_client(_verifier("valid")))
    assert provider.verify("m@acme.example") == "verified"


def test_verify_maps_accept_all_to_unverified():
    """The load-bearing branch: `accept_all` is common at small companies
    and must NOT map to "verified" -- Hunter did not confirm this specific
    address, only that the domain accepts anything. A one-character edit
    here (`accept_all` treated as valid) would ship guessed addresses as
    verified, and this is the test that must catch it."""
    provider = HunterProvider("key", client=_client(_verifier("accept_all")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_invalid_to_not_found():
    provider = HunterProvider("key", client=_client(_verifier("invalid")))
    assert provider.verify("m@acme.example") == "not_found"


def test_verify_maps_disposable_to_not_found():
    provider = HunterProvider("key", client=_client(_verifier("disposable")))
    assert provider.verify("m@acme.example") == "not_found"


def test_verify_maps_unknown_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("unknown")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_webmail_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("webmail")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_risky_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("risky")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_an_unrecognized_status_to_unverified():
    """Forward-compatible default: anything Hunter adds later that this
    pipeline doesn't recognize must fail closed, not open."""
    provider = HunterProvider("key", client=_client(_verifier("some_new_status")))
    assert provider.verify("m@acme.example") == "unverified"


@pytest.mark.parametrize("value,expected", [
    ("marisol-okonkwo", "https://www.linkedin.com/in/marisol-okonkwo"),
    ("https://evil.example/in/m", None),
    ("https://notlinkedin.com/in/m", None),
    ("https://linkedin.com.evil.example/in/m", None),
    ("javascript:alert(1)", None),
    ("https://uk.linkedin.com/in/m", "https://uk.linkedin.com/in/m"),
    ("https://linkedin.com/in/m", "https://linkedin.com/in/m"),
    ("https://evil.example\\@linkedin.com/in/x", None),
    ("https://evil.example\\@www.linkedin.com/in/x", None),
    ("https://x@linkedin.com/in/m", None),
    ("https://user:pw@www.linkedin.com/in/m", None),
    ("https://linkedin.com:8443/in/m", None),
    ("https://linkedin.com/in/m x", None),
    ("https://linkedin.com/in/\tm", None),
    ("https://www.linkedin.com/in/m?utm=1#frag", "https://www.linkedin.com/in/m"),
    ("ab", None),
    ("has space", None),
    (None, None),
    (42, None),
])
def test_linkedin_handle_is_expanded_and_foreign_hosts_rejected(value, expected):
    assert _safe_linkedin_url(value) == expected


SECRET = "sk-hunter-SECRET-key"


@pytest.mark.parametrize("call", [
    lambda c: HunterProvider(SECRET, client=c).verify("m@acme.com"),
], ids=["verify"])
def test_http_errors_never_carry_the_api_key(call):
    """The key rides in the query string, and httpx's own HTTPStatusError
    message embeds the full URL. The pipeline persists exception text, so the
    adapter must replace it with one naming only status and endpoint."""
    with pytest.raises(HunterAPIError) as caught:
        call(_client(lambda request: httpx.Response(500)))
    exc = caught.value
    assert SECRET not in str(exc) and SECRET not in repr(exc)
    assert "api_key" not in str(exc) and "?" not in str(exc)
    assert "HTTP 500" in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None
