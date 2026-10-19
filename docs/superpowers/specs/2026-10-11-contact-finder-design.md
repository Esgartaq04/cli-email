# Contact Finder for Cold Outreach — Design

**Date:** 2026-10-11 · **Builds on:** Spec A (`2026-09-30-startup-stage-targeting-design.md`) and Spec B (`2026-10-01-hn-discovery-design.md`), including both "As built" sections
**Replaces:** the research half of V1 and Spec A (quotes, evidence gate, findings, startup stages, hooks), and Spec B's stage-based pre-rank
**Status:** Implemented on `feat/contact-finder` (2026-10-18). See **As built** at the end for where the code departs from the text below.

## Context: what this is for and why now

The app was built to get around the hiring funnel. It researched what startups are building (quotes, an evidence gate, startup stages) and then found people to email. The user now wants a **contact finder for cold email** that two people use for two different things:

- **The user (job search):** reach hiring managers at companies that are hiring for the user's role.
- **The user's friend (student org):** reach managers at companies to ask whether they will **sponsor** the org (money, swag, prizes) or **speak or teach** (talks, workshops, mentoring).

Both pick companies by **sector**, **size** and, optionally, **location**. Each comes away with a report listing the right people and their confirmed email addresses. They write and send every email by hand, and the tool never sends anything.

Why now:

- The research is the slow and costly half, and the user doesn't need it ("name, website, size and the contacts are enough").
- `--sector` has never filtered anything. It is only stored on the run and printed in the report's title and filename.
- Hunter switched to per-email credits. Hunter's free company search (Discover) makes it possible to find companies by sector, size and location without starting from a job posting, which is what the friend needs.

**Success looks like this:**

- One run produces a report page of companies that match the filters, each listing up to 3 of the right people with confirmed emails.
- The cost is shown before any credit is spent, and it is accurate.
- Nobody already emailed shows up as new, and nothing is bought twice.
- The friend can install the tool and run it alone with only a Hunter key.

## Goals

1. Two run types:
   - `outreach jobs` finds companies hiring for a role.
   - `outreach companies` finds companies by sector, size and location, whether or not they are hiring.
2. Real sector, size and optional location filters, using Hunter's vocabulary.
3. Named, editable "people lists" that decide who gets looked up: `hiring` for jobs, `sponsors` for the friend.
4. A slimmed-down HTML report: one card per company, with people and confirmed emails.
5. An emailed list (`add`, `list`, `remove`) so nobody is listed or paid for twice.
6. Credit-frugal spending under Hunter's per-email pricing, with an accurate dry run.
7. Docs rewritten for the new purpose and for a first-time user (the friend).

## Non-goals

- Writing, drafting or sending email; follow-ups; reply tracking.
- Company research of any kind: quotes, findings, evidence gate, startup stages, "Hooks for a build".
- Scraping email addresses from websites. Hunter is the only email source.
- A shared or multi-user install. Each person has their own copy, keys, config and database.
- CSV or spreadsheet output. The user chose the report page.
- Paid LinkedIn profile lookups.

## Anti-goals (a failure even if everything else works)

- Showing an unconfirmed address as if it were good. Bounces damage the sender's only domain.
- Paying for people who don't appear on the list (for example, revealing 10 emails to keep 3), or paying again for a company or person already listed or emailed.
- A dry run that understates the cost.
- A silent empty run. Too-narrow filters, an unknown sector name, running out of credits and an unconfirmed domain must each say so.
- Listing someone already emailed as a fresh contact.
- The Hunter key leaking into error text or the report. It travels in the query string, so keep the `HunterAPIError` discipline in `contacts/hunter.py`.
- The friend needing an Anthropic key, or the user's setup, to do a company run.

## Constraints

- **The user's standing rules.**
  - Never read `.env`.
  - Never run a live `outreach` command or make a real Hunter or Anthropic call.
  - Every test uses fakes and recorded fixtures.
  - Any test that goes through `outreach.cli.build_context` keeps `load_dotenv` patched out. The autouse fixture in `tests/test_cli.py` does this today.
- **Hunter credits** (help center, updated 2026-08-20). The free plan is 50 credits a month.

  | Call | Cost |
  | --- | --- |
  | Domain Search | 1 credit per email revealed |
  | Email Finder | 1 credit per email revealed |
  | Email Verifier | 0.5 credit |
  | Company Enrichment | 0.2 credit |
  | Discover | Free |
  | Email Count | Free |

- **Hunter free-plan limits.** Domain Search allows `limit + offset ≤ 10`. Discover returns at most 100 companies per call, and its `limit`/`offset` are premium-only.
- **Hunter Discover filters** (POST `/v2/discover`; rate limit 5 per second, 50 per minute):
  - `industry` include/exclude, using values from `hunter.io/files/industries.json` (about 490 names).
  - `headcount` bands: `1-10`, `11-50`, `51-200`, `201-500`, `501-1000`, `1001-5000`, `5001-10000`, `10001+`.
  - `headquarters_location` include/exclude: `country` (ISO alpha-2), `state` (US only), `city`.
  - `job_openings`: `hiring`, `job_title` include/exclude, `posted_date`.
  - Also `keywords` and `company_type`.
