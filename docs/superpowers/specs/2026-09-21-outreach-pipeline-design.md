# Outreach Research Pipeline — V1 Design

**Date:** 2026-09-21
**Status:** Approved for planning
**Author:** Esteven (with Claude)

---

## 1. Purpose

Reach people who can hire outside the conventional hiring funnel, by arriving with a specific, evidenced observation about their business rather than a generic introduction.

The full strategy is: find a company, find a person there with real hiring authority, find a genuine problem they have said out loud in public, build a small tool that addresses it, and send it to them for free. This spec covers **only the research half** — everything up to and including a report the user reads. Building the tool and writing the email are done by hand in V1.

This is a personal tool. One user, one machine, run roughly weekly.

## 2. Scope

### In scope for V1

- CLI-invoked pipeline: job title + sector + headcount band in, one HTML report out
- Company discovery from active job postings for adjacent roles
- Contact identification and ranking, with email enrichment and verification
- Evidence collection from public first- and third-party sources
- A deterministic gate that decides whether a bottleneck is real enough to ship
- Persistent storage so weekly runs accumulate rather than repeat
- A report that makes both findings and failures visible

### Explicitly out of scope for V1

| Deferred | Why |
| --- | --- |
| Per-contact resume tailoring | Already exists in the job-tracker project; integrate only once research quality is proven |
| Outreach email generation | Depends on research quality that V1 exists to validate |
| The "no bottleneck → offer to help" email | Same |
| Sending, deliverability, follow-up sequencing | Manual sending keeps a human check on every message at this volume |
| Web UI, multi-user, hosting | One user, weekly runs — a file and a CLI are sufficient |
| Suggesting what tool to build | Speculation dressed as analysis; this is the user's judgment call |

Each deferred item gets its own spec when its time comes.

## 3. Success criteria

**Primary:** reading a report cold, the user can write an email to at least one person that quotes something specific and true about their business, without going back to a search engine.

**Secondary:**

- A run's failures are legible — a company with no findings is distinguishable from a company whose sources failed to fetch
- No enrichment credit is spent twice on the same person across runs
- The gate's behavior can be changed and verified without a live network call

## 4. Decisions already settled

| Decision | Choice |
| --- | --- |
| Users | Single user, standalone repo, no auth or multi-tenancy |
| V1 boundary | Research only; ends at a report |
| Throughput | 5–10 companies per run, roughly weekly; depth over coverage |
| Company discovery | Companies actively posting roles adjacent to the target title |
| Headcount band | 20–1,000, configurable per run; smaller sorted first |
| Contact emails | One enrichment provider behind a swappable adapter, free tier; unverified addresses are never shipped as addresses |
| Evidence bar | Two independent sources, at least one first-party, at least one recent |
| Architecture | Local CLI, SQLite, static HTML report |

### Why a CLI and not n8n or a web app

The project's uncertainty is entirely in whether the evidence gate produces findings worth staking an email on — not in how runs are triggered or where data lives. A CLI reaches that answer fastest and makes the gate unit-testable. n8n would trap the gate's logic in GUI nodes with no test suite. A web app triples the build for one weekly user without making a single bottleneck more accurate.

The pipeline is written as a library with a thin CLI on top, so n8n can later shell out to it and a web app can import it. Neither is true in reverse.

## 5. Architecture

Six stages. Each reads the previous stage's rows from SQLite and writes its own. Every stage is checkpointed per company, so a rate limit or crash resumes rather than restarting and re-spending.

1. **discover** — role title + sector + size band + target region in; companies currently posting adjacent roles out, deduped by canonical domain. Region defaults to US and is enforced here, at the only stage that introduces new companies (see §14)
2. **profile** — per company, resolve the domain and collect *surfaces*: careers board, engineering blog, changelog or release notes, GitHub org, status page, recent funding or press
3. **contacts** — identify candidate people, rank by plausible hiring authority, attempt enrichment and verification
4. **evidence** — extract candidate claims from stored documents, each with a verbatim quote, URL, fetch timestamp, source class, and a short theme label
5. **gate + synthesize** — cluster claims by theme, apply the gate, write summaries for survivors only

**Clustering is deliberately dumb.** `extract_claims` returns a short theme label per claim (for example `reconciliation-throughput`); `core/clustering.py` normalizes the label — lowercase, trimmed, hyphenated — and groups by exact match. No embeddings, no similarity threshold, no second model call. The cost of this is occasional near-duplicate clusters that each fail the gate separately; the benefit is that clustering is a pure function with no tunable magic number, and a missed cluster shows up as a no-bottleneck company rather than a wrong bottleneck. Revisit only if real runs show it splitting genuine findings.
6. **report** — render one self-contained HTML file

