# Outreach Pipeline

Turns a role and a sector into a single self-contained HTML report aimed at
startups of up to 2,000 employees: companies that are hiring for that role
right now, what each one is building (with the first-party evidence for it),
the funding stage it is at, and the people worth emailing, with a LinkedIn
link where one can be found. Companies over the size cap are listed as
excluded rather than silently dropped.

A run spends real Hunter credits, so a broken run should cost zero. Every API
key is checked before anything is spent, discovery always happens before
enrichment, and `--dry-run` itemizes what a real run would cost before you
commit to it. **Don't run a live `outreach run` without first doing
`--dry-run`.**

## Stages

Each company is classified into one stage, and the report is ordered by it.
The first rule that matches wins; funding beats headcount.

| Stage | Meaning (default thresholds, configurable under `[stage]`) |
| --- | --- |
| seed startup | Latest round is pre-seed or seed, or at most 49 employees |
| growth | Latest round is Series A or B, or 50-499 employees |
| expansion | Series C or later, or 500+ employees, or a growth company with a recent expansion signal |
| maturity | IPO or acquired, or founded 12+ years ago and last funded 6+ years ago |
| unknown | Nothing found to classify it by |

Headcount, funding rounds and founding year come from Hunter's Company
Enrichment; quotes found in press and about pages fill the gaps. Quotes from
pages that describe the company *today* (careers, about, job postings) can't
date a funding round or count as a recent expansion signal, since their only
date is the day they were fetched. Any IPO or acquisition among the known
rounds makes a company mature, whatever came after it.

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `outreach` command (backed by `src/outreach/cli.py`).

## `.env` setup

Copy the example file and fill in your keys:

```bash
cp .env.example .env
```

```
ANTHROPIC_API_KEY=sk-ant-...
HUNTER_API_KEY=...
OUTREACH_CONTACT_EMAIL=you@example.com
```

- `ANTHROPIC_API_KEY` — used by `AnthropicLLM` (`src/outreach/llm/anthropic_client.py`)
  to expand role titles, extract claims from fetched pages, write the
  one-paragraph summary, and read HN posts the deterministic parser can't. It
  has exactly those four jobs. The pass/fail decision about evidence, the
  startup stage and the size cap are deterministic code, never a model call;
  and every field the model reads out of an HN post must appear in the post
  itself, or the post is dropped.
- `HUNTER_API_KEY` — used by `HunterProvider` (`src/outreach/contacts/hunter.py`)
  to find people at a company's domain and verify their email addresses, and
  by `HunterFactsProvider` (`src/outreach/contacts/facts.py`) for company facts
  (headcount, funding, founding year) and LinkedIn profile lookups.
- `OUTREACH_CONTACT_EMAIL` — appended to the crawler's user agent as
  `(+contact: ...)`, so a site operator who notices this traffic can reach a
  person about it. Optional, and deliberately kept out of the source rather
  than hardcoded: it is a politeness signal, not a secret, but committing it
  would publish a scrapeable address. Unset, requests still identify the tool
  and the CLI warns once per run.

`.env` is gitignored. Both keys are required for a live run and are checked
*before* any network call is made — a run with a missing key aborts
immediately rather than doing partial, uncharged work and then failing.

## `config.toml`

Copy or edit `config.toml` at the repo root. The `[gate]`, `[ranking]`,
`[discovery]` and `[paths]` sections and their core keys are required; a
missing one aborts the run with a clear error naming it. `[stage]` and
`[hunter]` are optional, and everything shown commented out below is optional
with the default shown.

