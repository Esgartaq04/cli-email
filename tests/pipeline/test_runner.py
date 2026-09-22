import sqlite3
from datetime import datetime

import pytest

from outreach.pipeline.runner import run_pipeline
from outreach.types import PersonRef, SourceClass
from tests.pipeline.factories import build_context


def _raise_on(ctx, fragment: str, error: BaseException, once: bool = False):
    """Make exactly one surface blow up, leaving every other surface intact.

    Not an httpx error: `Fetcher.get` converts those into a `network_error`
    outcome by design, which the runner already handles as data. Only a
    non-`FetchOutcome` failure reaches the isolation boundary under test.
    """
    real = ctx.fetcher.get
    fired = []

    def get(url, *args, **kwargs):
        if fragment in url and not (once and fired):
            fired.append(url)
            raise error
        return real(url, *args, **kwargs)

    ctx.fetcher.get = get


class CountingProvider:
    """Wraps the fake provider to count what the runner actually pays for."""

    def __init__(self, inner):
        self.inner = inner
        self.verify_calls: list[str] = []

    @property
    def find_calls(self):
        return self.inner.find_calls

    def find(self, domain, role_keywords):
        return self.inner.find(domain, role_keywords)

    def verify(self, email):
        self.verify_calls.append(email)
        return self.inner.verify(email)

    def remaining_credits(self):
        return self.inner.remaining_credits()


def test_happy_path_produces_an_evidenced_company():
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.companies == 2
    assert summary.evidenced == 1
    assert summary.no_bottleneck == 1


def test_one_company_failing_does_not_abort_the_run():
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.failures == 1
    assert summary.evidenced == 1  # the healthy company still finished


def test_quota_shortfall_marks_companies_skipped_not_missing():
    ctx = build_context(credits=1)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.skipped_quota == 1
    assert summary.companies == 2


def test_rejected_quotes_are_counted_in_the_summary():
    ctx = build_context(include_invented_quote=True)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.quotes_rejected >= 1


def test_rerunning_the_same_run_id_skips_completed_stages():
    ctx = build_context()
    first = run_pipeline(ctx, "Backend Engineer", "fintech")
    calls_before = len(ctx.contact_provider.find_calls)
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=first.run_id)
    assert len(ctx.contact_provider.find_calls) == calls_before


