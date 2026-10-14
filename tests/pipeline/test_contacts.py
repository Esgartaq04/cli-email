"""The shared tail of a run: which companies get contacts bought, and the credits it costs."""
from dataclasses import replace
from datetime import datetime, timedelta

from outreach.config import DEFAULT_PEOPLE, PeopleList, RunConfig
from outreach.contacts.fake import FakePeopleFinder
from outreach.pipeline.contacts import (Candidate, Preview, buy_contacts, max_cost,
                                        preview_contacts, restore_candidates)
from outreach.pipeline.summary import RunSummary
from outreach.types import CompanyInfo, PersonRef, SourceClass, Spend
from tests.pipeline.factories import CONFIG, TODAY, build_context, company_id, person, start_run

HIRING = DEFAULT_PEOPLE["hiring"]
SPONSORS = DEFAULT_PEOPLE["sponsors"]


def _candidate(ctx, domain, guessed=False):
    name = domain.split(".")[0].title()
    return Candidate(ctx.companies.upsert(domain, name, None, None), domain, name, guessed)


def _buy(ctx, run_id, *domains, people=HIRING, guessed=False):
    summary = RunSummary(run_id)
    buy_contacts(ctx, run_id, people, [_candidate(ctx, d, guessed) for d in domains], summary)
    return summary


def _status(ctx, run_id, domain):
    return ctx.runs.stage_status(run_id, company_id(ctx, domain), "contacts")


def _names(ctx, run_id, domain):
    return [c.full_name for c in ctx.run_contacts.for_company(run_id, company_id(ctx, domain))]


def _capped(cap):
    return replace(CONFIG, run=RunConfig(max_companies=cap))


def test_buys_people_tier_by_tier_until_the_company_is_full():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "Engineering Manager"), person("Bo Diaz", "CTO"),
                                             person("Cy Park", "VP Engineering"), person("Di Moe", "Tech Lead")]})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert finder.search_calls == [("a.example", HIRING.tiers[0], 3), ("a.example", HIRING.tiers[1], 2),
                                   ("a.example", HIRING.tiers[2], 1)]
    assert [c.full_name for c in ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))] == [
        "Bo Diaz", "Cy Park", "Ann Lee"]
    assert (_status(ctx, run_id, "a.example"), summary.emails, summary.verifications, summary.spent,
            summary.bought) == ("bought", 3, 0, 30, 1)


def test_a_fresh_valid_is_trusted_and_a_stale_one_is_checked():
    fresh = person("Bo Diaz", "CTO", checked_on=TODAY - timedelta(days=90))
    stale = person("Cy Park", "VP Engineering", checked_on=TODAY - timedelta(days=91))
    finder = FakePeopleFinder({"a.example": [fresh, stale]})
    ctx = build_context(people=finder); summary = _buy(ctx, start_run(ctx), "a.example")
    assert finder.verify_calls == [stale.email] and (summary.verifications, summary.spent) == (1, 25)


def test_an_unconfirmed_address_is_never_stored():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO", status="accept_all")]},
                              statuses={"bo@a.example": "unverified"})
    ctx = build_context(people=finder); run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    [contact] = ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))
    assert (contact.email, contact.email_status) == (None, "unverified")


def test_an_address_confirmed_on_an_earlier_run_is_reused_without_a_check():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO", status="accept_all")]})
    ctx = build_context(people=finder)
    cid = ctx.companies.upsert("a.example", "A", None, None)
    ctx.contacts.upsert(cid, PersonRef("Bo Diaz", "CTO", None, "BO@a.example", "verified"),
                        "hunter", datetime(2026, 8, 1))
    run_id = start_run(ctx); summary = _buy(ctx, run_id, "a.example")
    [contact] = ctx.run_contacts.for_company(run_id, cid)
    assert (finder.verify_calls, summary.verifications, summary.spent) == ([], 0, 10)
    assert (contact.email, contact.email_status) == ("bo@a.example", "verified")