```toml
[gate]
min_independent_sources = 1       # how many independent sources must agree
require_first_party = true        # at least one source must be first-party
recency_days = 180                # evidence older than this is not "current"
first_party_classes = [           # which source classes count as first-party
  "job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page",
  "press", "about", "hn_post"
]
# max_findings_per_company = 3    # findings kept per company

[ranking]
max_contacts_per_company = 3      # how many people to surface per company
exclude_title_patterns = ["recruit", "talent", "sourcer"]  # never contact these

[discovery]
headcount_min = 1
headcount_max = 2000              # a company is dropped only when KNOWN to exceed this
region = "US"                     # postings outside this region are dropped
greenhouse_tokens = []            # e.g. ["stripe"]
ashby_tokens = []                 # e.g. ["ramp"]
lever_tokens = []                 # e.g. ["palantir"]
max_blog_posts = 5                # engineering posts read per company
max_job_postings = 2              # matching job postings read per company
# max_changelog_entries = 5
# max_press_posts = 3
# max_github_repos = 5

# [stage]                         # see the Stages table above
# seed_max_headcount = 49
# growth_max_headcount = 499
# maturity_min_age_years = 12
# maturity_min_years_since_funding = 6
# signal_recency_days = 365       # how recent an expansion signal must be

# [hunter]
# enrichment_cost = 1             # credits charged per Company Enrichment call
# finder_cost = 1                 # credits charged per Email Finder (LinkedIn) lookup
# linkedin_lookup = true          # set false to skip LinkedIn lookups entirely
# facts_ttl_days = 90             # reuse a company's cached facts for this long

# [hn]                            # discovery from HN's "Who is hiring?" thread
# enabled = true
# max_new_companies = 15          # HN companies researched per run, best stage hints first
# llm_fallback_max = 20           # LLM parses per run for posts the rules can't parse
# recheck_days = 30               # skip HN companies researched this recently

[paths]
db = "data/pipeline.db"           # SQLite database (created on first run)
cache = "data/cache"              # content-addressed cache of fetched pages
reports = "reports"               # where finished HTML reports are written
```

The three token lists are the company slugs in public job-board URLs:
`https://boards.greenhouse.io/<token>`, `https://jobs.ashbyhq.com/<token>` and
`https://jobs.lever.co/<token>`. Add the companies you want discovery to
search. A board with no tokens is simply not searched; if all three lists are
empty and HN discovery is off, the CLI warns that the run will discover zero
companies.

## Discovery from Hacker News

Every run also reads the latest monthly "Ask HN: Who is hiring?" thread (free,
through the HN Algolia API) and adds companies from it, on top of your tokens:

1. Only posts that mention the role (any of the expanded role titles) go on.
2. Each post is parsed by rules: the `Company | Role | Location | REMOTE |
   Full-time` first line, the company's own website from the post's links,
   and a Greenhouse, Ashby or Lever board if one is linked. Posts the rules
   can't read go to the LLM, at most `llm_fallback_max` per run, and its answer
   is kept only if every field appears in the post.
3. The same US-region and `--work-mode` filters apply.
4. Before any credit is spent, companies are ranked by what their post gives
   away for free ("Series B", "team of 80": growth and expansion first),
   companies researched in the last `recheck_days` days are skipped, and only
   the top `max_new_companies` are kept. The rest appear in the report's
   Excluded list.
5. A kept company with a board is searched through that board, using the
   post's real website rather than a domain guessed from the board token; one
   without a board (or whose board lacks the role) uses the post itself as its
   posting. Either way the post is read as first-party evidence (`hn_post`),
   dated by when it was posted.

If HN can't be reached the run carries on with your tokens. `--no-hn` skips
it for one run; `[hn] enabled = false` turns it off.

## Commands

### `outreach run`

```bash
outreach run --role "Backend Engineer" --sector fintech --work-mode remote --dry-run
outreach run --role "Backend Engineer" --sector fintech --work-mode remote
```

Runs the full pipeline: discover postings, enrich and classify each company,
fetch and extract evidence, resolve and verify contacts within budget,
evaluate the gate, and write one HTML report to the configured `reports`
directory. Prints the report's path when done. Do the `--dry-run` first.

Options:

- `--work-mode remote|hybrid|onsite[,...]` — keep only postings in the given
  work modes, e.g. `--work-mode remote,hybrid`. Default: all three. A posting
  whose mode can't be determined is kept rather than guessed away, and
  contract, part-time, intern and temporary postings are always dropped. A
  company with no posting left is listed as excluded in the report. An
  unrecognised value exits with status 2 before anything is spent.
