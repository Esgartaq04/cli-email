from datetime import date

from outreach.extraction.pubdate import parse_published_date


def test_reads_json_ld_date_published():
    html = ('<html><head><script type="application/ld+json">'
            '{"@type":"BlogPosting","datePublished":"2026-04-27"}'
            '</script></head><body>hi</body></html>')
    assert parse_published_date(html) == date(2026, 4, 27)


def test_reads_json_ld_timestamp_with_offset():
    html = '<script type="application/ld+json">{"datePublished": "2026-08-03T09:30:00-07:00"}</script>'
    assert parse_published_date(html) == date(2026, 8, 3)


def test_reads_article_published_time_meta():
    html = '<meta property="article:published_time" content="2026-06-11T08:30:00Z">'
    assert parse_published_date(html) == date(2026, 6, 11)


def test_reads_itemprop_date_published_meta():
    html = '<meta itemprop="datePublished" content="2026-01-02">'
    assert parse_published_date(html) == date(2026, 1, 2)


def test_a_single_time_element_is_the_publication_date():
    html = '<article><time datetime="2026-09-15T00:00-07:00">Sep 15</time></article>'
    assert parse_published_date(html) == date(2026, 9, 15)


def test_several_distinct_time_elements_are_ambiguous_so_no_date():
    """A listing page has one <time> per card; picking one would be a guess."""
    html = ('<time datetime="2026-09-15">a</time><time datetime="2026-08-01">b</time>')
    assert parse_published_date(html) is None


def test_the_same_time_value_repeated_is_still_unambiguous():
    html = ('<time datetime="2026-09-15">a</time><time datetime="2026-09-15">b</time>')
    assert parse_published_date(html) == date(2026, 9, 15)


def test_structured_metadata_beats_a_time_element():
    html = ('<meta property="article:published_time" content="2026-03-01">'
            '<time datetime="2026-09-15">updated</time>')
    assert parse_published_date(html) == date(2026, 3, 1)


def test_garbage_and_missing_dates_return_none():
    assert parse_published_date("<html><body>no dates here</body></html>") is None
    assert parse_published_date('<meta property="article:published_time" content="soon">') is None
    assert parse_published_date('<time datetime="">x</time>') is None
    assert parse_published_date("") is None


def test_an_impossible_calendar_date_returns_none():
    assert parse_published_date('<meta itemprop="datePublished" content="2026-13-45">') is None