- **Hunter Domain Search filters:** `department`, `seniority`, `job_titles`, `type`, `verification_status` and `limit`.
- **Region posture.** No location means US-only, as today (CAN-SPAM/GDPR posture from V1 §14).
- **Politeness.** HN and job-board fetching keeps using `net/fetcher.Fetcher`, which handles robots.txt, the per-host rate limit and an honest user agent.
- **Repo conventions.** Work happens on a feature branch off `main` and goes into a PR with no description, which the user merges. Docs and memory are brought up to date after a merge.

## Decided with the user

1. **Output:** one HTML report page per run, with a card per company. No CSV.
2. **Commands:** two of them.
   - `outreach jobs` requires `--role`.
   - `outreach companies` requires `--sector`.
3. **Research** is dropped entirely.
4. **Emails** come from Hunter only.
5. **Sector:**
   - The input is Hunter's exact category names.
   - `outreach sectors [word]` lists them, along with the size bands.
6. **Job-run company sources:** board tokens and HN "Who is hiring?" stay. Hunter's "hiring for this title" search is added alongside them.
7. **Location:**
   - It is optional. When omitted, the run is US-only.
   - On a company run it means the company's headquarters.
   - On a job run it means where the job is. A remote job open to US applicants passes whatever city or state was asked for.
8. **Size:** Hunter's bands, chosen per run. They replace the 2,000-employee cap and the startup stages.
9. **People lists:** `hiring` and `sponsors` ship in `config.toml`.
   - Users can edit them and add their own.
   - `--people` picks one, and each command's default is set in config.
   - `hiring`: founders, CTO, VP and head of engineering, directors, engineering managers and tech leads, most senior first. Recruiters are excluded.
   - `sponsors`: partnerships, marketing, community, events, developer relations, and campus or university recruiting, plus founders and engineering leaders for talks.
10. **Emailed list:**
    - `outreach emailed add <address>... [--on DATE]` marks people as emailed.
    - `outreach emailed list` shows who has been marked.
    - `outreach emailed remove <address>...` takes people off the list.
    - Marked people show "emailed <date>" on every later report and are never paid for again.
    - An address the tool never found can also be added.
11. **Earlier lists:** a company already on an earlier list isn't bought again. It gets one line pointing to that run, so each run moves on to new companies.
12. **Unconfirmed addresses:** these are never shown as addresses. The person is still listed, marked "address not confirmed", with a LinkedIn search link.
13. **Spending:**
    - Hunter is asked only for people on the chosen list, and only for as many as the report keeps (default 3 per company).
    - The free Email Count is checked first, so a company with no matching people is dropped for nothing.
    - An address that Domain Search already marks as confirmed isn't paid for again. Any other address costs 0.5 to verify.
    - A run stops at 10 companies by default and stops buying when credits run out.
14. **LinkedIn:** paid lookups are removed. A LinkedIn link is shown when Hunter supplies one; otherwise the free "Search LinkedIn" link is shown.
15. **AI calls:** company runs make none, so the friend needs only `HUNTER_API_KEY`. Job runs keep the two AI calls they already make: `expand_titles`, and the HN `parse_job_post` fallback.
16. **Docs:**
    - The README is rewritten around the two uses.
    - A new getting-started guide covers the friend's path from install to first run.
    - A costs section explains Hunter's per-email pricing and the free plan.
    - `config.toml`, `.env.example` and all `--help` text are updated.
    - The three older specs get a "superseded" status line.
17. **Old data:**
    - Old runs stay in the database untouched.
    - `outreach report <old run>` points to the run's saved HTML file and exits non-zero.
18. **Companies already emailed:** if any address at a company is on the emailed list, later runs skip that company before searching it.
    - The skip costs nothing, and the company is listed with the reason.
    - This holds even if the address was added by hand. Hunter can't be told to leave a particular person out of a search, so this is the only way to guarantee nobody emailed is ever paid for again.

## Calls made during design (reviewed by the user at approval)