- `--dry-run` — run discovery only, then itemize what a full run would cost
  against how many credits remain on the account, e.g. `Dry run: 12 companies
  matched. A full run would use up to 12 enrichment + 12 contact-search + 36
  LinkedIn-lookup credits (60 total); 80 remain.` Companies are counted after
  the `--work-mode` filter. The LinkedIn figure is companies x
  `max_contacts_per_company` x `finder_cost`, or 0 with
  `linkedin_lookup = false`. These are upper bounds. No Hunter credits are
  spent and no report is written; the only paid call is one Anthropic request
  that expands the role title into search terms. If the balance can't be fetched the estimate is still
  printed, marked as unchecked, with a non-zero exit status.
- `--no-hn` — skip discovery from HN's "Who is hiring?" thread for this run.
  In a `--dry-run`, HN is read and parsed for free (never the LLM fallback),
  and an extra `HN: ...` line shows how many posts were read, matched the role,
  parsed, would need the LLM fallback, and survive the cap; the credit
  estimate includes those companies.
- `--resume RUN_ID` — resume a previously interrupted run by its id instead
  of starting a new one. Work already checkpointed for a stage is not
  repeated.

### `outreach report RUN_ID`

```bash
outreach report 3
```

Re-renders a completed run's HTML report from what is already stored in the
database, without touching any adapter or spending any credits. Useful after
changing report formatting, or to regenerate a report you deleted. Runs made
before the stage-targeting change (schema v1) can't be re-rendered in the new
layout; the command says so and exits non-zero — their original HTML files in
`reports/` are the record.

## Credit costs and limits

- Hunter doesn't document which credit bucket Company Enrichment and Email
  Finder draw from, so the pipeline charges both against the one balance it
  reads at the start of a run (the conservative assumption). After your first
  run, check your Hunter account page to see which buckets actually moved, and
  correct `enrichment_cost` / `finder_cost` in `[hunter]` if they differ.
- A company's enrichment facts are cached for `facts_ttl_days` (90), so
  re-running within that window doesn't re-charge for them.
- LinkedIn lookups are bought only with the credits left after every
  company's contact search, highest-priority company first, so a short
  balance costs profile links rather than contacts.
- A failed LinkedIn lookup is re-attempted, and re-charged, on later runs.
- GitHub's unauthenticated API allows 60 requests per hour, so on a large run
  the repository evidence thins out once that is used up.
- HN discovery adds up to `max_new_companies` (15) companies per run, each
  costing the same Hunter credits as a token company; lower it if your balance
  is small. It never spends credits on companies skipped by the cap or the
  recheck window.

## Reading the report

Companies are laid out by stage: growth and expansion companies first, with
what they are building, then the other stages, then companies where no
building evidence was found (with the specific reason the gate did not pass,
e.g. `insufficient_independent_sources`), then an **Excluded** list (over the
size cap, or no posting in the requested work mode), then run diagnostics.
Every quote links back to the source page it was extracted from. A contact's
email address is shown only when Hunter's verifier confirmed it as
deliverable — an unverified or not-found address is never printed as though it
were a working one. A **LinkedIn** link appears only when a profile was found;
otherwise the contact gets a clearly labelled **Search LinkedIn** link (a
people search for the name and company), which is never stored or presented as
a found profile.

Under each company, **Hooks for a build** lists the public GitHub repositories
this run saw recently pushed and the company's developer docs (the first of
`/docs`, `/developers`, `/api` that answered) — raw material if you'd rather build
something for a company than write to it.

## Tests

```bash
.venv/Scripts/python -m pytest -q    # Windows; .venv/bin/python on macOS/Linux
```

Every test runs against fakes and recorded fixtures; none touches a live API or
your `.env`. The CLI tests patch out `.env` loading and run in a scratch
directory, so they never see your real keys, config or database.
