# src/outreach/store/dimensions.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from outreach.types import Company, Contact, PersonRef, SourceClass, SourceDocument


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CompanyRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(
        self, domain: str, name: str, headcount: int | None, headcount_source: str | None
    ) -> int:
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

    def get(self, company_id: int) -> Company:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        return Company(row["id"], row["canonical_domain"], row["name"],
                       row["headcount"], row["headcount_source"])


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
        # cur.lastrowid is unreliable here: it is a connection-level value
        # that is NOT reset when INSERT OR IGNORE finds a conflict and
        # inserts nothing, so it can report the id of a *different*, more
        # recently inserted row instead of this document's real id. Always
        # look the row up by its unique key rather than trusting lastrowid.
        self.conn.execute(
            """INSERT OR IGNORE INTO source_documents
               (company_id, url, source_class, publisher_domain, published_at,
                fetched_at, http_status, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
