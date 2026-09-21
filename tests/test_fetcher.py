import httpx
from outreach.net.fetcher import Fetcher, USER_AGENT


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
    assert seen["ua"] == USER_AGENT


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
    fetcher, sleeps = build({"/robots.txt": lambda r: httpx.Response(404),
                             "/a": lambda r: httpx.Response(200, text="a"),
                             "/b": lambda r: httpx.Response(200, text="b")})
    fetcher.get("https://acme.example/a")
    fetcher.get("https://acme.example/b")
    assert any(s > 0 for s in sleeps)
