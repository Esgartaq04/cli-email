from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

# First path segments that mean "this site's blog". Sites rename theirs
# (Brex serves /blog from /journal), so the segment of the index URL we were
# given is added to this set at call time.
_BLOG_SECTIONS = frozenset({"blog", "journal", "engineering", "articles", "insights"})

# Second-level slugs that are listing or navigation pages inside a blog, not
# posts. Fetching them would spend the post budget on index pages.
_NOT_A_POST = frozenset({
    "feed", "rss", "page", "category", "categories", "tag", "tags", "author",
    "authors", "search", "industry", "product", "products", "corporate",
    "company", "news", "engineering", "engineering-blog", "articles",
})

_ASSET_SUFFIXES = (".xml", ".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                   ".pdf", ".css", ".js", ".ico", ".json", ".rss", ".atom")


@dataclass(frozen=True)
class BlogLinks:
    engineering_index: str | None
    posts: list[str]


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.hrefs.append(value)


def _site(host: str) -> str:
    return host.lower().removeprefix("www.")


def find_blog_links(html: str, base_url: str) -> BlogLinks:
    """Post links (in page order) and the engineering index, if the page links one.

    A blog's index mixes marketing with engineering writing; the engineering
    section, when there is one, is where the pain points are. Only same-site
    links under a blog-shaped section qualify, so a nav or footer link never
    costs a fetch.
    """
    base = urlsplit(base_url)
    own_section = [s.lower() for s in base.path.split("/") if s][:1]
    sections = _BLOG_SECTIONS | set(own_section)
    parser = _Anchors()
    parser.feed(html)
    parser.close()

    posts: list[str] = []
    engineering_index: str | None = None
    seen: set[str] = set()

    for href in parser.hrefs:
        parts = urlsplit(urljoin(base_url, href))
        if parts.scheme not in ("http", "https") or _site(parts.netloc) != _site(base.netloc):
            continue
        path = parts.path.rstrip("/")
        if path.lower().endswith(_ASSET_SUFFIXES):
            continue
        segments = [s for s in path.split("/") if s]
        if len(segments) < 2 or segments[0].lower() not in sections:
            continue

        url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        if url in seen:
            continue
        seen.add(url)

        second = segments[1].lower()
        if len(segments) == 2 and "engineering" in second and engineering_index is None:
            engineering_index = url
            continue
        if second in _NOT_A_POST:
            continue
        posts.append(url)

    return BlogLinks(engineering_index=engineering_index, posts=posts)
