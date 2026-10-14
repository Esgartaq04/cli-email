# src/outreach/store/runs.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime

from outreach.store.dimensions import row_to_contact
from outreach.types import (CompanyInfo, Contact, FetchAttempt, HNPostRecord, Location,
                            ParsedPost, PostingRef, RunFilters, RunInfo, SourceClass, Spend)

# The schema version `start` stamps on a run. Runs from before it are told
# apart by their lower number: they have no kind, people list or filters.
RUN_SCHEMA_VERSION = 4


def _filters_to_json(filters: RunFilters) -> str:
    return json.dumps({
        "sectors": list(filters.sectors), "sizes": list(filters.sizes),
        "location": asdict(filters.location), "work_modes": list(filters.work_modes)})


def _filters_from_json(text: str) -> RunFilters:
    data = json.loads(text)
    return RunFilters(tuple(data["sectors"]), tuple(data["sizes"]),
                      Location(**data["location"]), tuple(data["work_modes"]))


class RunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start(self, kind: str, role: str, filters: RunFilters, people: str,
              started_at: datetime) -> int:
        # headcount_min/max are columns of the old size band, which a run no
        # longer has; they stay NOT NULL, so they are 0.
        cur = self.conn.execute(
            """INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max,
                                 started_at, status, schema_version, kind, people, filters)
               VALUES (?, ?, ?, 0, 0, ?, 'running', ?, ?, ?, ?)""",
            (role, "; ".join(filters.sectors), filters.location.country,
             started_at.isoformat(), RUN_SCHEMA_VERSION, kind, people,
             _filters_to_json(filters)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def info(self, run_id: int) -> RunInfo | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunInfo(
            int(row["id"]), row["kind"], row["role_title"], row["people"],
            _filters_from_json(row["filters"]) if row["filters"] else None,
            datetime.fromisoformat(row["started_at"]), int(row["schema_version"]),
            Spend(**json.loads(row["spend"])) if row["spend"] else None)

    def record_spend(self, run_id: int, spend: Spend) -> None:
        """Add `spend` to what the run has already used. The balance is the one
        read before the first purchase, so a later one never replaces it."""
        row = self.conn.execute("SELECT spend FROM runs WHERE id = ?", (run_id,)).fetchone()
        before = Spend(**json.loads(row["spend"])) if row and row["spend"] else Spend()
        total = Spend(
            before.balance_before if before.balance_before is not None else spend.balance_before,
            before.emails + spend.emails, before.verifications + spend.verifications,
            before.spent + spend.spent)
        self.conn.execute("UPDATE runs SET spend = ? WHERE id = ?",
                          (json.dumps(asdict(total)), run_id))
        self.conn.commit()

    def bought_before(self, company_id: int, people: str,
                      before_run_id: int | None) -> tuple[int, datetime] | None:
        """The latest earlier run that bought this company's contacts for the
        same people list, as (run id, when it started).

        Runs before v4 recorded no people list; their contacts were all the
        hiring list, so for that list a pre-v4 run whose contacts stage went
        `ok` counts too.
        """
        return self._earlier_contacts_run(
            company_id, people, before_run_id, ("bought",),
            legacy_statuses=("ok",) if people == "hiring" else ())

    def came_back_empty_before(self, company_id: int, people: str,
                               before_run_id: int | None) -> bool:
        """Whether an earlier run searched this company for the same people list
        and found no one to buy (which costs nothing)."""
        return self._earlier_contacts_run(
            company_id, people, before_run_id, ("empty", "no_matching_people")) is not None

    def _earlier_contacts_run(
            self, company_id: int, people: str, before_run_id: int | None,
            statuses: tuple[str, ...], legacy_statuses: tuple[str, ...] = (),
    ) -> tuple[int, datetime] | None:
        marks = ", ".join("?" * len(statuses))
        matches = [f"(r.people = ? AND rc.status IN ({marks}))"]
        params: list[object] = [company_id, people, *statuses]
        if legacy_statuses:
            legacy_marks = ", ".join("?" * len(legacy_statuses))
            matches.append(
                f"(r.schema_version < {RUN_SCHEMA_VERSION} AND rc.status IN ({legacy_marks}))")
            params.extend(legacy_statuses)
        sql = ("SELECT r.id, r.started_at FROM runs r JOIN run_companies rc ON rc.run_id = r.id "
               "WHERE rc.company_id = ? AND rc.stage = 'contacts' AND ("
               + " OR ".join(matches) + ")")
        if before_run_id is not None:
            sql += " AND r.id < ?"
            params.append(before_run_id)
        row = self.conn.execute(sql + " ORDER BY r.id DESC LIMIT 1", params).fetchone()
        return (int(row["id"]), datetime.fromisoformat(row["started_at"])) if row else None

    def schema_version(self, run_id: int) -> int:
        """The schema version the run was recorded under: 4 for every run
        `start` creates, lower for runs recorded before it."""
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


class CompanyInfoRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, company_id: int, info: CompanyInfo) -> None:
        # A resume re-runs discovery; the first answer for a company stands.
        self.conn.execute(
            """INSERT OR IGNORE INTO run_company_info (run_id, company_id, position,
                   source, sector, size, fit, guessed_domain)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, company_id, info.position, info.source, info.sector, info.size,
             info.fit, int(info.guessed_domain)))
        self.conn.commit()

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> CompanyInfo:
        return CompanyInfo(int(row["position"]), row["source"], row["sector"], row["size"],
                           row["fit"], bool(row["guessed_domain"]))

    def for_run(self, run_id: int) -> list[tuple[int, CompanyInfo]]:
        """Every company the run recorded, as (company_id, info), by position."""
        rows = self.conn.execute(
            "SELECT * FROM run_company_info WHERE run_id = ? ORDER BY position, id",
            (run_id,)).fetchall()
        return [(int(r["company_id"]), self._row_to_info(r)) for r in rows]

    def get(self, run_id: int, company_id: int) -> CompanyInfo | None:
        row = self.conn.execute(
            "SELECT * FROM run_company_info WHERE run_id = ? AND company_id = ?",
            (run_id, company_id)).fetchone()
        return self._row_to_info(row) if row else None


class RunContactRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, run_id: int, company_id: int, contact_id: int, position: int) -> None:
        # A resume buys nothing twice and lists the same person again.
        self.conn.execute(
            """INSERT OR IGNORE INTO run_contacts (run_id, company_id, contact_id, position)
               VALUES (?, ?, ?, ?)""",
            (run_id, company_id, contact_id, position))
        self.conn.commit()

    def for_company(self, run_id: int, company_id: int) -> list[Contact]:
        """The contacts this run listed for a company, in the order it ranked them."""
        rows = self.conn.execute(
            """SELECT c.* FROM run_contacts rc JOIN contacts c ON c.id = rc.contact_id
               WHERE rc.run_id = ? AND rc.company_id = ? ORDER BY rc.position, rc.id""",
            (run_id, company_id)).fetchall()
        return [row_to_contact(r) for r in rows]


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