- **People-list key.** "Already on an earlier list" means contacts were bought for that company in an earlier run **using the same people list**. A company where no one was found (which cost nothing) can come back in later runs.
- **Unknown sector.** On a job run, a token or HN company whose sector or size Hunter doesn't know is **kept with a "sector unknown" label** and ordered after known matches. This follows the existing rule that a company is dropped only when it is *known* not to fit.
- **Spending order.** Companies with real postings (HN, tokens) come first in source order, then Hunter's hiring finds in Hunter's order.
- **HN settings.** `[hn] recheck_days` is folded into the earlier-list rule and removed. `[hn] max_new_companies` (15) stays as the bound on how many HN companies a run considers, now taken in thread order.
- **Sector list copy.** The sector list is a copy of Hunter's `industries.json` bundled with the package, so `outreach sectors` and validation work offline and for free. The docs say how to refresh it.
- **`config.toml`** stays tracked in the repo and carries both people lists. The friend can use it as is, because company runs don't read board tokens.
- **Old config sections.** `[gate]`, `[stage]` and the research keys in `[discovery]` are ignored with a single warning rather than failing.
- **Title priority decides who is bought, not only the order on the card.**
  - The list's `titles` are searched in priority tiers through Domain Search's `job_titles` filter. Each tier is one call with `limit` set to the slots still open, and the search stops once `contacts_per_company` people are found.
  - Credits are charged per email revealed, so tiering costs extra calls but no extra credits.
  - Overlapping titles (a "Founder & CTO") belong in the same tier, so one person isn't revealed twice.
  - **Fallback**, if Hunter's docs show `job_titles` can't be used this way: one search by `departments` + `seniority`, with titles used only for ordering. People are shown even if their title matches none of the list's titles, because they were paid for. This limitation goes in the docs.
  - Anyone matching `exclude_titles` is dropped even though they were paid for. That is rare, and the user explicitly wants recruiters off the `hiring` list.
- **Confirmation age.** A `valid` status from Domain Search counts as confirmed only if Hunter verified it within the last 90 days (`[hunter] verified_max_age_days = 90`). Older addresses are re-verified for 0.5 credit, because bounces are the first anti-goal.
- **The run cap counts companies where at least one person was bought.**
  - A company whose search comes back empty (cost nothing) doesn't use a slot, and the next company fills it.
  - Companies that came back empty are remembered per people list and tried after fresh companies in later runs. Otherwise Discover's fixed free-plan order would put the same empty companies at the front of every run.
- **Cross-list re-reveals.** The earlier-list rule is per people list, so one person running both lists could rarely pay to re-reveal someone bought under the other list. This was accepted as a bounded edge case, since each person normally uses one list.
- **Old contacts.** Contacts bought in pre-v4 runs count as earlier `hiring` lists. The user's database holds 6 verified contacts at 2 companies from v1 runs.

## How it works

### Commands

```
outreach jobs --role "Backend Engineer" [--sector NAME]... [--size BAND]... [--location TEXT]
              [--work-mode remote,hybrid,onsite] [--people hiring] [--no-hn] [--dry-run] [--resume RUN_ID]
outreach companies --sector NAME [--sector NAME]... [--size BAND]... [--location TEXT]
                   [--people sponsors] [--dry-run] [--resume RUN_ID]
outreach sectors [WORD]            # Hunter category names containing WORD, plus the size bands
outreach emailed add ADDRESS... [--on YYYY-MM-DD]
outreach emailed list
outreach emailed remove ADDRESS...
outreach report RUN_ID
```

- **Repeatable flags.** `--sector` and `--size` are repeated options, not comma lists, because some Hunter names contain commas ("Health, Wellness and Fitness").
- **Validate before anything else.** All argument validation happens before keys are checked or any network call is made, and a bad argument exits with status 2. This covers an unknown sector (with near-match suggestions and a pointer to `outreach sectors`), a bad size band, an unknown people list, a location that can't be parsed, and a bad work mode.
- **Keys:**
  - `jobs` requires `ANTHROPIC_API_KEY` and `HUNTER_API_KEY`.
  - `companies` requires only `HUNTER_API_KEY`.
  - `sectors` and `emailed` require no keys.

### Filters

- **Sector:**
  - The value must match a name in the bundled list, case-insensitively. It is normalized to Hunter's spelling.
  - On `companies` the flag is required; on `jobs` it is optional.
  - With no sector or size given, a job run does no company checks at all, so no check credits are spent.
- **Size:** zero or more of the 8 bands. Omitted means any size.
- **Location parsing:**
  - A two-letter value is always a US state (`TX`, and `CA` means California).
  - `City, ST` is a US city.
  - A country is given by name (`Canada`, `United Kingdom`), optionally preceded by a city (`Toronto, Canada`).
  - `US` or `United States` means the whole US.
  - Omitted means the US.
  - The dry run prints how the value was read.
  - The result is a `Location(country, state, city)` value that maps directly to Hunter's `headquarters_location`.
- **Postings on job runs:**
  - A posting passes when its location falls in the requested place.
  - A remote posting passes when it is open to the requested country.
  - The current US-only behavior of `sources/jobboards/region.matches_region` must stay exactly as it is when no location is given; its existing tests keep passing.

### Company runs (`outreach companies`)

