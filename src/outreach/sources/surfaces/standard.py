from __future__ import annotations

from outreach.sources.base import SurfaceTarget
from outreach.types import SourceClass


def surface_targets(domain: str, github_org: str | None) -> list[SurfaceTarget]:
    """The fixed set of public surfaces worth checking for every company."""
    targets = [
        SurfaceTarget(SourceClass.CAREERS_PAGE, f"https://{domain}/careers"),
        SurfaceTarget(SourceClass.ENG_BLOG, f"https://{domain}/blog"),
        SurfaceTarget(SourceClass.CHANGELOG, f"https://{domain}/changelog"),
        SurfaceTarget(SourceClass.STATUS_PAGE, f"https://status.{domain}"),
    ]
    if github_org:
        targets.append(SurfaceTarget(
            SourceClass.GITHUB,
            f"https://api.github.com/orgs/{github_org}/events/public"))
    return targets
