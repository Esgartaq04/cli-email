from outreach.types import SourceClass, Contact, PostingRef, Company


def test_source_class_values_are_stable_strings():
    assert SourceClass.JOB_POSTING.value == "job_posting"
    assert SourceClass.NEWS.value == "news"


def test_unverified_contact_has_no_email():
    c = Contact(
        id=None, company_id=7, full_name="Deepak Raman",
        title="Engineering Lead, Payments", profile_url="https://example.com/p/1",
        email=None, email_status="unverified", provider="hunter",
        looked_up_at=None,
    )
    assert c.email is None
    assert c.email_status == "unverified"


def test_posting_ref_defaults_to_unknown_mode_and_type():
    p = PostingRef("Co", "co.example", "Engineer", "u", "Remote")
    assert p.work_mode == "unknown" and p.employment_type == "unknown"


def test_company_positional_construction_still_works():
    c = Company(1, "co.example", "Co", None, None)
    assert c.headcount_band is None


def test_hn_post_source_class_value():
    from outreach.types import SourceClass
    assert SourceClass("hn_post") is SourceClass.HN_POST


def test_contact_no_longer_carries_a_contacted_mark():
    # The mark moved to the emailed list, keyed by address.
    import dataclasses
    assert "contacted_at" not in {f.name for f in dataclasses.fields(Contact)}


def test_run_filters_default_to_no_filters_in_the_us():
    from outreach.types import Location, RunFilters
    filters = RunFilters()
    assert (filters.sectors, filters.sizes, filters.work_modes) == ((), (), ())
    assert filters.location == Location()


def test_company_info_defaults_to_a_fitting_company_with_a_real_domain():
    from outreach.types import CompanyInfo
    info = CompanyInfo(0, "hunter")
    assert (info.sector, info.size, info.fit, info.guessed_domain) == (None, None, "fits", False)


def test_spend_defaults_to_nothing_spent():
    from outreach.types import Spend
    assert Spend() == Spend(None, 0, 0, 0)