1. Validate, load config, and require `HUNTER_API_KEY`.
2. **Find companies.** Call Hunter Discover with the industries, headcount bands and headquarters location. This is free and returns at most 100 companies.
3. **Skip companies already handled.** Drop any company that is either:
   - already on an earlier list for this people list, or
   - home to someone on the emailed list.

   Both are listed under "Already on an earlier list", with the run id and date or with the emailed address and date.
4. **Skip companies with nobody to find.** Use the free Email Count for the people list's departments. A company with zero matching people becomes "No one found (cost nothing)".
5. **Order and cap.** Fresh companies go first in Hunter's order, followed by companies that came back empty for this people list in earlier runs. Contacts are bought down that order until `max_companies` (default 10) companies have at least one person bought. The rest are "Left out: over this run's cap".
6. Find contacts (below), then write the report.

### Job runs (`outreach jobs`)

1. Validate, load config, and require both keys.
2. **Expand the role.** `expand_titles(role)` produces search terms (one AI call).
3. **Gather companies from three sources:**
   - **Board tokens.** Greenhouse, Ashby and Lever, as today, with postings filtered by location, work mode and full-time.
   - **HN "Who is hiring?".** As today: role prefilter, rules parse, LLM fallback (at most `llm_fallback_max`), and filters. The stage pre-rank is removed and companies are taken in thread order up to `max_new_companies`. A resume still restores the run's HN selection from `run_hn_posts`.
   - **Hunter Discover with `job_openings`** (hiring, `job_title` from the role terms) plus the sector, size and location filters. This is free. Hunter can only filter on headquarters location, so the report shows the headquarters for these companies.
4. **Merge.** Merge by canonical domain. A company found both through a posting and by Hunter keeps its posting.
5. **Check token and HN companies against sector and size** (only if those filters were given):
   - First choice: a free Discover lookup by domain together with the filters, if Hunter combines the two.
   - Otherwise: Company Enrichment at 0.2 credit each, reading industry, headcount and headquarters.
   - Unknown means kept and labeled; known not to fit means "Left out" with the reason.
6. **Guessed-domain guard (kept from today).** A board token's domain is a guess (`token.com`). Contacts are never bought at a guessed domain until a free homepage fetch confirms it, using the existing `_domain_confirmed` / `fetch_attempts` logic. Domains that come from an HN post's own links, or from Hunter, are not guesses.
7. Then the same tail as a company run: the earlier-list and emailed-company skip, the Email Count check, ordering and cap, contacts, and the report.

### Contacts (both run types)

**People lists** (in `config.toml`; the values below are the shipped starting point):

```toml
[jobs]
people = "hiring"
[companies]
people = "sponsors"
[run]
max_companies = 10
contacts_per_company = 3        # also the Domain Search `limit` (free plan max 10)

[people.hiring]
departments = ["executive", "it", "management"]
seniority = ["executive", "senior"]
titles = ["founder", "cto", "vp engineering", "head of engineering", "director of engineering",
          "engineering manager", "tech lead"]            # priority order
exclude_titles = ["recruit", "talent", "sourcer"]

[people.sponsors]
departments = ["marketing", "communication", "hr", "executive", "it"]
titles = ["partnerships", "sponsorship", "community", "events", "developer relations",
          "university relations", "campus", "marketing", "founder", "cto", "engineering manager"]
exclude_titles = []
```

**For each company:**

1. **Search.**
   - Use Domain Search's `job_titles` filter in the list's priority tiers. Each tier is one call with `limit` set to the slots still open, and the search stops once `contacts_per_company` people are found.
   - If `job_titles` can't be used this way, fall back to a single call by `departments` + `seniority` with `limit = contacts_per_company` (see "Calls made during design").
2. **Order and exclude.** Order the people by title priority and drop anyone matching `exclude_titles`.
3. **Confirm addresses.**
   - An address confirmed in an earlier run is reused for free (the existing `ContactRepo.already_resolved` path in `pipeline/runner._resolve_contacts`).
   - If Domain Search's own verification status is `valid` and dated within `verified_max_age_days` (90), the address counts as confirmed with no further call.
   - Otherwise one Email Verifier call is made (0.5 credit).
   - An address that still isn't confirmed is never shown.
4. **Budget.**
   - `contacts/quota.CreditBudget` moves to fractional credits, counted in integer tenths so there is no float drift.
   - Before a company's search, the worst case is reserved (`contacts × (email + verify)`), and whatever wasn't used is released afterward.
   - If the reservation fails, the company becomes "Left out: out of credits".
   - Costs live in `[hunter]`: `email_cost = 1`, `verify_cost = 0.5`, `enrichment_cost = 0.2`.
5. **Balance.**
   - The balance is read once, before the first purchase, from Hunter's Account endpoint. The current `remaining_credits` reads `requests.searches`, which is likely wrong under the credit system and must be re-checked against Hunter's docs.
   - If the balance can't be read, nothing is bought, as today with `credit_error`.
6. **Removed.** `find_profile` / Email Finder and `[hunter] linkedin_lookup` / `finder_cost` go away.

