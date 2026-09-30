from datetime import date

from outreach.sources.surfaces.github import find_github_org, parse_repos
from tests.conftest import FIXTURES

HOMEPAGE = (FIXTURES / "homepage.html").read_text(encoding="utf-8")
REPOS_JSON = (FIXTURES / "github_repos.json").read_text(encoding="utf-8")


def _repo(name: str, pushed: str) -> str:
    return (f'{{"name": "{name}", "description": "", "html_url": "https://github.com/o/{name}",'
            f' "pushed_at": "{pushed}", "fork": false, "archived": false}}')


def test_find_github_org_skips_reserved_paths():
    assert find_github_org(HOMEPAGE) == "acmehq"


def test_find_github_org_is_none_without_a_github_link():
    assert find_github_org('<a href="https://example.com/acme">x</a>') is None


def test_find_github_org_accepts_a_bare_org_link():
    assert find_github_org('<a href="https://github.com/Acme-Labs/">x</a>') == "acme-labs"


def test_find_github_org_ignores_lookalike_hosts():
    html = ('<a href="https://github.com.evil.example/acme">x</a>'
            '<a href="https://github.com\\@evil.example/acme">y</a>'
            '<a href="https://evil.example/github.com/acme">z</a>')
    assert find_github_org(html) is None


def test_parse_repos_keeps_fresh_non_fork_non_archived():
    repos = parse_repos(REPOS_JSON, date(2026, 9, 30), 180, 5)
    assert [r.name for r in repos] == ["sdk"] and repos[0].pushed_at == date(2026, 9, 20)


def test_parse_repos_maps_fields_and_null_description():
    body = ('[{"name": "a", "description": null, "html_url": "https://github.com/o/a",'
            ' "pushed_at": "2026-09-01T00:00:00Z", "fork": false, "archived": false}]')
    [repo] = parse_repos(body, date(2026, 9, 30), 180, 5)
    assert (repo.description, repo.url) == ("", "https://github.com/o/a")


def test_parse_repos_orders_newest_first_and_caps_at_limit():
    body = "[" + ",".join([_repo("a", "2026-06-01T00:00:00Z"), _repo("b", "2026-09-01T00:00:00Z"),
                           _repo("c", "2026-08-01T00:00:00Z")]) + "]"
    assert [r.name for r in parse_repos(body, date(2026, 9, 30), 180, 2)] == ["b", "c"]


def test_parse_repos_recency_cutoff_is_inclusive():
    body = "[" + _repo("a", "2026-04-03T00:00:00Z") + "]"
    # 2026-09-30 minus 180 days is 2026-04-03.
    assert len(parse_repos(body, date(2026, 9, 30), 180, 5)) == 1
    assert parse_repos(body, date(2026, 10, 1), 180, 5) == []


def test_parse_repos_malformed_json_is_empty():
    assert parse_repos("not json", date(2026, 9, 30), 180, 5) == []
    assert parse_repos('{"message": "Not Found"}', date(2026, 9, 30), 180, 5) == []
