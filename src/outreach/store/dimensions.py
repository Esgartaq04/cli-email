# src/outreach/store/dimensions.py
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from outreach.core.dedupe import canonical_domain
from outreach.types import (
    Company,
    CompanyFacts,
    Contact,
    FundingRound,
    PersonRef,
    SourceClass,
    SourceDocument,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CompanyRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(
        self, domain: str, name: str, headcount: int | None, headcount_source: str | None
    ) -> int:
        # Domain variants are the main way one company gets researched (and
        # billed) twice. Callers already canonicalize before reaching here,
        # but making this the choke point rather than a convention every
        # caller must remember is what actually guarantees it.
        domain = canonical_domain(domain)
        self.conn.execute(
            """INSERT INTO companies (canonical_domain, name, headcount, headcount_source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(canonical_domain) DO UPDATE SET
                 name = excluded.name,
                 headcount = COALESCE(excluded.headcount, companies.headcount),
                 headcount_source = COALESCE(excluded.headcount_source,
                                             companies.headcount_source)""",
            (domain, name, headcount, headcount_source),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM companies WHERE canonical_domain = ?", (domain,)
        ).fetchone()
        return int(row["id"])

    def find(self, domain: str) -> Company | None:
        """The stored company for a domain, or None -- never creates one."""
        row = self.conn.execute(
            "SELECT id FROM companies WHERE canonical_domain = ?",
            (canonical_domain(domain),)).fetchone()
        return self.get(int(row["id"])) if row else None

    def get(self, company_id: int) -> Company:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        rounds = tuple(
            FundingRound(r["kind"], date.fromisoformat(r["announced"]) if r["announced"] else None)
            for r in json.loads(row["funding_rounds"] or "[]"))
        return Company(row["id"], row["canonical_domain"], row["name"],
                       row["headcount"], row["headcount_source"],
                       row["headcount_band"], row["founded_year"], rounds,
                       tuple(json.loads(row["tags"] or "[]")), row["github_org"],
                       _dt(row["facts_fetched_at"]))

    def set_facts(self, company_id: int, facts: CompanyFacts, fetched_at: datetime) -> None:
        # A facts record with no headcount must not erase one we already know
        # (e.g. from a careers page), same as `upsert`'s COALESCE -- and the
        # source label travels with the number it describes.
        self.conn.execute(
            """UPDATE companies SET
                 headcount = COALESCE(?, headcount),
                 headcount_source = CASE WHEN ? IS NULL THEN headcount_source ELSE ? END,
                 headcount_band = ?, founded_year = ?, funding_rounds = ?, tags = ?,
                 facts_source = ?, facts_fetched_at = ?
               WHERE id = ?""",
            (facts.headcount, facts.headcount, facts.source, facts.headcount_band,
             facts.founded_year,
             json.dumps([{"kind": r.kind,
                          "announced": r.announced.isoformat() if r.announced else None}
                         for r in facts.funding_rounds]),
             json.dumps(list(facts.tags)), facts.source, fetched_at.isoformat(), company_id),
        )
        self.conn.commit()

    def mark_facts_checked(self, company_id: int, fetched_at: datetime) -> None:
        """Stamp the fetch time only: Hunter was asked and had nothing, and the
        stamp is what stops the next run re-spending a credit on the same miss
        inside the facts TTL."""
        self.conn.execute("UPDATE companies SET facts_fetched_at = ? WHERE id = ?",
                          (fetched_at.isoformat(), company_id))
        self.conn.commit()

    def set_github_org(self, company_id: int, org: str | None) -> None:
        self.conn.execute("UPDATE companies SET github_org = ? WHERE id = ?",
                          (org, company_id))
        self.conn.commit()


class ContactRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(
        self, company_id: int, person: PersonRef, provider: str, looked_up_at: datetime
    ) -> int:
        email = person.email if person.email_status == "verified" else None
        self.conn.execute(
            """INSERT INTO contacts (company_id, full_name, title, profile_url, email,
                                     email_status, provider, looked_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(company_id, full_name) DO UPDATE SET
                 title = excluded.title,
                 profile_url = COALESCE(excluded.profile_url, contacts.profile_url),
                 email = CASE WHEN excluded.email_status = 'verified'
                              THEN excluded.email ELSE NULL END,
                 email_status = excluded.email_status,
                 looked_up_at = excluded.looked_up_at""",
            (company_id, person.full_name, person.title, person.profile_url, email,
             person.email_status, provider, looked_up_at.isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM contacts WHERE company_id = ? AND full_name = ?",
            (company_id, person.full_name),
        ).fetchone()
        return int(row["id"])

    def _row_to_contact(self, row: sqlite3.Row) -> Contact:
        return Contact(row["id"], row["company_id"], row["full_name"], row["title"],
                       row["profile_url"], row["email"], row["email_status"],
                       row["provider"], _dt(row["looked_up_at"]), _dt(row["contacted_at"]))

    def for_company(self, company_id: int) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    def already_resolved(self, company_id: int, full_name: str) -> Contact | None:
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND full_name = ?",
            (company_id, full_name),
        ).fetchone()
        return self._row_to_contact(row) if row else None

    def mark_contacted(self, contact_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE contacts SET contacted_at = ? WHERE id = ?",
                          (when.isoformat(), contact_id))
        self.conn.commit()


class DocumentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, doc: SourceDocument) -> int:
        # The same url with the same text is one document, but reading it
        # again is news: a GitHub repo whose description is unchanged may
        # have been pushed since (its date is the push), and the fetch time
        # is how a report tells which run read it. Keeping the first-seen
        # dates would pin a repo to its oldest push forever and let an old
        # run's documents pass for this one's, so a conflict refreshes both.
        #
        # cur.lastrowid is unreliable here: it is a connection-level value
        # that is NOT reset when the conflict path inserts nothing, so it
        # can report the id of a *different*, more recently inserted row
        # instead of this document's real id. Always look the row up by its
        # unique key rather than trusting lastrowid.
        self.conn.execute(
            """INSERT INTO source_documents
               (company_id, url, source_class, publisher_domain, published_at,
                fetched_at, http_status, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url, content_hash) DO UPDATE SET
                 published_at = excluded.published_at,
                 fetched_at = excluded.fetched_at""",
            (doc.company_id, doc.url, doc.source_class.value, doc.publisher_domain,
             doc.published_at.isoformat() if doc.published_at else None,
             doc.fetched_at.isoformat(), doc.http_status, doc.content_hash),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM source_documents WHERE url = ? AND content_hash = ?",
            (doc.url, doc.content_hash),
        ).fetchone()
        return int(row["id"])

    def for_company(self, company_id: int) -> list[SourceDocument]:
        rows = self.conn.execute(
            "SELECT * FROM source_documents WHERE company_id = ? ORDER BY id",
            (company_id,)).fetchall()
        return [
            SourceDocument(
                r["id"], r["company_id"], r["url"], SourceClass(r["source_class"]),
                r["publisher_domain"],
                datetime.fromisoformat(r["published_at"]).date() if r["published_at"] else None,
                datetime.fromisoformat(r["fetched_at"]), r["http_status"], r["content_hash"],
            ) for r in rows
        ]