### Emailed list

- **Storage.** Marks are stored per lowercased address, together with the date emailed. This works for any address, including one the tool never found.
- **Company skip.** A marked address makes its company skipped in later runs. The company is matched by the address's domain run through `canonical_domain`, or by the address belonging to a stored contact of that company.
- **What the commands do:**
  - The report shows "emailed <date>" for any contact whose address is marked.
  - `list` shows the date, address and, when known, the name, title and company, most recent first.
  - `remove` deletes the mark.
- **Earlier runs.** People already contacted in earlier runs are covered: a mark set through the existing `contacts.contacted_at` column (`ContactRepo.mark_contacted`, which nothing calls today) either moves to the new storage or keeps working with it.

### Report (as mocked up in the conversation)

- **Header:** the command (jobs or companies), the role, the filters with the location as parsed, the people list, the run id and date, and credits used and left.
- **Sections, in order:**
  1. With contacts
  2. No one found
  3. Already on an earlier list (including "you've emailed someone here")
  4. Left out, with the reason: sector, size or location mismatch, no posting in the work mode, domain not confirmed, over the cap, or out of credits
  5. Run details: sources and counts (including the HN funnel), credits broken down into emails, verifying and checks, and errors
- **Card:**
  - Name, domain, size band, industry and headquarters.
  - On job runs, the posting: title, work mode, link, and "via HN Who is hiring? (Month Year)" or the board. For Hunter's finds, "Hiring <role>, per Hunter · no posting link".
  - Up to 3 contacts. Each shows name, title, then either the confirmed email with a copy button or "address not confirmed". After that comes a LinkedIn link, or "Search LinkedIn" when Hunter gave none, and "emailed <date>" if marked.
- **Safety rules kept:**
  - Links are `http(s)` only.
  - LinkedIn hosts are allowlisted through `contacts/hunter._safe_linkedin_url`.
  - Jinja autoescape stays on.
  - "Search LinkedIn" is built at render time and never stored.

### Dry run

- **What it calls.** It is free, except for one AI call on job runs (role expansion). HN is parsed by rules only and never uses the LLM fallback, as today. It makes Discover and Email Count calls, which are free.
- **What it prints:**
  - The parsed filters.
  - Companies per source.
  - How many companies were left out by the filters, already on an earlier list, skipped because someone there was emailed, or have no matching people.
  - The **maximum** cost: kept companies × `contacts_per_company` × (`email_cost` + `verify_cost`), plus the companies needing a paid check × `enrichment_cost`.
  - The credits left.
- **Exit behavior** is unchanged. An unchecked balance prints the estimate and exits non-zero.

### Data (migration v4: additive, in `store/migrations.py`; `schema.sql` stays untouched)

- **`companies`** gains `industry` and `hq_location` (with `headcount_band` reused for the size band).
- **`runs`** gains `kind` (`jobs` or `companies`) and `filters` (JSON with sectors, sizes, location, people list and work modes). New runs get `schema_version = 4`.
- **New storage for emailed marks.**
- **New stage statuses** for the left-out reasons and skips listed above: filters, earlier list, emailed company, no matching people, came back empty, over the cap, out of credits, and domain not confirmed.
- **Per-list lookups.** Both "Already on an earlier list" and "came back empty before" must be answerable per people list, either from `runs.filters` joined to the stage rows or from a dedicated column. That choice is the builder's.
- **Hunter's hiring finds** have no posting URL. How to store them (for example a `run_postings` row with a source column and no URL) is the builder's call.
- **Old tables stay** (`evidence_items`, `findings`, `run_company_stages`, `bottlenecks`, `source_documents`). New code stops writing them, and no data is deleted.

### Code that goes away

- **Core:** `core/gate.py`, `core/clustering.py`, `core/themes.py` and `core/stage.py`. Keep whatever size-band parsing is still needed; `parse_band` is re-exported from `contacts/facts.py`.
- **Research code:** `extraction/*` and `sources/surfaces/*`.
- **LLM methods:** `extract_claims` and `write_summary`.
- **Runner stages:** the evidence, classify, synthesize and LinkedIn-lookup stages in `pipeline/runner.py`.
- **Facts parsing:** the funding and stage parsing in `contacts/facts.py`. If enrichment is kept, it is slimmed to industry, size and headquarters.
- **Report parts:** the findings, hooks and stage parts of `render/view.py` and `render/templates/report.html.j2`.
- **Their tests and fixtures.**

### Code that stays and is reused

- **Fetching and job boards:**
  - `net/fetcher.Fetcher`
  - `sources/jobboards/{greenhouse,ashby,lever,multi}.py`
  - `sources/jobboards/region.py`, generalized to `Location`
- **HN discovery:**
  - `sources/hn.py` and `core/hnparse.py`
  - `pipeline/discovery.select_hn_candidates`, minus the stage pre-rank and the recheck window
  - `HNPostRepo` and its resume path (`runner._restored_hn_selection`)