def test_a_check_that_fails_leaves_the_address_unconfirmed():
    class VerifierDown(FakePeopleFinder):
        def verify(self, email):
            raise RuntimeError("verifier down")
    finder = VerifierDown({"a.example": [person("Bo Diaz", "CTO", status="accept_all")]})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    [contact] = ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))
    assert (contact.email, contact.email_status) == (None, "unverified")
    assert _status(ctx, run_id, "a.example") == "bought" and (summary.verifications, summary.spent) == (0, 10)
    assert summary.errors == ["contacts a.example: verifier down"]
    assert ctx.runs.stage_error(run_id, company_id(ctx, "a.example"), "contacts") == "verifier down"


def test_failing_second_tier_keeps_people_already_bought():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO"), person("Cy Park", "VP Engineering")]},
                              failing_titles=frozenset({"vp engineering"}))
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "bought" and summary.emails == 1
    assert [c.full_name for c in ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))] == ["Bo Diaz"]
    assert any("a.example" in e for e in summary.errors)


def test_resume_skips_bought_companies_and_counts_them_against_the_cap():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")],
                               "b.example": [person("Cy Park", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, config=replace(CONFIG, run=RunConfig(max_companies=1)))
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    calls = list(finder.search_calls)
    summary = _buy(ctx, run_id, "a.example", "b.example")
    assert finder.search_calls == calls
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example"), summary.bought) == (
        "bought", "over_cap", 1)


def test_excluded_people_are_dropped_even_after_being_paid_for():
    eng = PeopleList("eng", (("engineering",),), ("recruit",))
    finder = FakePeopleFinder({"a.example": [person("Rae Fox", "Engineering Recruiter"),
                                             person("Lee Ng", "Engineering Lead")]})
    ctx = build_context(people=finder); run_id = start_run(ctx, people="eng")
    summary = _buy(ctx, run_id, "a.example", people=eng)
    assert _names(ctx, run_id, "a.example") == ["Lee Ng"] and summary.emails == 2


def test_a_company_bought_on_an_earlier_list_is_skipped_for_free():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO"), person("Cy Park", "Founder"),
                                             person("Di Moe", "Co-founder")]})
    ctx = build_context(people=finder)
    assert _buy(ctx, start_run(ctx), "a.example").bought == 1
    second = start_run(ctx); _buy(ctx, second, "a.example")
    assert _status(ctx, second, "a.example") == "skipped_earlier_list" and len(finder.search_calls) == 1
    third = start_run(ctx, people="sponsors"); _buy(ctx, third, "a.example", people=SPONSORS)
    assert _status(ctx, third, "a.example") == "bought"


def test_pre_v4_contacts_count_as_an_earlier_hiring_list():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]})
    ctx = build_context(people=finder)
    cid = ctx.companies.upsert("a.example", "A", None, None)
    old = ctx.runs.conn.execute(
        "INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max, started_at,"
        " status, schema_version) VALUES ('r','s','US',1,2000,'2026-09-01T10:00:00','done',3)").lastrowid
    ctx.runs.set_stage(old, cid, "contacts", "ok")
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "skipped_earlier_list" and finder.search_calls == []


def test_a_company_where_someone_was_emailed_is_skipped_before_any_call():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]})
    ctx = build_context(people=finder)
    ctx.emailed.add(["sam@a.example"], TODAY, datetime(2026, 9, 1))
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "skipped_emailed"
    assert finder.count_calls == finder.search_calls == []


def test_no_one_matching_costs_nothing():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]}, counts={"a.example": 0})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "no_matching_people"
    assert finder.search_calls == [] and summary.spent == 0


