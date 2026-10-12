# src/outreach/store/runs.py
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from outreach.types import FetchAttempt, HNPostRecord, ParsedPost, PostingRef, SourceClass


class RunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, role_title: str, sector: str, region: str,
               band: tuple[int, int], started_at: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (role_title, sector, region, headcount_min,
                                 headcount_max, started_at, status, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, 'running', 2)""",
            (role_title, sector, region, band[0], band[1], started_at.isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def schema_version(self, run_id: int) -> int:
        """1 for a run recorded before findings existed (its report reads
        `bottlenecks`), 2 for every run created since."""
        row = self.conn.execute(
            "SELECT schema_version FROM runs WHERE id = ?", (run_id,)).fetchone()
        return int(row["schema_version"])

    def finish(self, run_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE runs SET finished_at = ?, status = 'done' WHERE id = ?",
                          (when.isoformat(), run_id))
        self.conn.commit()

    def set_stage(self, run_id: int, company_id: int, stage: str,
                  status: str, error: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO run_companies (run_id, company_id, stage, status, error)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id, company_id, stage) DO UPDATE SET
                 status = excluded.status, error = excluded.error""",
            (run_id, company_id, stage, status, error),
        )
        self.conn.commit()

    def stage_status(self, run_id: int, company_id: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM run_companies WHERE run_id=? AND company_id=? AND stage=?",
            (run_id, company_id, stage)).fetchone()
        return row["status"] if row else None

    def companies_for_run(self, run_id: int) -> list[int]:
        """Every company this run ever touched, in company-id order.

        `run_companies` has a row for a company the moment `discover`
        checkpoints it, whatever becomes of it afterwards -- so this is the
        only complete list of the companies a run considered.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT company_id FROM run_companies WHERE run_id = ? "
            "ORDER BY company_id", (run_id,)).fetchall()
        return [int(r["company_id"]) for r in rows]

    def stage_error(self, run_id: int, company_id: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT error FROM run_companies WHERE run_id=? AND company_id=? AND stage=?",
            (run_id, company_id, stage)).fetchone()
        return row["error"] if row else None


class PostingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, company_id: int, posting: PostingRef) -> None:
        # company_name/company_domain are not stored: they are the company's,
        # and `for_company` joins them back so they cannot drift.
        self.conn.execute(
            """INSERT OR IGNORE INTO run_postings (run_id, company_id, title, url,
                                                   location, work_mode, employment_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, company_id, posting.title, posting.url, posting.location,
             posting.work_mode, posting.employment_type),
        )
        self.conn.commit()

    def for_company(self, run_id: int, company_id: int) -> list[PostingRef]:
        rows = self.conn.execute(
            """SELECT p.*, c.name AS company_name, c.canonical_domain AS company_domain
               FROM run_postings p JOIN companies c ON c.id = p.company_id
               WHERE p.run_id = ? AND p.company_id = ? ORDER BY p.id""",
            (run_id, company_id)).fetchall()
        return [
            PostingRef(r["company_name"], r["company_domain"], r["title"], r["url"],
                       r["location"], r["work_mode"], r["employment_type"])
            for r in rows
        ]


class FetchAttemptRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, attempt: FetchAttempt) -> None:
        self.conn.execute(
            """INSERT INTO fetch_attempts (run_id, company_id, source_class, url,
                                           outcome, http_status, document_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, attempt.company_id, attempt.source_class.value, attempt.url,
             attempt.outcome, attempt.http_status, attempt.document_count),
        )
        self.conn.commit()

    def clear_for_company(self, run_id: int, company_id: int,
                          only: frozenset[SourceClass] | None = None,
                          keep: frozenset[SourceClass] | None = None) -> int:
        """Drop this run's fetch attempts for one company; return rows removed.

        A re-run refetches, so the old attempt rows would otherwise accumulate
        and the diagnostics would show one page tried twice as often as it was.

        More than one step can write this log and each re-derives only its own
        share on a resume, so `only` limits the delete to some classes and
        `keep` spares some. It is one DELETE: reading the survivors out and
        writing them back would lose them to a crash in between, and hand them
        new ids.
        """
        sql = "DELETE FROM fetch_attempts WHERE run_id = ? AND company_id = ?"
        params: list[object] = [run_id, company_id]
        for classes, op in ((only, "IN"), (keep, "NOT IN")):
            if classes is not None:
                marks = ", ".join("?" * len(classes))
                sql += f" AND source_class {op} ({marks})"
                params.extend(sorted(c.value for c in classes))
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return int(cur.rowcount)

    def for_company(self, run_id: int, company_id: int) -> list[FetchAttempt]:
        rows = self.conn.execute(
            "SELECT * FROM fetch_attempts WHERE run_id=? AND company_id=? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [
            FetchAttempt(r["company_id"], SourceClass(r["source_class"]), r["url"],
                         r["outcome"], r["http_status"], r["document_count"])
            for r in rows
        ]


class HNPostRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, company_id: int, record: HNPostRecord) -> None:
        # A resume re-runs discovery; the post is the same post.
        p = record.parsed
        self.conn.execute(
            """INSERT OR IGNORE INTO run_hn_posts (run_id, company_id, item_id,
                   thread_title, posted_at, parse_method, status, company, domain,
                   ats_kind, ats_token, role, location, work_mode, employment_type, html)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, company_id, record.item_id, record.thread_title,
             record.posted_at.isoformat(), record.parse_method, record.status,
             p.company if p else None, p.domain if p else None,
             p.ats[0] if p and p.ats else None, p.ats[1] if p and p.ats else None,
             p.role if p else None, p.location if p else None,
             p.work_mode if p else None, p.employment_type if p else None,
             record.html))
        self.conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> HNPostRecord:
        parsed = None
        if row["domain"]:
            ats = ((row["ats_kind"], row["ats_token"])
                   if row["ats_kind"] and row["ats_token"] else None)
            parsed = ParsedPost(row["company"], row["domain"], ats, row["role"] or "",
                                row["location"] or "", row["work_mode"] or "unknown",
                                row["employment_type"] or "unknown")
        return HNPostRecord(int(row["item_id"]), row["thread_title"],
                            date.fromisoformat(row["posted_at"]), row["parse_method"],
                            row["status"], parsed, row["html"] or "")

    def for_run(self, run_id: int) -> list[tuple[int, HNPostRecord]]:
        """Every HN post this run recorded, as (company_id, record), in order."""
        rows = self.conn.execute(
            "SELECT * FROM run_hn_posts WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [(int(r["company_id"]), self._row_to_record(r)) for r in rows]

    def for_company(self, run_id: int, company_id: int) -> HNPostRecord | None:
        row = self.conn.execute(
            "SELECT * FROM run_hn_posts WHERE run_id = ? AND company_id = ? "
            "ORDER BY id LIMIT 1", (run_id, company_id)).fetchone()
        return self._row_to_record(row) if row is not None else None