- **Filters and dedupe:** `core/workmode.py` (`posting_matches`, `parse_work_modes`) and `core/dedupe.canonical_domain`.
- **Hunter plumbing:** `contacts/hunter.hunter_get`, `HunterAPIError` and `_safe_linkedin_url`.
- **Storage:** `contacts/quota.CreditBudget` (made fractional), `CompanyRepo`, `ContactRepo`, and `RunRepo`'s stage rows and `--resume` checkpointing.
- **Rendering:** `render/report.write_report` and the Jinja setup.
- **CLI and test plumbing:**
  - `cli.build_context` and its `OUTREACH_FAKE_ADAPTERS=1` switch
  - the test-isolation fixture in `tests/test_cli.py`
  - `tests/pipeline/factories.py`

### New code (layout is the builder's call; this is the shape)

- **Hunter company-search adapter**
  - A protocol, a Hunter implementation and a fake.
  - Discover with filters, optional `job_openings` titles, and lookup by domain.
  - Email Count by department.
- **Pure filter module in `core/`**
  - Sector validation against the bundled list, with near-match suggestions.
  - Size bands.
  - Location parsing and posting-location matching.
- **Pure people module in `core/`**
  - Turns a people list into Domain Search parameters.
  - Orders people by title priority and applies the exclusions.
- **Package data:** the bundled `industries.json`, added to `[tool.setuptools.package-data]`.
- **Runner:** a jobs path and a companies path that share the contacts and report tail. `pipeline/runner.py` is already about 1,100 lines, so splitting it is encouraged.
- **CLI:** `jobs`, `companies`, `sectors`, the `emailed` group, and `report`.
- **Store:** the v4 migration and a repository for emailed marks.

### Docs

- **`README.md`** is rewritten.
  - What the tool is: a contact finder for cold email.
  - The two uses, each with a quick start.
  - Every command, and the config reference: people lists, run caps, `[hunter]` costs, tokens, `[hn]`.
  - Costs and the free plan.
  - Reading the report.
  - The emailed list.
  - Tests.
- **`docs/getting-started.md`** is new, written for a first-time user (the friend).
  - Installing.
  - Creating a Hunter account and key.
  - `.env` with only `HUNTER_API_KEY`.
  - Using `outreach sectors` to pick a sector.
  - A first `companies --dry-run`, then a small real run.
  - Reading the report and marking people emailed.
  - What the free plan's 50 credits buy.
- **Config and help text:** `config.toml` comments, `.env.example` (which key each command needs), and every command's `--help`.
- **Specs:**
  - This design goes in `docs/superpowers/specs/`, with an "As built" section added after implementation, as in the earlier specs.
  - The V1, A and B specs each get a status line saying they are superseded by this one.
  - The LLM-boundary wording changes from "exactly four methods" to two.

## Left to the builder

- **Exact Hunter field names:** Discover's body and response, Email Count's response, Domain Search's per-email verification field, and the Account endpoint's credit fields. Work only from Hunter's public docs and their documented example responses, saved as fixtures. No live calls.
- **Checking token and HN companies:** whether a free Discover lookup by domain plus filters can do it, or Company Enrichment is needed. If enrichment is used, work out how its `category` fields map onto `industries.json` names.
- **Small mechanics:** country-name to ISO alpha-2 mapping for locations, report file names, table and column names, how Hunter's hiring finds are stored, and message wording.
- **Runner structure:** how to split the runner, and how `--resume` checkpoints map onto the new stages.

## Verification

- **TDD, offline only.** Use fakes and recorded fixtures. Never run `outreach jobs` or `outreach companies` live, and never open `.env`. Run the suite with `.venv/Scripts/python -m pytest -q`.
- **Pure truth tables:**
  - Sector validation: exact names, case, unknown names with suggestions, and names containing commas.
  - Size bands.
  - Location parsing: `TX`, `Austin, TX`, `CA` (California), `Canada`, `Toronto, Canada`, `US`, garbage input.
  - Posting-location matching, including remote postings and an unchanged US default.
  - People list to Hunter parameters, title ordering and exclusions.
  - The fractional budget: reserve, release, refusal, and no float drift.
- **Adapter contract tests:** run the fake and the Hunter implementation against fixtures for Discover (filters, `job_openings`, lookup by domain), Email Count, Domain Search with filters and `limit`, and the verification shortcut.
- **Pipeline tests with fakes:**
  - A company run end to end.
  - A job run merging all three sources, keeping the posting when a company is found twice.
  - The earlier-list skip, matched on the same people list.
  - The emailed-company skip, including an address added by hand.
  - The no-matching-people skip, which spends nothing.
  - Tiered title searches stop once the slots are full, and the fallback path works.
  - A stale `valid` gets re-verified.
  - An empty search doesn't use a cap slot.
  - Previously empty companies are ordered after fresh ones.
  - Running out of credits stops purchases and lists the rest.
  - Emailed people are marked on the report and never re-bought.
  - Unknown sector is kept and labeled.
  - The guessed-domain guard.
  - `--resume`.
  - An Algolia outage leaves the other sources intact.
