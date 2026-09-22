# Outreach Pipeline

Turns a role and a sector into a single self-contained HTML report: companies
that plausibly need to hire for that role right now, the first-party evidence
that says so, and the people worth emailing.

The whole point is that a broken run costs zero credits. Every API key is
checked before anything is spent, discovery always happens before
enrichment, and `--dry-run` tells you what a real run would cost before you
commit to it.

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
```

- `ANTHROPIC_API_KEY` — used by `AnthropicLLM` (`src/outreach/llm/anthropic_client.py`)
  to expand role titles, extract claims from fetched pages, and write the
  one-paragraph bottleneck summary. It has exactly those three jobs; the
  pass/fail decision about whether evidence is good enough is deterministic
  code, not a model call.
- `HUNTER_API_KEY` — used by `HunterProvider` (`src/outreach/contacts/hunter.py`)
  to find people at a company's domain and verify their email addresses.

`.env` is gitignored. Both keys are required for a live run and are checked
*before* any network call is made — a run with a missing key aborts
immediately rather than doing partial, uncharged work and then failing.

## `config.toml`

Copy or edit `config.toml` at the repo root. All keys are required; a
missing one aborts the run with a clear error naming it.

```toml
[gate]
min_independent_sources = 2       # how many independent sources must agree
require_first_party = true        # at least one source must be first-party
recency_days = 180                # evidence older than this is not "current"
first_party_classes = [           # which source classes count as first-party
  "job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page"
]

[ranking]
max_contacts_per_company = 3      # how many people to surface per company
exclude_title_patterns = ["recruit", "talent", "sourcer"]  # never contact these

[discovery]
headcount_min = 20
headcount_max = 1000
region = "US"                     # postings outside this region are dropped
greenhouse_tokens = []            # Greenhouse board tokens to search, e.g. ["stripe"]

[paths]
db = "data/pipeline.db"           # SQLite database (created on first run)
cache = "data/cache"              # content-addressed cache of fetched pages
reports = "reports"               # where finished HTML reports are written
```

`greenhouse_tokens` are the company slugs in a public Greenhouse board URL
(`https://boards.greenhouse.io/<token>`) — add the companies you want
discovery to search.

## Commands

### `outreach run`

```bash
outreach run --role "Backend Engineer" --sector fintech
```

Runs the full pipeline: discover postings, fetch and extract evidence per
company, resolve and verify contacts within budget, evaluate the gate, and
write one HTML report to the configured `reports` directory. Prints the
report's path when done.

Options:

- `--dry-run` — run discovery only, then report how many companies matched
  and how many enrichment credits a full run would use against how many
  remain on the account. No contact lookups happen and no report file is
  written. This is the command to run when you're unsure whether you can
  afford a full run.
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

## Reading the report

Companies are split into two sections: **evidenced bottlenecks** (a theme
cleared the gate — at least the configured number of independent,
first-party, recent sources corroborate it) and **no bottleneck found**
(with the specific reason the gate did not pass, e.g.
`insufficient_independent_sources`). Evidenced companies are sorted
smallest-headcount-first, since a smaller company's bottleneck is generally
more bypassable by one hire. Every quote links back to the source page it
was extracted from. A contact's email address is shown only when Hunter's
verifier confirmed it as deliverable — an unverified or not-found address is
never printed as though it were a working one.
