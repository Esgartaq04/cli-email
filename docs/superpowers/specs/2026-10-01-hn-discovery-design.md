# Spec B — Startup Discovery from HN "Who is hiring?"

**Date:** 2026-10-01 · **Branch:** `feat/hn-discovery`
**Status:** Implemented on `feat/hn-discovery` (2026-10-01). See **As built** at the end.
**Builds on:** `2026-09-30-startup-stage-targeting-design.md` (Spec A), including its **As built** section.

## Context

Spec A researches only the companies whose job-board tokens the user lists by hand in `config.toml`. That caps the funnel at whatever the user already knows about. Hacker News's monthly "Ask HN: Who is hiring?" thread is a free, fresh (≤ 30 days), startup-heavy list of ~300–400 hiring companies. Each post is written by the company. About half the posts link a Greenhouse, Ashby or Lever board. Most follow a `Company | Role | Location | REMOTE/ONSITE | Full-time` first line, and most state their size or funding ("Series A", "team of 80").

Goal: every `outreach run` also discovers companies from the latest thread and feeds them through Spec A's pipeline unchanged (work-mode filter, size cap, stage, findings, contacts), without spending more Hunter credits than the user can afford.

Decisions made with the user:
- Discovered companies flow in **automatically per run**, with no review step.
- Posts without an ATS link are kept, and **the post itself serves as the posting**.
- Only the **latest month's** thread is read.
- **A cap plus a free pre-rank** protects credits.
- Posts are parsed **deterministically first, with a capped and validated LLM fallback**. This deliberately changes the earlier "exactly three LLM methods" rule.

## Flow

```
HN thread ─► top-level posts ─► prefilter ─► parse ─► region / work-mode filter ─► pre-rank + cap ─► Spec A pipeline
```

1. **Fetch.**
   - `GET https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10` finds the newest story whose title starts with `Ask HN: Who is hiring?`. The same author also posts "Who wants to be hired?", which must be skipped.
   - `GET https://hn.algolia.com/api/v1/items/{id}` returns the whole tree. Only top-level children with non-empty `text` are kept; deleted and dead posts are dropped.
   - The thread is re-read every run, which costs two requests and needs no persistence.
   - `hn.algolia.com/robots.txt` is a 404, which the existing `Fetcher` treats as permissive.
2. **Prefilter (free).** A post continues only if one of the run's expanded role terms (`llm.expand_titles`, already called once per run) appears, case-insensitively, in its plain text.
3. **Deterministic parse** (pure, in `core/`) of the post's HTML-to-text first line and its `<a href>` links:
   - **Company:** the first `|` segment, with any "(domain)" or URL stripped.
   - **ATS:** the first link whose host is `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.ashbyhq.com` or `jobs.lever.co`. The token is the first path segment.
   - **Company domain:** the first link that is not HN, an ATS host, or a known non-company host (`github.com`, `linkedin.com`, `twitter.com`/`x.com`, `docs.google.com`, `forms.gle`, `calendly.com`, `notion.so`, `wellfound.com`, `ycombinator.com`). It is canonicalized with `canonical_domain`. A bare `example.com` written in the first line also counts.
   - **Role, location, work mode and employment:** taken from the remaining `|` segments. `core/workmode.classify_work_mode(location, role, text)` and `classify_employment(role, text)` do the classifying. A segment is treated as a location when it is not the company, not a work-mode tag, not an employment tag, and not a salary (contains `$`, `€`, `£`, or `k` after digits).
   - **Parsed** means the company and the domain are both present, plus at least one of location or work mode. Anything less means the post is unparsed.
4. **LLM fallback.** This is a new fourth `LLMClient` method, `parse_job_post(text: str) -> ParsedPost | None`.
   - It is called only for prefiltered posts that the deterministic parser left unparsed, and at most `[hn] llm_fallback_max` (default 20) times per run.
   - Its output is validated against the post. A field that fails validation drops the post, and the drop is counted:
     - `company` must appear in the post text, case-insensitively;
     - `domain` must be the host of one of the post's own links, or appear verbatim in the text;
     - `role` and `location` must appear in the text;
     - `work_mode` must be one of the known values and supported by a tag in the text.
   - The model never decides stage, size or evidence sufficiency.
5. **Filter.** The existing `matches_region(location, region)` applies to the parsed location; a missing location fails closed, as on Greenhouse. The existing `posting_matches(posting, work_modes)` applies the `--work-mode` and full-time rules.
6. **Pre-rank and cap** (before any paid call).
   - Free stage hints come from `core/stage.parse_round` and `parse_headcount_statement` over the post text. These are fed to `classify_stage(None, hint_signals, today, config.stage)`, with signals dated by the post's `created_at`.
   - Companies are ordered by `stage_sort_key(stage, headcount_hint)`, then by thread order.
   - A company whose `evidence` stage completed in any run that started within `[hn] recheck_days` (default 30) becomes `skipped_recent`.
   - The top `[hn] max_new_companies` (default 15) are kept; the rest become `skipped_cap`.
   - Config-token companies are not subject to the cap. A domain already found through a config token is deduped and not counted against the cap.
