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
Enrichment; quotes found in press and about pages fill the gaps.

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
  to expand role titles, extract claims from fetched pages, and write the
  one-paragraph summary. It has exactly those three jobs; the pass/fail
  decision about whether evidence is good enough is deterministic code, not a
  model call.
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
  "press", "about"
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

[paths]
db = "data/pipeline.db"           # SQLite database (created on first run)
cache = "data/cache"              # content-addressed cache of fetched pages
reports = "reports"               # where finished HTML reports are written
```

The three token lists are the company slugs in public job-board URLs:
`https://boards.greenhouse.io/<token>`, `https://jobs.ashbyhq.com/<token>` and
`https://jobs.lever.co/<token>`. Add the companies you want discovery to
search. A board with no tokens is simply not searched; if all three lists are
empty the CLI warns that the run will discover zero companies.

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
  `linkedin_lookup = false`. These are upper bounds. Nothing is spent and no
  report is written. If the balance can't be fetched the estimate is still
  printed, marked as unchecked, with a non-zero exit status.
- `--resume RUN_ID` — resume a previously interrupted run by its id instead
  of starting a new one. Work already checkpointed for a stage is not
  repeated.

### `outreach report RUN_ID`

```bash
outreach report 3
```

Re-renders a completed run's HTML report from what is already stored in the
database, without touching any adapter or spending any credits. Useful after
changing report formatting, or to regenerate a report you deleted.

## Credit costs and limits

- Hunter doesn't document which credit bucket Company Enrichment and Email
  Finder draw from, so the pipeline charges both against the one balance it
  reads at the start of a run (the conservative assumption). After your first
  run, check your Hunter account page to see which buckets actually moved, and
  correct `enrichment_cost` / `finder_cost` in `[hunter]` if they differ.
- A company's enrichment facts are cached for `facts_ttl_days` (90), so
  re-running within that window doesn't re-charge for them.
- A failed LinkedIn lookup is re-attempted, and re-charged, on later runs.
- GitHub's unauthenticated API allows 60 requests per hour, so on a large run
  the repository evidence thins out once that is used up.

## Reading the report

Companies are laid out by stage: growth and expansion companies first, with
what they are building, then the other stages, then companies where no
building evidence was found (with the specific reason the gate did not pass,
e.g. `insufficient_independent_sources`), then an **Excluded** list (over the
size cap, or no posting in the requested work mode), then run diagnostics.
Every quote links back to the source page it was extracted from. A contact's
email address is shown only when Hunter's verifier confirmed it as
deliverable — an unverified or not-found address is never printed as though it
were a working one — and a LinkedIn link appears only when a lookup found one.
