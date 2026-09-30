# Spec A — Startup-Stage Targeting & "What They're Building" Research

**Date:** 2026-09-30 · **Branch:** `feat/v1-pipeline` · **Supersedes parts of:** `docs/superpowers/specs/2026-09-21-outreach-pipeline-design.md`
**Follow-up (separate spec, not in scope here):** Spec B — auto-discovery from HN "Who is hiring?" threads feeding Greenhouse/Ashby/Lever tokens into this pipeline.

## Context

The V1 pipeline finds companies via hand-picked Greenhouse tokens, hunts for *engineering pain* (bottleneck themes, 2-source gate), and finds Hunter contacts. The goal has shifted: land a **job offer at a startup**. That means:

- Only companies **≤ 2,000 employees** (today headcount is *never populated* — `runner.py:95` upserts `None`; the 20–1,000 band is unenforced and a test asserts so).
- Know each company's **startup lifecycle stage**; favor **Growth & Establishment** and **Expansion**.
- Research **what they're building / working on**, not their problems. The report supports both a tailored email and a "build something for them" option, decided per company.
- Filter jobs by **work mode per run** (remote / hybrid / onsite), **full-time only**.
- **LinkedIn for every contact** where possible. (Existing bug: `contacts/hunter.py:74` reads `linkedin_url`; Hunter domain-search's field is `linkedin`, so profile links have always been empty.)

Decisions made with the user: stage via deterministic rules + quoted signals; non-target stages are **kept but ranked lower**; >2,000 is a hard drop; two-spec split (this = A).

## Flow

```
discover ─► filter postings ─► enrich company ─► research ─► classify stage ─► synthesize "building" ─► contacts ─► report
```

1. **discover** — Greenhouse (existing) + new **Ashby** and **Lever** `JobBoardSource` adapters; `config.toml` gains `ashby_tokens`, `lever_tokens`.
2. **filter postings** — `PostingRef` gains `work_mode: remote|hybrid|onsite|unknown` and `employment_type: full_time|other|unknown`. Ashby/Lever fill from structured fields; Greenhouse via pure `core/workmode.py` (location + title text). `--work-mode` (comma list, default all three) keeps matches; `unknown` is kept and flagged; non-full-time always dropped. Region (US) logic unchanged.
3. **enrich** — new `CompanyFactsProvider` protocol (`contacts/`-style, with fake) + Hunter Company Enrichment impl → `headcount` (band midpoint) + `headcount_band`, `founded_year`, `funding_rounds`, `tags`. Cached across runs, 90-day TTL. **>2,000 → dropped here**, before any Anthropic spend, listed under "Excluded — size". Unknown headcount → kept. `headcount_min` → 1, `headcount_max` → 2000. Domain-unconfirmed guard applies: enrich first fetches the company **homepage** (also reused for the GitHub link scan); if it doesn't return 200, enrichment is skipped (`skipped_domain_unconfirmed`), the company stays with stage `Unknown`, and contacts are skipped as today.
4. **research** — surfaces: careers, blog (existing), changelog **with per-entry dating**, new **press** (`/press`, `/news`, `/newsroom`), new **about**, **GitHub** (org resolved by scanning homepage for a `github.com/<org>` link; recently pushed public repos as dated first-party docs), developer docs (`/docs`, `/developers`, `/api`) for hooks only. Status page dropped.
5. **classify stage** — pure `core/stage.py`.
6. **synthesize** — gate per building theme, up to 3 passing findings per company.
7. **contacts** — quota spent in stage-rank order; LinkedIn resolution.
8. **report**.

## LLM boundary (still exactly three methods)

- `expand_titles` — unchanged.
- `extract_claims` — re-prompted; closed theme list replaced by two groups:
  - *Building:* `product-launch`, `active-build`, `platform-infra`, `ai-ml`, `public-api-sdk`, `open-source`, `new-market`
  - *Stage signals:* `funding-round`, `new-office`, `acquisition`, `headcount-statement`, `new-product-line`
  - Substring guard unchanged. Funding round name (Seed/Series A…/IPO) is parsed from the quote by **regex in core**, not by the model.
- `write_summary` — re-prompted: "what they're building and where it's heading," grounded only in given quotes.

## Stage classifier (`core/stage.py`, pure, thresholds in `[stage]` config)

First match wins; funding facts beat headcount; quoted funding signals count as rounds when Hunter has none.

| Stage | Rule |
|---|---|
| Maturity | Public/IPO or acquired, or no funding in ≥6 yrs and founded ≥12 yrs ago |
| Expansion | Latest round Series C+, **or** 500–2,000 employees, **or** Growth + a quoted expansion signal (`new-office`, `new-market`, `new-product-line`, `acquisition`) |
| Growth & Establishment | Latest round Series A–B, **or** 50–499 employees |
| Seed / Startup | Pre-seed/Seed, **or** <50 employees |
| Unknown | Nothing usable |

Returns `StageResult(stage, reasons: list[str])`; reasons render on the card (e.g. "Series B 2026-03 · ~350 employees · new office (quote)").
**Ordering:** Growth/Expansion → Unknown → Seed/Startup → Maturity; smaller headcount first within a group.

## Gate & findings

- Existing pure `core/gate.py` kept; config `min_independent_sources = 1`, `require_first_party = true`, `recency_days = 180`.
- ≥2 independent sources → **"corroborated"** badge on the finding.
- Up to 3 passing building themes per company (most corroborated first, theme name tiebreak).
- Stage-signal themes never become findings.

## Hooks for a build (deterministic, no LLM)

Per company: recently pushed public GitHub repos (name, description, pushed date) and any developer docs URLs that returned 200.

## Contacts & LinkedIn

- Ranking (`core/ranking.py`) unchanged; cap 3/company.
- `HunterProvider.find` reads `linkedin`; allowlist `http(s)` **and** host `linkedin.com`/`*.linkedin.com`. Fix `tests/contacts/test_hunter.py` fixtures that use `linkedin_url`.
- Fallback: Hunter **Email Finder** (`linkedin_url`) for ranked contacts still lacking a profile and not resolved in a prior run; config `linkedin_lookup = true`; counted in quota.
- Last resort: render-time "Search LinkedIn" link (people search, name + company), labeled as a search, never stored.
- **Quota:** `allocate_quota` becomes stage-rank ordered; enrichment budget checked before research. `--dry-run` prints per-bucket estimate. *Before implementing quota code, confirm from Hunter docs/account page which credit bucket Company Enrichment and Email Finder consume — no live calls.*

## Report

Card: name/domain · **stage badge + reasons** · headcount (source) · founded · latest round → matching postings (work-mode tag + link) → **What they're building** (≤3 themes: claim, summary, verbatim quotes w/ links, corroborated badge) → **Hooks for a build** → contacts (name, title, email status, LinkedIn or search link, why-this-person, contacted marker) → coverage log.
Sections: Growth & Expansion → Other stages → No building evidence (gate reason + coverage) → Excluded (size >2,000; no posting matching work mode) → Diagnostics (+ enrichment credits, stage counts).

## Data (additive, versioned via `PRAGMA user_version` in `store/db.py`)

- `companies` + `headcount_band`, `founded_year`, `funding_rounds` (JSON), `tags` (JSON), `facts_source`, `facts_fetched_at`
- `run_companies` + `stage`, `stage_reasons` (JSON)
- new `run_postings` (run, company, title, url, location, work_mode, employment_type)
- new `findings` (run, company, theme, claim, summary, corroborated, evidence_ids) — replaces `bottlenecks` for new runs; old rows untouched
- `SourceClass` + `PRESS`, `ABOUT`; `STATUS_PAGE` retained for old rows only
- `outreach report <id>` on a pre-migration run prints "pre-v2 run; see saved HTML"
- `contacts.contacted_at` history preserved

## Failure handling

Per-company isolation unchanged. Enrichment failure/quota → company kept, stage `Unknown`, reason in coverage log (never dropped for a fact we failed to fetch). Every new fetch/API call wrapped like existing surfaces.

## Critical files

- Modify: `src/outreach/pipeline/runner.py`, `src/outreach/types.py`, `src/outreach/config.py`, `config.toml`, `src/outreach/llm/anthropic_client.py` (+ `llm/fake.py`), `src/outreach/contacts/hunter.py`, `src/outreach/contacts/quota.py`, `src/outreach/sources/surfaces/standard.py`, `src/outreach/extraction/pubdate.py`, `src/outreach/store/schema.sql`, `store/db.py`, `store/dimensions.py`, `store/runs.py`, `src/outreach/cli.py` (`build_view`, `run` flags), `src/outreach/render/report.py` + `templates/report.html.j2`, `README.md`
- New: `core/stage.py`, `core/workmode.py`, `sources/jobboards/ashby.py`, `sources/jobboards/lever.py`, `contacts/facts.py` (protocol + Hunter + fake), `sources/surfaces/github.py` (org link scan + repos)
- Reuse: `core/gate.evaluate`, `core/clustering.cluster_by_theme`, `extraction/extract.extract_and_persist` (substring guard), `extraction/pubdate.parse_published_date`, `net/fetcher.Fetcher`, `hunter._safe_profile_url`, `runner._ingest` / `_record_attempt`

## Testing & verification

TDD. **No test touches a live API; I will not run the live pipeline** (user's standing rule).
- Truth tables: `core/stage.py` (each row + funding-beats-headcount + signal-promotes-to-Expansion + round regex), `core/workmode.py`, gate at `min=1` + corroborated badge.
- Recorded-fixture contract tests: Ashby, Lever board JSON; Hunter enrichment / domain-search (`linkedin`) / email-finder; changelog dating; homepage GitHub link scan.
- Pipeline tests with fakes: >2,000 dropped pre-research; enrichment failure keeps company as Unknown; quota spent in stage order; work-mode filter.
- End-to-end with `OUTREACH_FAKE_ADAPTERS=1`: report renders sections in order, stage badges, LinkedIn/search links.
- Verify: `pytest` green (fake adapters only). First live run is the user's.

## Next step

1. User reviews this spec.
2. Invoke `superpowers:writing-plans` for the implementation plan; user picks execution method.