### The LLM boundary

**The LLM never operates the gate.** It has exactly three jobs:

- `expand_titles` — turn a role title into adjacent search terms
- `extract_claims` — pull candidate claims and their verbatim quotes from a fetched document
- `write_summary` — write prose for a bottleneck that has *already passed the gate*

Whether a bottleneck passes is plain Python counting evidence rows. A model asked to judge sufficiency will find sufficiency, because it wants to be helpful. Deterministic code will not. This separation is also what makes the risky logic testable: the gate is fed fixture rows and asserted against, with no HTTP and no model call.

If a fourth LLM method is ever proposed, that is a design conversation, not a convenience.

## 6. Data model

Two layers, because the same companies recur across weekly runs.

**Persistent dimension tables**, keyed by natural identity:

- `companies` — keyed by canonical domain; name, headcount estimate, headcount source
- `contacts` — keyed by company + full name; title, profile URL, email, email status, provider, `looked_up_at`, `contacted_at`
- `source_documents` — keyed by URL + content hash; source class, publisher domain, fetch timestamp, HTTP status, path to stored body

**Per-run tables:**

- `runs` — role title, sector, size band, timestamps, status, config snapshot
- `run_companies` — join table carrying per-stage status and error text
- `evidence_items` — company, source document, normalized claim, verbatim quote, source class, publisher domain, cluster key
- `bottlenecks` — company, claim, summary, verdict, contributing evidence IDs
- `fetch_attempts` — company, surface, URL, outcome (used by the report's coverage log)

This split means a person resolved three weeks ago costs no new credit, a previously researched company arrives with prior findings instead of a cold re-fetch, and `contacts.contacted_at` lets the report gray out people already emailed — which matters from run one, because nothing is worse than a second cold email to someone who ignored the first.

**Raw documents live on disk** at `data/cache/<content_hash>`, with the path in the database. SQLite stays small, and those files double as test fixtures — the gate's test suite reads the same bytes a real run saw.

### The substring guard

Every `evidence_item` carries a verbatim `quote`, and the extraction stage asserts that quote appears as a literal substring of the stored document **before the row is allowed to persist**. A model that paraphrases, embellishes, or invents has its claim dropped at insert time. No prompt engineering required; it is a three-line check.

Two independent defenses result: quotes must be real (substring assert), and bottlenecks need two independent real quotes (the gate). Either alone leaks. Together they are hard to fool.

Rejected quotes are **expected output, not errors**. The count goes in diagnostics; a spike means the extraction prompt has drifted.

## 7. Module layout

One rule does the heavy lifting: **`core/` contains pure functions and imports nothing that touches the network or disk.** Everything I/O-shaped sits behind a protocol with a fake for tests.

```
core/        gate.py, ranking.py, clustering.py, dedupe.py   ← pure
sources/     jobboards/          JobBoardSource protocol
             evidence/           CareersBoard, EngBlog, Changelog,
                                 GitHubOrg, StatusPage, News
contacts/    provider protocol: find() + verify()
llm/         three typed calls, nothing else
store/       SQLite repositories
render/      report
cli.py
```

## 8. Contact ranking

Deterministic scoring, not model judgment.

- **Title tier** — tier 1: founder, CTO, VP Engineering, Head of X. tier 2: Director, Senior Manager. tier 3: Engineering Manager, Team Lead. tier 4: Staff or Senior engineer.
- **Size factor** — multiplies tier-1 scores upward sharply at small headcounts. Authority to hire outside the funnel is mostly a function of company size, not seniority: at a company with a recruiting org, the best a senior manager can offer is a referral into the same pipeline.
- **Relevance** — whether the person's org matches the target role, by title keywords.

Recruiters and talent partners are excluded by keyword. They *are* the funnel.

**Cap at three contacts per company**, so a bad run cannot drain the enrichment quota. The score's components ship into the report as a one-line "why this person," which also makes a wrong ranking visible.

Verified emails ship as emails. Unverified ones ship as a named person with title and profile link, flagged for manual lookup — never as a guess. Bounces damage a sending domain, and the user only has one.

## 9. The evidence gate

A single pure function over evidence rows. A cluster passes only if **all three** hold:

1. **Two or more independent sources.** Independent means distinct `source_class`, or the same class from different publisher domains. Three job postings from one company's board count as one source — this is the case that would otherwise wave everything through.
2. **At least one first-party source** — their posting, blog, changelog, GitHub, or status page. Without this, the user can end up emailing someone about a problem a journalist invented for them.
3. **At least one source within the recency window**, default 180 days. A 2019 blog post is history, not a bottleneck.

Fail any one and the company routes to the no-bottleneck branch. Thresholds live in config, not code.

## 10. The report

One self-contained HTML file per run at `reports/YYYY-MM-DD-<role>-<sector>.html`. Old reports are kept. No server, no framework.

**Organizing principle: quotes are the deliverable, prose is navigation.** What gets pasted into an email is a verbatim line from their changelog, not the model's summary of it. The layout puts quotes in the visual foreground and the generated summary in a smaller supporting role. If the user finds themselves reading summaries and ignoring quotes, the gate is doing nothing — and that should be noticeable.

**Evidenced companies first,** sorted by headcount ascending. Each card carries:

- Name, domain, headcount with its source, and the posting that surfaced them
- The bottleneck: one-sentence claim, short summary, then the evidence list — each item a verbatim quote, source-class badge, link, and date
- One to three contacts: name, title, email or "not found," profile link, the one-line why-this-person, and a marker if already contacted

**The no-bottleneck section sits below** and carries what the happy path does not: which surfaces were attempted and what happened to each, plus which gate condition failed. A company with nothing found because their GitHub org 404'd looks identical to a company with genuinely nothing to find, unless the report shows the difference. This is the primary signal for whether the pipeline is healthy or quietly rotting.

**A diagnostics footer** closes the file: per-stage counts, quotes rejected, sources that errored, cache hits, enrichment credits consumed and remaining, wall clock. Silent degradation is the failure mode that would waste the most time.

A rendered sample layout with fictional data sits beside this spec at `docs/superpowers/specs/2026-09-21-sample-run-report.html`.

## 11. Failure handling

**Nothing aborts a run.** Stage status is per company; one bad domain cannot cost the other eight. Failures are recorded as rows, not log lines, which is what lets the report display them.

- **Fetch failures** become `fetch_attempts` rows with status codes, surfaced in the per-company coverage log
- **Quota exhaustion never truncates silently.** Before the contacts stage, remaining credits are compared against queued companies; if short, companies are processed in rank order and the remainder marked `skipped — quota` in both report and diagnostics
- **LLM extraction is per document**, retried once, then marked failed. Malformed output is discarded and counted
- **Network politeness is a requirement, not a nicety** — per-domain rate limiting, honest user agent, robots.txt respected. These are companies the user intends to email
- **Fail fast on config** — a missing API key stops the run at startup, before any spend
- `--resume <run_id>` continues from checkpoints; `--dry-run` runs discovery and profiling only and reports what a full run would cost in credits

## 12. Testing strategy

**TDD, gate first** — it is the component the entire design exists to protect. Its truth table is the first thing written:

| Evidence | Verdict |
| --- | --- |
| Two sources, same class, same publisher | fail |
| Two independent sources, all older than the window | fail |
| Two independent sources, fresh, none first-party | fail |
| Two independent sources, fresh, one first-party | pass |
| One first-party source, fresh | fail |

- `core/` gets real unit tests — gate, ranking, clustering, dedupe — since it is pure
- **Extraction** is tested against saved real documents in `tests/fixtures/` with a recorded-response fake LLM, asserting both extracted claims and the substring guard
- **Each adapter** has a shared contract test that its fake and its real implementation both satisfy
- **One end-to-end test** runs with every adapter faked and asserts a report renders with the expected sections
- **No test touches a live API**

The test that matters most is not automated: after run one, the user reads the report and checks the success criterion in §3.

## 13. Configuration

API keys in `.env`, never committed. A checked-in config file holds gate thresholds, the source-class list, the first-party classification of each source class, the recency window, the headcount band, the target region, and per-company contact caps — so tuning the gate never requires touching code.

## 14. Legal and practical constraints

- Contact data comes from legitimate enrichment provider APIs. No scraping of sites that forbid it, and no credential-based access to anything.
- Cold outreach to a work address is lawful in the US under CAN-SPAM when its rules are followed. Contacts in the EU bring GDPR into scope; V1 targets US companies.
- No email is sent by this system in V1. Every message is written and sent by the user, by hand.
- Unverified addresses are never presented as addresses.

## 15. Next step

An implementation plan produced by the writing-plans skill. No product code is written before that plan exists.
