# src/outreach/store/emailed.py
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.types import EmailedEntry

# An entry also carries what a stored contact tells about its address, so a
# listing can say who it was. Addresses are stored lowercased; a contact's is
# stored as Hunter gave it, hence lower(). Two contacts can share an address
# (one person at two companies), and the join takes the newest so an address
# is never listed twice.
_ENTRIES = """
SELECT e.address, e.emailed_on, c.full_name, c.title, co.name AS company
FROM emailed e
LEFT JOIN contacts c ON c.id = (
    SELECT id FROM contacts
    WHERE lower(email) = e.address AND email_status = 'verified'
    ORDER BY id DESC LIMIT 1)
LEFT JOIN companies co ON co.id = c.company_id
"""
_NEWEST_FIRST = " ORDER BY e.emailed_on DESC, e.address"


class EmailedRepo:
    """The addresses already emailed. Every address passed in is already
    normalized (see `core.addresses.normalize_address`)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, addresses: Sequence[str], on: date, now: datetime) -> None:
        # Adding an address again moves its date; `added_at` stays the moment
        # it first went on the list.
        self.conn.executemany(
            """INSERT INTO emailed (address, emailed_on, added_at) VALUES (?, ?, ?)
               ON CONFLICT(address) DO UPDATE SET emailed_on = excluded.emailed_on""",
            [(address, on.isoformat(), now.isoformat()) for address in addresses])
        self.conn.commit()

    def remove(self, addresses: Sequence[str]) -> list[str]:
        """Take addresses off the list; return those that were on it."""
        removed = []
        for address in addresses:
            cur = self.conn.execute("DELETE FROM emailed WHERE address = ?", (address,))
            if cur.rowcount:
                removed.append(address)
        self.conn.commit()
        return removed

    def get(self, address: str) -> date | None:
        row = self.conn.execute(
            "SELECT emailed_on FROM emailed WHERE address = ?", (address,)).fetchone()
        return date.fromisoformat(row["emailed_on"]) if row else None

    def all(self) -> list[EmailedEntry]:
        rows = self.conn.execute(_ENTRIES + _NEWEST_FIRST).fetchall()
        return [self._entry(r) for r in rows]

    def mark_for_company(self, company_id: int | None, domain: str) -> EmailedEntry | None:
        """The newest mark that puts this company on the list: an address at its
        domain, or the address of one of its stored contacts (which may be at
        another domain, like a personal mailbox)."""
        row = self.conn.execute(
            _ENTRIES + """
            WHERE lower(substr(e.address, instr(e.address, '@') + 1)) = ?
               OR e.address IN (SELECT lower(email) FROM contacts
                                WHERE company_id = ? AND email IS NOT NULL)"""
            + _NEWEST_FIRST + " LIMIT 1",
            (canonical_domain(domain), company_id)).fetchone()
        return self._entry(row) if row else None

    @staticmethod
    def _entry(row: sqlite3.Row) -> EmailedEntry:
        return EmailedEntry(row["address"], date.fromisoformat(row["emailed_on"]),
                            row["full_name"], row["title"], row["company"])
