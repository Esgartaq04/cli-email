"""Buying contacts: the tail a companies run and a jobs run share, and where credits are spent.

Every company is checked, for free and in a fixed order, before a credit is
committed to it: bought on an earlier list, someone emailed there, over the
run's cap, a guessed domain nobody answers on, nobody to find. Only then is its
worst-case cost reserved, the people bought tier by tier, their addresses
confirmed, and the reservation settled for what was really used. Credits are
tenths throughout (see `outreach.credits`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence, TypeVar

from outreach.config import PeopleList
from outreach.contacts.ledger import CreditLedger
from outreach.core.people import is_excluded, order_people
from outreach.pipeline.context import RunContext
from outreach.pipeline.summary import RunSummary
from outreach.types import EmailStatus, FetchAttempt, FoundPerson, PersonRef, SourceClass, Spend

STAGE = "contacts"
# A company in one of these is finished for this run; a resume leaves it alone.
# `over_cap`, `out_of_credits` and `failed` are not: they are tried again.
DONE_STATUSES = frozenset({"bought", "empty", "no_matching_people", "skipped_earlier_list",
                           "skipped_emailed", "domain_unconfirmed"})

T = TypeVar("T")


@dataclass(frozen=True)
class Candidate:
    """A company a run may buy contacts at. `guessed_domain` is a domain nobody
    has confirmed (a job board's token, say), which is checked before buying."""
    company_id: int
    domain: str
    name: str
    guessed_domain: bool = False


@dataclass
class Preview:
    """What `buy_contacts` would do with some companies, without doing it."""
    to_buy: int = 0
    earlier_list: int = 0
    emailed: int = 0
    no_one: int = 0
    over_cap: int = 0


@dataclass
class _Basket:
    """What buying one company's contacts has used so far.

    It is filled by the steps and read when settling, so the credits really
    spent are counted even if a later step raises.
    """
    charged: int = 0  # addresses returned: one email credit each
    verifications: int = 0
    search_failed: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        return "; ".join(self.problems) or None


def max_cost(ctx: RunContext, companies: int) -> int:
    """The most buying contacts at `companies` companies can cost, in tenths."""
    hunter = ctx.config.hunter
    return companies * ctx.config.run.contacts_per_company * (hunter.email_cost + hunter.verify_cost)


def _fresh_first(items: Sequence[T], came_back_empty: Callable[[T], bool]) -> list[T]:
    """Keep the given order, but put what came back empty before behind everything else."""
    flagged = [(item, came_back_empty(item)) for item in items]
    return [item for item, empty in flagged if not empty] + [item for item, empty in flagged if empty]


def restore_candidates(ctx: RunContext, run_id: int) -> list[Candidate]:
    """The companies a run kept after discovery, as it recorded them, by position."""
    candidates = []
    for company_id, info in ctx.company_info.for_run(run_id):
        if ctx.runs.stage_status(run_id, company_id, "discover") == "ok":
            company = ctx.companies.get(company_id)
            candidates.append(Candidate(company_id, company.canonical_domain, company.name,
                                        info.guessed_domain))
    return candidates


def preview_contacts(ctx: RunContext, people: PeopleList, domains: Sequence[str]) -> Preview:
    """How a run would treat `domains`, checked in the order and by the rules
    `buy_contacts` uses. Reads only: it fetches, searches, verifies and writes nothing.

    A domain with no stored company is fresh, so only the checks that need a
    company id are skipped for it. Here `to_buy` stands in for the companies a
    real run would have bought at, since a free count cannot tell who is empty.
    """
    entries = [(domain, ctx.companies.find(domain)) for domain in domains]
    ordered = _fresh_first(entries, lambda entry: entry[1] is not None and
                           ctx.runs.came_back_empty_before(entry[1].id, people.name, None))
    cap = ctx.config.run.max_companies
    preview = Preview()
    for domain, company in ordered:
        company_id = company.id if company is not None else None
        if company_id is not None and ctx.runs.bought_before(
                company_id, people.name, None) is not None:
            preview.earlier_list += 1
        elif ctx.emailed.mark_for_company(company_id, domain) is not None:
            preview.emailed += 1
        elif preview.to_buy >= cap:
            preview.over_cap += 1
        elif ctx.people.count_people(domain) == 0:
            preview.no_one += 1
        else:
            preview.to_buy += 1
    return preview


def buy_contacts(ctx: RunContext, run_id: int, people: PeopleList,
                 candidates: Sequence[Candidate], summary: RunSummary) -> None:
    """Buy contacts at each candidate, in the order the spec fixes.

    Each company ends in one `contacts` stage status (see `types.StageStatus`),
    and the credits used are added to the run. Calling it again for the same run
    resumes: companies already finished are left alone and count against the cap.
    """
    _Buyer(ctx, run_id, people, summary).run(candidates)


class _Buyer:
    """One `buy_contacts` call: the balance, the ledger and the counts its steps share."""

    def __init__(self, ctx: RunContext, run_id: int, people: PeopleList,
                 summary: RunSummary) -> None:
        self.ctx, self.run_id, self.people, self.summary = ctx, run_id, people, summary
        self.cap = ctx.config.run.max_companies
        self.cpc = ctx.config.run.contacts_per_company
        self.balance: int | None = None
        self.balance_error: Exception | None = None
        self.ledger = CreditLedger(0)
        self.bought = 0  # companies with someone bought, resumed ones included
        self.emails = 0  # this call's
        self.verifications = 0

    def run(self, candidates: Sequence[Candidate]) -> None:
        self._read_balance()
        ordered = _fresh_first(candidates, lambda c: self.ctx.runs.came_back_empty_before(
            c.company_id, self.people.name, self.run_id))
        self.bought = sum(1 for c in candidates if self._stage(c) == "bought")
        try:
            for candidate in ordered:
                if self._stage(candidate) not in DONE_STATUSES:
                    self._visit(candidate)
        finally:
            # Also when the run is interrupted: what was settled is already spent.
            self._record_spend()

    def _stage(self, candidate: Candidate) -> str | None:
        return self.ctx.runs.stage_status(self.run_id, candidate.company_id, STAGE)

    def _read_balance(self) -> None:
        try:
            self.balance = self.ctx.people.balance_tenths()
        except Exception as exc:
            self.summary.errors.append(f"credits: {exc}")
            self.balance_error = exc
        self.ledger = CreditLedger(self.balance or 0)

    def _record_spend(self) -> None:
        self.summary.balance_before = self.balance
        self.summary.spent += self.ledger.spent
        self.summary.bought = self.bought
        self.ctx.runs.record_spend(
            self.run_id, Spend(self.balance, self.emails, self.verifications, self.ledger.spent))

    def _visit(self, candidate: Candidate) -> None:
        try:
            status, error = self._decide(candidate)
        except Exception as exc:
            self.summary.errors.append(f"contacts {candidate.domain}: {exc}")
            status, error = "failed", str(exc)
        if status == "bought":
            self.bought += 1
        self.ctx.runs.set_stage(self.run_id, candidate.company_id, STAGE, status, error=error)

    def _decide(self, candidate: Candidate) -> tuple[str, str | None]:
        """The company's (status, error): the free checks, then the purchase."""
        company_id, domain = candidate.company_id, candidate.domain
        if self.ctx.runs.bought_before(company_id, self.people.name, self.run_id) is not None:
            return "skipped_earlier_list", None
        if self.ctx.emailed.mark_for_company(company_id, domain) is not None:
            return "skipped_emailed", None
        if self.bought >= self.cap:
            return "over_cap", None
        if candidate.guessed_domain and not self._homepage_answers(candidate):
            return "domain_unconfirmed", None
        if self.balance_error is not None:
            return "failed", str(self.balance_error)
        if self.ctx.people.count_people(domain) == 0:
            return "no_matching_people", None
        reserved = max_cost(self.ctx, 1)
        if not self.ledger.try_reserve(reserved):
            self.summary.out_of_credits += 1
            return "out_of_credits", None
        return self._buy(candidate, reserved)

    def _homepage_answers(self, candidate: Candidate) -> bool:
        """Whether the guessed domain's homepage loads, which is what confirms it
        is a real site. The attempt is recorded, replacing one from an earlier try."""
        url = f"https://{candidate.domain}/"
        self.ctx.fetch_attempts.clear_for_company(
            self.run_id, candidate.company_id, only=frozenset({SourceClass.HOMEPAGE}))
        fetched = self.ctx.fetcher.get(url)
        self.ctx.fetch_attempts.insert(self.run_id, FetchAttempt(
            candidate.company_id, SourceClass.HOMEPAGE, url, fetched.outcome, fetched.status, 0))
        return fetched.outcome == "ok"

    def _buy(self, candidate: Candidate, reserved: int) -> tuple[str, str | None]:
        basket = _Basket()
        try:
            kept = self._search(candidate.domain, basket)
            ranked = order_people(kept, self.people)[:self.cpc]
            statuses = [self._confirm(candidate, person, basket) for person in ranked]
            self._store(candidate, ranked, statuses)
        finally:
            self._settle(reserved, basket)
        if ranked:
            return "bought", basket.error
        # A company we could not search must not be remembered as one that came back empty.
        return ("failed" if basket.search_failed else "empty"), basket.error

    def _search(self, domain: str, basket: _Basket) -> list[FoundPerson]:
        """Buy tier by tier until the company's slots are full. Every address
        returned is paid for, kept or not."""
        kept: list[FoundPerson] = []
        seen: set[str] = set()
        for tier in self.people.tiers:
            # Whoever is kept was paid for, so a slot still open means credits are too.
            limit = self.cpc - basket.charged
            if limit <= 0:
                break
            try:
                found = self.ctx.people.search_people(domain, tier, limit)
            except Exception as exc:
                self._problem(domain, exc, basket)
                basket.search_failed = True
                break
            basket.charged += len(found)
            for person in found:
                email = person.email.lower()
                if email not in seen and not is_excluded(person.title, self.people):
                    seen.add(email)
                    kept.append(person)
        return kept

    def _confirm(self, candidate: Candidate, person: FoundPerson, basket: _Basket) -> EmailStatus:
        """Whether the address is deliverable, paying for a check only when nothing known says so."""
        if self._known_verified(candidate, person) or self._recently_valid(person):
            return "verified"
        try:
            status = self.ctx.people.verify(person.email)
        except Exception as exc:
            # A check that never answered was not billed, and the address stays unconfirmed.
            self._problem(candidate.domain, exc, basket)
            return "unverified"
        basket.verifications += 1
        return status

    def _known_verified(self, candidate: Candidate, person: FoundPerson) -> bool:
        earlier = self.ctx.contacts.already_resolved(candidate.company_id, person.full_name)
        return (earlier is not None and earlier.email_status == "verified"
                and earlier.email is not None and earlier.email.lower() == person.email.lower())

    def _recently_valid(self, person: FoundPerson) -> bool:
        return (person.hunter_status == "valid" and person.hunter_checked_on is not None
                and (self.ctx.today - person.hunter_checked_on).days
                <= self.ctx.config.hunter.verified_max_age_days)

    def _store(self, candidate: Candidate, ranked: Sequence[FoundPerson],
               statuses: Sequence[EmailStatus]) -> None:
        for position, (person, status) in enumerate(zip(ranked, statuses)):
            ref = PersonRef(person.full_name, person.title, person.profile_url,
                            person.email if status == "verified" else None, status)
            contact_id = self.ctx.contacts.upsert(candidate.company_id, ref, "hunter", datetime.now())
            self.ctx.run_contacts.add(self.run_id, candidate.company_id, contact_id, position)

    def _settle(self, reserved: int, basket: _Basket) -> None:
        hunter = self.ctx.config.hunter
        self.ledger.settle(reserved, basket.charged * hunter.email_cost
                           + basket.verifications * hunter.verify_cost)
        self.emails += basket.charged
        self.verifications += basket.verifications
        self.summary.emails += basket.charged
        self.summary.verifications += basket.verifications

    def _problem(self, domain: str, exc: Exception, basket: _Basket) -> None:
        self.summary.errors.append(f"contacts {domain}: {exc}")
        basket.problems.append(str(exc))
