from datetime import date

from outreach.extraction.changelog import split_changelog
from tests.conftest import FIXTURES

FIXTURE = (FIXTURES / "changelog.html").read_text(encoding="utf-8")


def test_changelog_splits_on_dated_headings_newest_first():
    entries = split_changelog(FIXTURE)
    assert [e.published_at for e in entries] == [date(2026, 9, 12), date(2026, 8, 1), date(2024, 3, 12)]
    assert "Webhooks v2" in entries[0].text and "Webhooks v2" not in entries[1].text


def test_changelog_footer_dates_are_not_entries():
    assert all(e.published_at.year != 2019 for e in split_changelog(FIXTURE))


def test_a_changelog_without_dated_headings_has_no_entries():
    assert split_changelog("<h2>Improvements</h2><p>Faster.</p>") == []


def test_undated_intro_is_not_part_of_any_entry():
    assert all("Subscribe by RSS" not in e.text for e in split_changelog(FIXTURE))


def test_undated_subheading_stays_in_the_entry_body():
    entries = split_changelog(FIXTURE)
    assert "timezone bug" in entries[1].text and "Fixes" in entries[1].text


def test_time_element_inside_the_heading_supplies_the_date():
    html = '<h2><time datetime="2026-08-01">August 1</time></h2><p>body</p>'
    # "August 1" alone has no year; the <time datetime> is the only source.
    assert [e.published_at for e in split_changelog(html)] == [date(2026, 8, 1)]


def test_entries_are_sorted_newest_first_even_when_the_page_is_not():
    html = "<h2>2024-01-05</h2><p>old</p><h2>2026-02-03</h2><p>new</p>"
    assert [e.published_at for e in split_changelog(html)] == [date(2026, 2, 3), date(2024, 1, 5)]


def test_every_accepted_date_format_parses():
    headings = ["2026-09-12", "September 12, 2026", "Sep 12, 2026", "12 Sep 2026", "12 September 2026"]
    for h in headings:
        entries = split_changelog(f"<h3>{h}</h3><p>body</p>")
        assert [e.published_at for e in entries] == [date(2026, 9, 12)], h


def test_an_impossible_date_is_not_an_entry():
    assert split_changelog("<h2>2026-02-31</h2><p>body</p>") == []


def test_a_word_that_merely_starts_like_a_month_is_not_a_date():
    assert split_changelog("<h2>12 Marketing 2026</h2><p>body</p>") == []
