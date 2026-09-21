from outreach.sources.surfaces.standard import surface_targets
from outreach.types import SourceClass


def test_targets_cover_every_expected_surface():
    targets = surface_targets("acme.example", github_org="acme")
    classes = {t.source_class for t in targets}
    assert classes == {
        SourceClass.CAREERS_PAGE, SourceClass.ENG_BLOG, SourceClass.CHANGELOG,
        SourceClass.GITHUB, SourceClass.STATUS_PAGE,
    }


def test_github_surface_is_omitted_when_no_org_known():
    targets = surface_targets("acme.example", github_org=None)
    assert all(t.source_class is not SourceClass.GITHUB for t in targets)


def test_all_urls_are_absolute_and_https():
    for target in surface_targets("acme.example", github_org="acme"):
        assert target.url.startswith("https://")
