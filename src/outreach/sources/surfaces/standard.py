from __future__ import annotations

from outreach.sources.base import SurfaceTarget
from outreach.types import SourceClass


def surface_targets(domain: str, github_org: str | None) -> list[SurfaceTarget]:
    """The fixed set of public surfaces worth checking for every company.

    Press, about and dev docs are where an early-stage company says what it is
    building and who backs it; a status page says nothing about either, so it
    is not fetched. Alternates are tried only when the primary path is absent.
    """
    targets = [
        SurfaceTarget(SourceClass.CAREERS_PAGE, f"https://{domain}/careers"),
        SurfaceTarget(SourceClass.ENG_BLOG, f"https://{domain}/blog"),
        SurfaceTarget(SourceClass.CHANGELOG, f"https://{domain}/changelog"),
        SurfaceTarget(SourceClass.PRESS, f"https://{domain}/press",
                      (f"https://{domain}/news", f"https://{domain}/newsroom")),
        SurfaceTarget(SourceClass.ABOUT, f"https://{domain}/about"),
        SurfaceTarget(SourceClass.DEV_DOCS, f"https://{domain}/docs",
                      (f"https://{domain}/developers", f"https://{domain}/api")),
    ]
    if github_org:
        # Recently pushed repos show what is being built now; the public events
        # feed is noisy (stars, forks) and says little about it.
        targets.append(SurfaceTarget(
            SourceClass.GITHUB,
            f"https://api.github.com/orgs/{github_org}/repos?sort=pushed&per_page=20"))
    return targets
