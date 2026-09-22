import httpx
from outreach.net.fetcher import DEFAULT_USER_AGENT, Fetcher, build_user_agent


def handler_factory(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[request.url.path](request)
    return handler


def build(routes, **kwargs):
    transport = httpx.MockTransport(handler_factory(routes))
    client = httpx.Client(transport=transport)
    sleeps: list[float] = []
    fetcher = Fetcher(client, rate_limit_seconds=2.0, sleep=sleeps.append, **kwargs)
    return fetcher, sleeps


class FakeClock:
    """A controllable clock so throttle tests do not depend on real time."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_successful_fetch_returns_body_and_sends_honest_user_agent():
    seen = {}

    def ok(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="hello")

    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(404),
                        "/page": ok})
    out = fetcher.get("https://acme.example/page")
    assert out.outcome == "ok"
    assert out.body == "hello"
    assert seen["ua"] == DEFAULT_USER_AGENT


def test_robots_disallow_blocks_the_fetch():
    fetcher, _ = build({
        "/robots.txt": lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private"),
        "/private": lambda r: httpx.Response(200, text="secret"),
    })
    out = fetcher.get("https://acme.example/private")
    assert out.outcome == "blocked_by_robots"
    assert out.body is None


def test_http_error_is_reported_not_raised():
    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(404),
                        "/missing": lambda r: httpx.Response(404, text="nope")})
    out = fetcher.get("https://acme.example/missing")
    assert out.outcome == "http_error"
    assert out.status == 404


def test_second_request_to_same_host_sleeps_for_the_rate_limit():
    clock = FakeClock()
    fetcher, sleeps = build({"/robots.txt": lambda r: httpx.Response(404),
                             "/a": lambda r: httpx.Response(200, text="a"),
                             "/b": lambda r: httpx.Response(200, text="b")},
                            now=clock.now)
    fetcher.get("https://acme.example/a")  # primes the robots cache
    sleeps.clear()

    fetcher.get("https://acme.example/b")

    assert sleeps == [2.0]


def test_request_after_the_rate_limit_has_elapsed_does_not_sleep():
    clock = FakeClock()
    fetcher, sleeps = build({"/robots.txt": lambda r: httpx.Response(404),
                             "/a": lambda r: httpx.Response(200, text="a"),
                             "/b": lambda r: httpx.Response(200, text="b")},
                            now=clock.now)
    fetcher.get("https://acme.example/a")  # primes the robots cache
    sleeps.clear()
    clock.advance(3.0)

    fetcher.get("https://acme.example/b")

    assert sleeps == []


def test_robots_txt_server_error_disallows_the_host():
    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(500),
                        "/page": lambda r: httpx.Response(200, text="hello")})
    out = fetcher.get("https://acme.example/page")
    assert out.outcome == "blocked_by_robots"


def test_robots_txt_network_error_disallows_the_host():
    def raise_connect_error(request):
        raise httpx.ConnectError("boom", request=request)

    fetcher, _ = build({"/robots.txt": raise_connect_error,
                        "/page": lambda r: httpx.Response(200, text="hello")})
    out = fetcher.get("https://acme.example/page")
    assert out.outcome == "blocked_by_robots"


def test_malformed_url_returns_network_error_instead_of_raising():
    fetcher, _ = build({})
    out = fetcher.get("https://[::1")
    assert out.outcome == "network_error"
    assert out.body is None


def test_url_httpx_rejects_after_robots_check_returns_network_error_instead_of_raising():
    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(404)})
    out = fetcher.get("https://acme.example/page\x00")
    assert out.outcome == "network_error"
    assert out.body is None


def test_default_user_agent_names_no_contact_address():
    """The address lives in the environment, never in the source."""
    assert "@" not in DEFAULT_USER_AGENT
    assert DEFAULT_USER_AGENT == "outreach-pipeline/0.1"


def test_build_user_agent_appends_a_configured_contact():
    assert build_user_agent("me@example.com") == (
        "outreach-pipeline/0.1 (+contact: me@example.com)")


def test_build_user_agent_omits_an_absent_or_blank_contact():
    assert build_user_agent(None) == DEFAULT_USER_AGENT
    assert build_user_agent("") == DEFAULT_USER_AGENT
    assert build_user_agent("   ") == DEFAULT_USER_AGENT


def test_configured_contact_reaches_the_wire():
    seen = {}

    def ok(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="hi")

    transport = httpx.MockTransport(
        lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else ok(r))
    fetcher = Fetcher(httpx.Client(transport=transport), sleep=lambda s: None,
                      user_agent=build_user_agent("me@example.com"))
    fetcher.get("https://acme.example/page")
    assert seen["ua"] == "outreach-pipeline/0.1 (+contact: me@example.com)"