def test_an_empty_search_does_not_use_a_cap_slot():
    finder = FakePeopleFinder({"a.example": [person("Di Moe", "Designer")],
                               "b.example": [person("Bo Diaz", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, config=_capped(1)); run_id = start_run(ctx)
    _buy(ctx, run_id, "a.example", "b.example")
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example")) == ("empty", "bought")


def test_companies_that_came_back_empty_before_go_after_fresh_ones():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "CTO")],
                               "b.example": [person("Bo Diaz", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, config=_capped(1))
    earlier = start_run(ctx)
    ctx.runs.set_stage(earlier, ctx.companies.upsert("a.example", "A", None, None), "contacts", "empty")
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example", "b.example")
    assert (_status(ctx, run_id, "b.example"), _status(ctx, run_id, "a.example")) == ("bought", "over_cap")


def test_companies_over_the_cap_are_never_counted_or_searched():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "CTO")],
                               "b.example": [person("Bo Diaz", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, config=_capped(1)); run_id = start_run(ctx)
    _buy(ctx, run_id, "a.example", "b.example")
    assert finder.count_calls == ["a.example"]
    assert {call[0] for call in finder.search_calls} == {"a.example"}


def test_running_out_of_credits_stops_buying():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "Founder"), person("Bo Diaz", "CTO"),
                                             person("Cy Park", "Co-founder")],
                               "b.example": [person("Di Moe", "CTO", domain="b.example")]}, balance=50)
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example", "b.example")
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example")) == ("bought", "out_of_credits")
    assert (summary.spent, summary.out_of_credits) == (30, 1)
    assert not any(call[0] == "b.example" for call in finder.search_calls)


def test_an_unreadable_balance_buys_nothing():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]},
                              balance_error=RuntimeError("account endpoint down"))
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "failed" and finder.search_calls == []
    assert "credits: account endpoint down" in summary.errors
    assert ctx.runs.stage_error(run_id, company_id(ctx, "a.example"), "contacts") == "account endpoint down"
    assert (summary.balance_before, summary.spent) == (None, 0)


def test_a_guessed_domain_is_bought_only_after_its_homepage_answers():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "CTO")],
                               "b.example": [person("Bo Diaz", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, routes={"https://b.example/": "<html>hello</html>"})
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example", "b.example", guessed=True)
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example")) == (
        "domain_unconfirmed", "bought")
    assert finder.count_calls == ["b.example"]
    a, b = company_id(ctx, "a.example"), company_id(ctx, "b.example")
    [miss] =ctx.fetch_attempts.for_company(run_id, a)
    [hit] = ctx.fetch_attempts.for_company(run_id, b)
    assert (miss.source_class, miss.url, miss.outcome, miss.http_status) == (
        SourceClass.HOMEPAGE, "https://a.example/", "http_error", 404)
    assert (hit.url, hit.outcome, hit.http_status) == ("https://b.example/", "ok", 200)


def test_a_resumed_homepage_check_replaces_its_attempt_row():
    ctx = build_context(people=FakePeopleFinder(balance_error=RuntimeError("down")),
                        routes={"https://a.example/": "<html>hello</html>"})
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example", guessed=True)
    assert _status(ctx, run_id, "a.example") == "failed"
    ctx.people = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]})
    _buy(ctx, run_id, "a.example", guessed=True)
    assert _status(ctx, run_id, "a.example") == "bought"
    assert [a.outcome for a in ctx.fetch_attempts.for_company(run_id, company_id(ctx, "a.example"))] == ["ok"]


def test_spend_is_recorded_on_the_run():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "Founder"), person("Bo Diaz", "CTO"),
                                             person("Cy Park", "Co-founder")]})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert ctx.runs.info(run_id).spend == Spend(500, 3, 0, 30)
    assert summary.balance_before == 500


def test_a_search_that_fails_before_anyone_is_bought_is_failed_not_empty():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")]}, failing_titles=frozenset({"founder"}))
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    cid = company_id(ctx, "a.example")
    assert _status(ctx, run_id, "a.example") == "failed" and summary.spent == 0
    assert ctx.runs.stage_error(run_id, cid, "contacts") == "search failed for a.example"
    assert any("a.example" in e for e in summary.errors)
    assert ctx.runs.came_back_empty_before(cid, "hiring", None) is False


