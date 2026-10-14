from outreach.render.links import is_http_url, linkedin_search_url, slugify


def test_only_web_links_are_http_urls():
    assert is_http_url("https://x")
    assert is_http_url("HTTP://x.example/a")
    assert not is_http_url("javascript:alert(1)")
    assert not is_http_url(" https://x")  # leading junk a browser might strip also fails
    assert not is_http_url("")
    assert not is_http_url(None)


def test_linkedin_search_is_a_people_search_for_the_name_and_company():
    url = linkedin_search_url("Dana Reyes", "Kestrel Labs")
    assert url.startswith("https://www.linkedin.com/search/results/people/")
    assert url.endswith("keywords=Dana+Reyes+Kestrel+Labs")


def test_linkedin_search_quotes_what_it_is_given():
    assert linkedin_search_url("A&B", "C D").endswith("keywords=A%26B+C+D")


def test_slugify_makes_one_filesystem_safe_segment():
    assert slugify("Backend / Platform Eng") == "backend-platform-eng"
    assert slugify("  Health, Wellness & Fitness!  ") == "health-wellness-fitness"
    assert slugify("///") == ""