def test_only_current_state_surfaces_are_dated_with_the_fetch_date():
    """A dated archive we cannot parse a date from must not look fresh."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    by_class = {i.source_class: i.published_at
                for i in ctx.evidence.for_company(summary.run_id, good)}

    assert by_class[SourceClass.CAREERS_PAGE] == ctx.today
    assert by_class[SourceClass.ENG_BLOG] is None
    assert by_class[SourceClass.CHANGELOG] is None


def test_a_failed_company_gets_no_fabricated_verdict():
    """`no bottleneck found` about a company we never researched is a lie."""
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    bad = ctx.companies.upsert("bad.example", "Bad Co", None, None)
    assert ctx.runs.stage_status(summary.run_id, bad, "evidence") == "failed"
    verdicts = [b.company_id for b in ctx.bottlenecks.for_run(summary.run_id)]
    assert bad not in verdicts


def test_a_malformed_posting_does_not_cost_the_other_companies():
    """One posting with no usable domain is one failure, not a dead run."""
    ctx = build_context(include_malformed_posting=True)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    assert summary.companies == 2
    assert summary.failures == 1
    assert any("Broken Co" in e for e in summary.errors)
    assert summary.evidenced == 1
    assert summary.no_bottleneck == 1


def test_a_contact_provider_outage_does_not_discard_the_run():
    """Losing the credit check costs the contacts stage, not the evidence."""
    ctx = build_context()

    def blow_up():
        raise RuntimeError("provider down")

    ctx.contact_provider.remaining_credits = blow_up
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    assert summary.evidenced == 1
    assert any("provider down" in e for e in summary.errors)

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    assert ctx.runs.stage_status(summary.run_id, good, "contacts") == "failed"
    assert ctx.runs.stage_status(summary.run_id, good, "evidence") == "ok"


def test_a_failing_finish_still_returns_the_summary():
    """The run is over and paid for; a bad UPDATE must not eat the result."""
    ctx = build_context()

    def blow_up(*args, **kwargs):
        raise RuntimeError("db locked")

    ctx.runs.finish = blow_up
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    assert summary.evidenced == 1
    assert summary.no_bottleneck == 1
    assert any("db locked" in e for e in summary.errors)


def test_one_failing_surface_still_lets_the_company_reach_a_verdict():
    """Two good surfaces clear the gate even when the third one explodes."""
    ctx = build_context()
    _raise_on(ctx, "/changelog", RuntimeError("changelog blew up"))
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    assert ctx.runs.stage_status(summary.run_id, good, "evidence") == "ok"
    assert len(ctx.evidence.for_company(summary.run_id, good)) == 2
    assert summary.evidenced == 1
    assert any("changelog blew up" in e for e in summary.errors)

    verdicts = {b.company_id: b.passed
                for b in ctx.bottlenecks.for_run(summary.run_id)}
    assert verdicts[good] is True


def test_a_company_whose_every_surface_fails_is_marked_failed():
    """The other half of the rule: no surface reached means no verdict."""
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    bad = ctx.companies.upsert("bad.example", "Bad Co", None, None)
    assert ctx.runs.stage_status(summary.run_id, bad, "evidence") == "failed"
    assert summary.failures == 1


def test_resuming_after_a_crash_does_not_duplicate_evidence():
    """A company re-researched on resume must not cite each quote twice."""
    ctx = build_context()
    # A KeyboardInterrupt escapes the runner's `except Exception` exactly as
    # a killed process would: the first two surfaces' evidence is already
    # committed, and no `evidence` checkpoint is ever written.
    _raise_on(ctx, "/changelog", KeyboardInterrupt(), once=True)
    run_id = ctx.runs.create("Backend Engineer", "fintech", "US", (20, 1000),
                             datetime.now())
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=run_id)

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    assert len(ctx.evidence.for_company(run_id, good)) == 2  # partial, committed
    assert ctx.runs.stage_status(run_id, good, "evidence") is None

    summary = run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=run_id)

    rows = ctx.evidence.for_company(run_id, good)
    assert len(rows) == 3  # not 5
    assert len({r.quote for r in rows}) == 3
    assert len(ctx.fetch_attempts.for_company(run_id, good)) == 4  # not 6
    assert summary.quotes_accepted == 3


def test_an_already_resolved_contact_is_not_verified_again():
    """The user never pays a second credit for a person already confirmed."""
    ctx = build_context()
    ctx.contact_provider._people["good.example"] = [
        PersonRef("Marisol Okonkwo", "Co-founder & CTO", None,
                  "m@good.example", "unverified"),
    ]
    ctx.contact_provider = CountingProvider(ctx.contact_provider)

    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.verify_calls == ["m@good.example"]

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    stored = ctx.contacts.for_company(good)
    assert [(c.email, c.email_status) for c in stored] == [
        ("m@good.example", "verified")]

    # A brand-new run over the same database: same person, same provider
    # answer, and no second verification.
    ctx.contact_provider.verify_calls.clear()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.verify_calls == []
    assert [(c.email, c.email_status) for c in ctx.contacts.for_company(good)] == [
        ("m@good.example", "verified")]


def test_verified_with_no_address_is_stored_as_not_found():
    """A `verified` status with nothing to send to is not a verified contact."""
    ctx = build_context()
    ctx.contact_provider._people["good.example"] = [
        PersonRef("Marisol Okonkwo", "Co-founder & CTO", None, None, "verified"),
    ]
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    assert [(c.email, c.email_status) for c in ctx.contacts.for_company(good)] == [
        (None, "not_found")]
    assert summary.evidenced == 1


def test_the_short_circuit_only_covers_the_address_already_paid_for():
    """A new address is still verified; a blank one keeps the confirmed one."""
    ctx = build_context()
    ctx.contact_provider._people["good.example"] = [
        PersonRef("Marisol Okonkwo", "Co-founder & CTO", None,
                  "m@good.example", "unverified"),
    ]
    ctx.contact_provider = CountingProvider(ctx.contact_provider)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    good = ctx.companies.upsert("good.example", "Good Co", None, None)

    # The provider now reports a DIFFERENT address: a different question,
    # so it is worth a credit.
    ctx.contact_provider.inner._people["good.example"] = [
        PersonRef("Marisol Okonkwo", "Co-founder & CTO", None,
                  "marisol@good.example", "unverified"),
    ]
    ctx.contact_provider.verify_calls.clear()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.verify_calls == ["marisol@good.example"]
    assert [(c.email, c.email_status) for c in ctx.contacts.for_company(good)] == [
        ("marisol@good.example", "verified")]

    # The provider now has no address at all. The one we paid to confirm
    # must survive rather than being overwritten with `not_found`.
    ctx.contact_provider.inner._people["good.example"] = [
        PersonRef("Marisol Okonkwo", "Co-founder & CTO", None, None, "not_found"),
    ]
    ctx.contact_provider.verify_calls.clear()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.verify_calls == []
    assert [(c.email, c.email_status) for c in ctx.contacts.for_company(good)] == [
        ("marisol@good.example", "verified")]


def test_a_set_stage_failure_for_one_company_does_not_abort_the_run():
    """The evidence checkpoint write is data, not a crash, like everything else.

    `set_stage` for the evidence stage sits, after the per-surface rework,
    outside any surrounding guard on the success path: a real research
    result (2 good surfaces) is computed, and only the write that records
    it fails. That write raising must not take the rest of the run with it.
    """
    ctx = build_context()
    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    real_set_stage = ctx.runs.set_stage

    def set_stage(run_id, company_id, stage, status, error=None):
        if company_id == good and stage == "evidence" and status == "ok":
            raise sqlite3.OperationalError("database is locked")
        return real_set_stage(run_id, company_id, stage, status, error=error)

    ctx.runs.set_stage = set_stage

    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    # The failure was recorded, not swallowed and not fatal.
    assert any("database is locked" in e for e in summary.errors)
    assert ctx.runs.stage_status(summary.run_id, good, "evidence") == "failed"

    # No fabricated verdict for the company whose checkpoint write failed.
    verdicts = {b.company_id: b.passed for b in ctx.bottlenecks.for_run(summary.run_id)}
    assert good not in verdicts

    # The other company was never touched by the injected failure and still
    # reached a verdict.
    thin = ctx.companies.upsert("thin.example", "Thin Co", None, None)
    assert thin in verdicts
    assert verdicts[thin] is False  # thin.example is the no-bottleneck company
    assert summary.no_bottleneck == 1