7. **Into Spec A.**
   - **With an ATS token:** that board is added to this run's board search, using the existing adapters. The resulting postings take the post's company domain in place of the guessed `token.com`. If the board yields no matching posting, the HN post is used as the posting instead.
   - **Without an ATS token:** the HN post is the posting: `PostingRef(company, domain, role, url=https://news.ycombinator.com/item?id=N, location, work_mode, employment_type)`.
   - **In every case**, the post text is ingested as a source document of the new class `hn_post`:
     - it is first-party, published by `news.ycombinator.com`, and dated by its `created_at`;
     - `extract_claims` gets stage signals and "what they're building" quotes from it, still substring-guarded;
     - `hn_post` counts as an independent source next to the company's own pages under the gate's `(source_class, publisher_domain)` key.

## Configuration and CLI

```toml
[hn]                       # optional; defaults shown
enabled = true
max_new_companies = 15
llm_fallback_max = 20
recheck_days = 30
```

- `outreach run --no-hn` skips HN discovery for one run.
- `--dry-run` fetches and parses deterministically only, and never calls the LLM fallback. It reports the thread title, posts read, posts matching the role, posts parsed, posts that would need the LLM fallback, posts surviving the filters, and companies kept after the cap. The credit estimate counts the kept HN companies together with the config-token companies.

## Data (migration to v3, additive)

- `run_hn_posts(run_id, company_id, item_id, posted_at, parse_method TEXT CHECK in ('parsed','llm'), status TEXT CHECK in ('kept','skipped_cap','skipped_recent'), UNIQUE(run_id, item_id))`.
- `SourceClass.HN_POST = "hn_post"`, added to the default `first_party_classes`.
- New `StageStatus` values `skipped_cap` and `skipped_recent` at the `discover` stage. Those companies are upserted, recorded and listed in the report, but get no research and spend nothing.
- Posts dropped before a company exists are only counted on `RunSummary`, with new fields `hn_posts_read`, `hn_role_matched`, `hn_parsed`, `hn_llm_parsed`, `hn_llm_rejected`, `hn_filtered`, `hn_skipped_recent` and `hn_skipped_cap`. A re-rendered report shows these as "not recorded".

## Report

- A card for a company found via HN shows "via HN Who is hiring? (<Month Year>)", linked to the post through the `http(s)`-only guard.
- The Excluded list gains "Not this run — over the HN cap" and "Researched in the last 30 days".
- The diagnostics footer gains the thread title and the HN funnel counts.

## Failure handling

- HN is additive. If the Algolia search or item fetch fails, or no "Who is hiring?" story is found, `summary.errors` gets an `hn:` entry and the run continues with the config tokens. A run with neither HN companies nor tokens still produces the usual empty-run report.
- An LLM fallback call that raises or returns invalid output drops only that post, and the drop is counted.
- Malformed HTML or JSON in a post drops only that post.
- The cap, the recent-research skip and the LLM fallback limit are all applied before any Hunter call. The LLM fallback runs only on posts that already match the role.

## Module layout

- `src/outreach/sources/hn.py` handles the thread lookup and tree fetch through `Fetcher`, and returns `list[HNPost(item_id, posted_at, html)]`.
- `src/outreach/core/hnparse.py` is pure. It holds `parse_post(html, ...) -> ParsedPost | None`, link and ATS extraction, the validator for LLM output, and pre-rank hint extraction.
- `src/outreach/llm/base.py`, `anthropic_client.py` and `fake.py` gain `parse_job_post`. `ParsedPost` lives in `types.py` as a frozen dataclass with these fields:
  - `company: str`, `domain: str`
  - `ats: tuple[str, str] | None`, as (board kind, token), where the kind is one of `greenhouse`, `ashby` or `lever`
  - `role: str`, `location: str`
  - `work_mode: WorkMode`, `employment_type: EmploymentType`

  Both the deterministic parser and the LLM fallback produce it.
- `src/outreach/pipeline/discovery.py` is new. It orchestrates fetch, prefilter, parse, fallback, filter and pre-rank/cap, and returns the HN companies with their postings, board tokens and `hn_post` documents. This keeps `runner.py` from growing further; at about 930 lines it is already large.
- `runner.py` calls discovery once, before the existing board search, and merges the results.
- The store gets the v3 migration and a `HNPostRepo`. The render layer gets the view and template additions. `cli.py` gets `--no-hn`, the dry run and wiring.

