from outreach.sources.surfaces.standard import surface_targets
from outreach.types import SourceClass


def test_targets_cover_every_expected_surface():
    classes = [t.source_class for t in surface_targets("a.example", "acme")]
    assert classes == [
        SourceClass.CAREERS_PAGE, SourceClass.ENG_BLOG, SourceClass.CHANGELOG,
        SourceClass.PRESS, SourceClass.ABOUT, SourceClass.DEV_DOCS, SourceClass.GITHUB,
    ]
    press = surface_targets("a.example", None)[3]
    assert press.alternates == ("https://a.example/news", "https://a.example/newsroom")


def test_dev_docs_has_alternates_and_status_page_is_gone():
    targets = surface_targets("a.example", None)
    docs = next(t for t in targets if t.source_class is SourceClass.DEV_DOCS)
    assert docs.alternates == ("https://a.example/developers", "https://a.example/api")
    assert all(t.source_class is not SourceClass.STATUS_PAGE for t in targets)


def test_github_surface_lists_recent_repos_by_push_time():
    github = surface_targets("a.example", "acme")[-1]
    assert github.url == "https://api.github.com/orgs/acme/repos?sort=pushed&per_page=20"


def test_github_surface_is_omitted_when_no_org_known():
    targets = surface_targets("acme.example", github_org=None)
    assert all(t.source_class is not SourceClass.GITHUB for t in targets)


def test_all_urls_are_absolute_and_https():
    for target in surface_targets("acme.example", github_org="acme"):
        assert target.url.startswith("https://")
        assert all(alt.startswith("https://") for alt in target.alternates)