- **CLI tests:**
  - Bad arguments exit 2 before any key or network access.
  - `companies` doesn't require the Anthropic key.
  - `sectors` filtering.
  - `emailed add`, `list` and `remove`.
  - Dry-run output, with no LLM fallback and correct cost lines.
  - `report` on a v3 run.
- **Migration tests:** v3 → v4 and v1 → v4, with contact history intact.
- **First live run is the user's.** Do `outreach companies --sector "Software Development" --size 51-200 --location TX --dry-run` first, then a small real run, then check Hunter's account page against the report's credit line.

## Next steps

1. The user approves this design.
2. Create the feature branch off `main` (name it, for example, `feat/contact-finder`), save this document as `docs/superpowers/specs/2026-10-11-contact-finder-design.md`, and commit.
3. Invoke `superpowers:writing-plans` for the implementation plan. The user picks how it gets executed.

## As built

Decisions taken during implementation and review, recorded here because the execution ledger is deleted after the work lands. Hunter's payload shapes were confirmed from its public API reference (https://hunter.io/api-documentation/v2) and saved as fixtures; no live call was made.

**Decisions the plan made inside the spec's room**

- **People lists are tiers.** Hunter documents `job_titles` on Domain Search, so the department/seniority fallback was not built. A list is `tiers` (each a list of titles, searched with one Domain Search) plus `exclude_titles`. The `departments`, `seniority` and `titles` keys in the config sketch above don't exist. Tier searches also send `type=personal`.
- **The Email Count pre-check is per domain.** It uses the domain's total personal emails, not counts per department. A department sum would have dropped companies whose partnerships people Hunter files under "sales", and an empty search costs nothing anyway.
- **No Company Enrichment.** The sector and size check for board and HN companies uses Discover only, so `enrichment_cost` is not a setting. The first call is `organization.domain` alone, which tells which domains Hunter knows; then one call per sector × size combination, with the filters.
- **Discover carries no sector or size.** Its response has only domain, name and email counts, so every search is one request per sector × size combination and each company carries the exact sector and band it was found under. The `companies` table is not extended; those values live in a per-run table, `run_company_info`. For companies Hunter found, the report shows the requested location as the headquarters ("HQ Texas").
- **Report file names include the run id** (`2026-10-14-run7-jobs-backend-engineer.html`), so two runs on the same day don't overwrite each other.

**Departures from the text above**

- **Posting locations read every state they name.** `posting_in_location` takes every US state a posting names (each ", XX" code and each full state name), not only a trailing code. A state search passes when the state asked for is among them. Hub cities ("NYC", "Austin") count only when the text names no state. A city search needs the city as a whole word and, when the text names any state, the state asked for among them. So "Austin, TX / New York" passes an Austin, TX search and "Austin, MN" does not. Ambiguous names such as "Kansas City" stay imperfect.
- **"Sector unknown" on every unknown-fit card.** The report prints it on any card whose fit is unknown, whatever else the card shows. An unknown fit means Hunter doesn't know the domain, so the card has no sector or size anyway.
- **"No one found" wording.** A company the free count found nobody at reads `Hunter has no one matching "<list>" — cost nothing`. A company whose search came back empty reads `Hunter has no one matching "<list>"` with no cost claim, because its paid results may all have been excluded titles, which were paid for.
- **The sector and size check treats "known but missing" as a mismatch.** A domain Hunter knows that is absent from the sector- and size-filtered Discover call is left out as "doesn't match your sector or size". Discover's response has no sector or size, so "known but no data" can't be told apart from "known and wrong". The spec's keep-unknown rule holds for domains Hunter doesn't know at all. See the last known gap.
- **An empty role expansion means nothing found.** If `expand_titles` returns no titles, the Hunter hiring search makes no request: without its `job_openings` filter Hunter would answer with any company in the location. The role is not substituted for the missing titles.
- **A failed role expansion exits 1.** If the AI call that expands the role fails (an HTTP error, or a reply that isn't a list of titles), `outreach jobs` prints `Expanding the role into job titles failed: …` and exits 1, in a dry run and in a real run. Nothing has been recorded or spent by then.
- **Dry-run edge cases.**
  - `--dry-run` with `--resume` exits 2.
  - A dry run that finds no companies stops after the no-match line and doesn't read the balance.
  - A Hunter error from the free email counts exits 1 with `Hunter's email count failed: …`.
- **The jobs dry run's ceiling leaves room for the LLM fallback.** The Dry run section above has HN read by rules only, and also lists "a dry run that understates the cost" as an anti-goal. A real run can keep more HN companies through the fallback than the rules alone find, so the rules-only ceiling could be too low. The decision keeps the dry run LLM-free and widens the ceiling instead. The dry run adds `min(hn_needs_llm, [hn] llm_fallback_max, max(0, [hn] max_new_companies − hn_kept))` companies to the ones it would buy, prices `min([run] max_companies, would-buy + that)` companies, and prints `Includes up to N more HN companies that the LLM fallback might add in a real run.` just before the `Dry run:` line. N is how far the ceiling rose, so the run's cap can make it smaller than the sum, and the line is left out when nothing is added. The `Dry run:` line keeps its format. The companies dry run is unchanged.
- **Failures of a run are said out loud.** A Hunter request that can't be made (offline, DNS failure, timeout) raises `HunterAPIError` naming only the endpoint and the kind of failure, never the URL, so every existing handler covers it. A real run whose summary holds a `credits: …` error (the balance couldn't be read, so nothing was bought) prints `The credit balance could not be checked, so nothing was bought: …` on stderr, after the report is written, and exits 1. Any other errors in the summary print `Warning: N error(s) during this run; the report's run details list them.` and exit 0. `expand_titles` keeps only non-blank text titles (stripped), because a blank term matches every posting; with none left, nothing is found.
- **The report labels every non-HN posting "via job board".** The spec says "or the board". A posting found by a board adapter is labelled with the kind of source, not the board's name.
- **`outreach report <old run>` prints no file path.** For a run made before the report was rebuildable, it says `see its saved HTML report` and exits 1. Old report file names can't be rebuilt reliably, so the command doesn't guess a path.
- **Task 1 kept two fixtures the plan listed for deletion.** `tests/fixtures/careers_page.html` and `eng_blog_post.html` stay, because the moved `html_to_text` tests (`tests/sources/test_htmltext.py`) read them.
- **`.gitignore` anchors `/data/`.** The bare pattern `data/` also ignored `src/outreach/data/`, so the bundled `industries.json` would never have reached git.