## Testing

All tests use fakes and fixtures; no live API.
- **Recorded fixtures:** an Algolia search JSON whose hits include a "Who wants to be hired?" story to skip, and a trimmed items-tree JSON with about 12 representative posts. The posts cover each ATS host, an `?ashby_jid` careers link, an own-site-only post, an email-only post, a non-US location, a non-pipe format, a deleted post and HTML entities.
- **Parser truth tables** for company, domain, ATS, role, location, work mode and employment, including the rejection of salary segments and non-company hosts.
- **Validator:** a fake LLM that invents a domain or company, or a role not in the text, is rejected. Valid output is accepted. The fallback limit is respected.
- **Pre-rank and cap:** "Series B" and "team of 120" rank ahead of unknown and seed; a company researched within 30 days is skipped; a config-token domain is not counted against the cap.
- **Runner integration:**
  - an HN company with an ATS board uses the post's domain;
  - an HN-only company gets a posting and an `hn_post` document that the gate accepts as first-party;
  - an Algolia outage leaves the config-token companies intact;
  - `--no-hn` skips discovery entirely.
- **Migration** to v3 from v2 and from v1, with contact history intact.
- **CLI:** the dry-run output without any LLM fallback call, and `--no-hn`.

## Out of scope

- "Who wants to be hired?" threads, and threads older than the latest month.
- Following careers pages to find an embedded board (`?ashby_jid=`); those posts use the post-as-posting path.
- Other discovery sources (YC directory, Wellfound).
- Remembering failed LinkedIn lookups (Spec A deferred gap).

## As built

Decisions taken during implementation and the whole-branch review, recorded here because the execution ledger is deleted after the work lands.

**Departures from the text above**

- **US locations.** `region._US_MARKERS` gained common US hubs written without a state code (NYC, New York, SF, Bay Area, Seattle, Boston, Austin, Los Angeles, Chicago, Denver, Atlanta, Miami, DC) and "us-only"/"remote, us". A location that is exactly "US"/"USA"/"United States" matches; "US" inside a phrase ("US hours overlap", "LATAM (US timezones)") does not. This also applies to Greenhouse/Ashby/Lever postings.
- **Work-mode tags.** A first-line segment that is only a work-mode tag ("| REMOTE |") is read in the loose location slot of `classify_work_mode`; an employment-only segment ("| Contract |") is passed as the declared employment type.
- **Company domain.** A domain written in the company segment ("Acme (acme.io)", "Modash.io") wins; otherwise the first link whose host is not in a non-company *family* (ATS and job boards incl. Workable/Dover/WaaS and EU Greenhouse, forms/docs/video, social, press, webmail, shorteners — matched as the host or any subdomain). Tech names elsewhere in the first line ("ASP.NET Engineer") are never domains.
- **LLM output validation** additionally rejects non-company domains, a domain seen in the text only inside an email address or as a prefix of a longer one ("acme.co" vs "acme.com"), and empty or >120-character company/role; the board always comes from the post's links, never the model; an unsupported "other" employment is downgraded to "unknown".
- **Role prefilter** matches a pipe-format post on its first line; the body counts only for free-text posts and first lines that name generic openings ("roles", "positions", "openings").
- **Resume** never re-selects: `run_hn_posts` stores each post's parsed fields and HTML, and a resumed run rebuilds its HN selection from them without calling HN or the LLM.
- **Recheck window** is measured from the run date (`ctx.today`), like the facts TTL.
- A board posting "covers" an HN company only if it also passes the work-mode filter; otherwise the HN post is added as its posting.
- `run_hn_posts` also stores the thread title, for the "via HN Who is hiring? (Month Year)" link.

**Known gaps, deferred**

- The role or company segment can be a non-role ("acme.com", "Series A", or "(YC W23)" kept in the company name), and an HN company name overwrites the stored name on upsert (including skipped companies).
- Company/role checks in the LLM validator are plain substrings with no minimum length.
- A config-token company whose board posting fails the work-mode filter does not fall back to its HN post, though `run_hn_posts` records it as kept.
- A config token `acme` (guessed `acme.com`) and an HN post for `jobs.ashbyhq.com/acme` whose site is `acme.io` become two companies.
- The dry-run HN line does not show how many posts survived the region/work-mode filters.
- Greenhouse embed links (`boards.greenhouse.io/embed/job_app?for=acme`) yield token `embed` (falls back to the post).
- The duplicate-company check runs after the LLM fallback, so a company's second unparsed post can spend a fallback call; fake-adapter runs always log "hn: no Who is hiring? thread found".
- Multi-region locations with a non-US marker ("Remote (US/Canada)") are filtered out (Spec A's strict US policy).
