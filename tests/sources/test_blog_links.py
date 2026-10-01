from outreach.sources.surfaces.blog import find_blog_links


def _page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + "</body></html>"


def test_collects_post_links_under_the_blog_section_in_page_order():
    html = _page("/blog/first-post", "/blog/second-post", "/pricing", "/blog/third-post")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == [
        "https://acme.example/blog/first-post",
        "https://acme.example/blog/second-post",
        "https://acme.example/blog/third-post",
    ]


def test_finds_the_engineering_section_index():
    html = _page("/blog/industry", "/blog/engineering", "/blog/a-post")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.engineering_index == "https://acme.example/blog/engineering"


def test_engineering_index_may_live_under_a_renamed_section():
    """Brex serves /blog from /journal and keeps engineering at /journal/engineering-blog."""
    html = _page("/journal/engineering-blog", "/journal/some-post")
    links = find_blog_links(html, "https://brex.com/blog")
    assert links.engineering_index == "https://brex.com/journal/engineering-blog"
    assert links.posts == ["https://brex.com/journal/some-post"]


def test_category_pagination_and_feed_pages_are_not_posts():
    html = _page("/blog/industry", "/blog/product", "/blog/corporate", "/blog/engineering",
                 "/blog/page/2", "/blog/feed", "/blog/tag/payments", "/blog/real-post")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == ["https://acme.example/blog/real-post"]


def test_absolute_same_site_links_count_and_www_is_the_same_site():
    html = _page("https://www.acme.example/blog/a-post", "https://acme.example/blog/b-post")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == ["https://www.acme.example/blog/a-post",
                           "https://acme.example/blog/b-post"]


def test_other_sites_and_assets_are_ignored():
    html = _page("https://elsewhere.example/blog/a-post", "/blog/feed.xml",
                 "/blog/cover.png", "/blog/notes.md", "mailto:a@b.example")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == []


def test_fragments_and_query_strings_are_dropped_and_links_deduplicated():
    html = _page("/blog/a-post#comments", "/blog/a-post?utm_source=x", "/blog/a-post")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == ["https://acme.example/blog/a-post"]


def test_the_index_page_itself_is_not_a_post():
    html = _page("/blog", "/blog/", "https://acme.example/blog")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == []
    assert links.engineering_index is None


def test_unrelated_sections_are_not_treated_as_a_blog():
    html = _page("/customers/acme", "/docs/getting-started", "/careers/backend")
    links = find_blog_links(html, "https://acme.example/blog")
    assert links.posts == []


def test_no_links_at_all():
    links = find_blog_links("<html><body>nothing</body></html>", "https://acme.example/blog")
    assert links.posts == []
    assert links.engineering_index is None


def test_own_section_only_follows_nothing_outside_the_index_section():
    """A press index's nav can link the eng blog; those posts are not press."""
    html = _page("/blog/engineering", "/blog/x", "/journal/y", "/news/seed-round")
    links = find_blog_links(html, "https://acme.example/news", own_section_only=True)
    assert links.engineering_index is None
    assert links.posts == ["https://acme.example/news/seed-round"]