def test_one_failing_company_does_not_stop_the_others():
    class CountDown(FakePeopleFinder):
        def count_people(self, domain):
            if domain == "a.example":
                raise RuntimeError("count failed")
            return super().count_people(domain)
    finder = CountDown({"b.example": [person("Bo Diaz", "CTO", domain="b.example")]})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example", "b.example")
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example")) == ("failed", "bought")
    assert summary.errors == ["contacts a.example: count failed"]


def test_credits_are_settled_even_when_a_company_fails_after_buying():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "Founder"), person("Bo Diaz", "CTO"),
                                             person("Cy Park", "Co-founder")],
                               "b.example": [person("Di Moe", "CTO", domain="b.example")]}, balance=80)
    ctx = build_context(people=finder); run_id = start_run(ctx)
    a = ctx.companies.upsert("a.example", "A", None, None)
    store = ctx.contacts.upsert

    def storing_a_fails(company, ref, provider, when):
        if company == a:
            raise RuntimeError("disk full")
        return store(company, ref, provider, when)
    ctx.contacts.upsert = storing_a_fails
    summary = _buy(ctx, run_id, "a.example", "b.example")
    # a's 3 addresses were paid for before it failed. Had its 45 reserved tenths not been
    # settled, only 35 would be left and b (which reserves 45) would have been refused.
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example")) == ("failed", "bought")
    assert (summary.emails, summary.spent) == (4, 40)
    assert ctx.runs.info(run_id).spend == Spend(80, 4, 0, 40)


def test_preview_counts_without_buying_or_writing():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")],
                               "b.example": [person("Cy Park", "CTO", domain="b.example")],
                               "c.example": [person("Di Moe", "CTO", domain="c.example")]},
                              counts={"b.example": 0})
    ctx = build_context(people=finder)
    ctx.emailed.add(["sam@c.example"], TODAY, datetime(2026, 9, 1))
    preview = preview_contacts(ctx, HIRING, ["a.example", "b.example", "c.example"])
    assert preview == Preview(to_buy=1, emailed=1, no_one=1)
    assert finder.search_calls == [] and finder.verify_calls == []
    assert finder.count_calls == ["a.example", "b.example"]
    assert ctx.companies.find("a.example") is None


def test_preview_applies_earlier_lists_the_cap_and_the_empty_last_order():
    finder = FakePeopleFinder({d: [person("Bo Diaz", "CTO", domain=d)]
                               for d in ("a.example", "b.example", "c.example", "d.example")})
    ctx = build_context(people=finder, config=_capped(1))
    earlier = start_run(ctx)
    ctx.runs.set_stage(earlier, ctx.companies.upsert("a.example", "A", None, None), "contacts", "empty")
    ctx.runs.set_stage(earlier, ctx.companies.upsert("b.example", "B", None, None), "contacts", "bought")
    preview = preview_contacts(ctx, HIRING, ["a.example", "b.example", "c.example", "d.example"])
    # b is on an earlier list; a came back empty so goes last; c takes the one slot.
    assert preview == Preview(to_buy=1, earlier_list=1, over_cap=2)
    assert finder.count_calls == ["c.example"]


def test_restore_candidates_follows_the_recorded_order():
    ctx = build_context(); run_id = start_run(ctx)
    ids = {d: ctx.companies.upsert(d, d.split(".")[0].title(), None, None)
           for d in ("a.example", "b.example", "c.example", "d.example")}
    for domain, position, status, guessed in (("a.example", 2, "ok", True), ("b.example", 0, "ok", False),
                                              ("c.example", 1, "left_out_filters", False),
                                              ("d.example", 3, "ok", False)):
        ctx.company_info.insert(run_id, ids[domain], CompanyInfo(position, "board", guessed_domain=guessed))
        ctx.runs.set_stage(run_id, ids[domain], "discover", status)
    assert restore_candidates(ctx, run_id) == [
        Candidate(ids["b.example"], "b.example", "B", False),
        Candidate(ids["a.example"], "a.example", "A", True),
        Candidate(ids["d.example"], "d.example", "D", False)]


def test_max_cost():
    assert max_cost(build_context(), 2) == 90
