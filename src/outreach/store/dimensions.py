# src/outreach/store/dimensions.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from outreach.core.dedupe import canonical_domain
from outreach.types import Company, Contact, PersonRef


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def row_to_contact(row: sqlite3.Row) -> Contact:
    """A `contacts` row as a Contact. The table's `contacted_at` column is not
    read: marks live on the emailed list now."""
    return Contact(row["id"], row["company_id"], row["full_name"], row["title"],
                   row["profile_url"], row["email"], row["email_status"],
                   row["provider"], _dt(row["looked_up_at"]))


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
        return Company(row["id"], row["canonical_domain"], row["name"],
                       row["headcount"], row["headcount_source"], row["headcount_band"])


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

    def for_company(self, company_id: int) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()
        return [row_to_contact(r) for r in rows]

    def already_resolved(self, company_id: int, full_name: str) -> Contact | None:
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND full_name = ?",
            (company_id, full_name),
        ).fetchone()
        return row_to_contact(row) if row else None