**Hunter fields, as confirmed**

- **Account credits.** `GET /v2/account` → `data.requests.credits.{available, used, remaining}`. The tool's balance is `available - used`, which equals `remaining` in the documented example. The counter exists only on unified-credit plans.
- **Domain Search.** `GET /v2/domain-search` with `domain`, `job_titles` (comma-separated; a `CTO` search matches "Co-founder & CTO" but not "Director"), `type=personal` and `limit` (default 10, maximum 100). Each `data.emails[]` entry has `value`, `first_name`, `last_name`, `position`, `linkedin` and `verification.{status, date}`; the verification field is what lets a recent `valid` stand in for a paid check. The docs list no `linkedin_url` field; the code reads `linkedin` first and falls back to it harmlessly.
- **Email Count.** `GET /v2/email-count?domain=` → `data.personal_emails`.
- **Discover.** `POST /v2/discover` with `industry.include`, `headcount`, `headquarters_location.include` (`country`, `state`, `city`), `job_openings.job_title.include` and `organization.domain`; the response is `data[].{domain, organization}`. At most 100 companies come back per call, `offset` is Premium-only, and the limits are 5 requests a second and 50 a minute (the tool doesn't throttle; a call Hunter refuses surfaces as an ordinary `Hunter discover returned HTTP <status>` error).
- **Fixtures.** `tests/fixtures/hunter_*.json` are the documented examples. The Domain Search one adds two entries in the documented shape and moves the domain to `acme.example`; the Discover one was reconstructed from the documented `organization` example.

**Known gaps**

- **Non-US locations match by country name only.** A posting that says "Toronto" without "Canada" fails a `--location Canada` search.
- **Discover's free plan returns the same top 100 companies per filter combination.** There is no pagination, so a run can't reach past them; the earlier-list and emailed skips are what move later runs on to new companies.
- **The Email Count pre-check is per domain, not per title.** A company with people on file but none on the chosen list passes the free check and ends in "No one found" after a search that returned nothing.
- **Domain Search's cost per email is not stated in Hunter's API reference.** The tool assumes 1 credit per email (`[hunter] email_cost`) and 0.5 per address check (`verify_cost`), from Hunter's help center (2026-08-20). The reference says only that Discover is free; it states no cost for Email Count, Domain Search, Email Verifier or Company Enrichment. Check the account page against the first run's credit line.
- **The Account credit counter exists only on unified-credit plans.** On any other plan the balance can't be read, the run records a `credits:` error, nothing is bought, and a real run exits 1 (a dry run prints its estimate and exits 1).
- **Discover with `organization` and `industry` or `headcount` together is undocumented.** The docs neither allow nor forbid it, and the jobs run's sector and size check relies on it. One manual Discover call that combines them would settle it.
- **With a size filter, a company whose headcount Hunter lacks is left out** as not matching, rather than kept as "sector unknown".
