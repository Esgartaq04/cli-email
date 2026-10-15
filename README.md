# outreach: a contact finder for cold email

`outreach` finds the right people to email at the right companies and gives you
their confirmed email addresses in one HTML report. It has two uses:

- **Job outreach.** `outreach jobs` finds companies that are hiring for a role
  and the hiring managers there.
- **Sponsors and speakers.** `outreach companies` finds companies by sector,
  size and location, whether or not they are hiring, and the people to ask to
  sponsor a student org or to give a talk.

Hunter is the only source of people and addresses. **It never writes or sends
email.** You write every message yourself; `outreach emailed` only keeps track
of who you have already written to, so nobody is listed or paid for twice.

New to this? Start with [docs/getting-started.md](docs/getting-started.md).

The sample output in this README comes from the real commands run against test
doubles standing in for Hunter, Hacker News and the AI, filled with made-up
data: every company and person in it is invented, and the addresses end in
`.example`. Yours will differ. On Windows, paths in the output use backslashes.

## What a run costs

Hunter charges credits for the people it reveals, so read this before the first
real run.

| What | Credits |
| --- | --- |
| Reveal one email address | 1 |
| Check one address that Hunter has not already confirmed | 0.5 |
| Company search (Hunter's Discover) | free |
| Email count (is there anyone to find at this company?) | free |

- **The free plan is 50 credits a month.** At the worst case of 4.5 credits per
  company (3 addresses and 3 checks) that is about 11 companies at 3 people
  each. An address Hunter confirmed within the last 90 days is not checked
  again, so a company can cost as little as 3 credits.
- **Where the prices come from.** Hunter's API reference says the company
  search is free. It states no cost for the email count, for revealing an
  address or for checking one, so those figures, and the plan size, come from
  Hunter's help center (the page was updated 2026-08-20). After your first
  run, compare the report's credit line with your Hunter account page. If
  Hunter changes a price, edit `[hunter]` in `config.toml`.
- **Always `--dry-run` first.** It prints the most the run can cost and how
  many credits you have, and spends nothing. (On a job run it makes one AI call,
  which uses your Anthropic key.)

## Install

Requires Python 3.11 or newer. Run these in the project folder, and run every
`outreach` command from that folder too: `config.toml`, the database
(`data/pipeline.db`) and the reports (`reports/`) are all found relative to it.

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

This installs the `outreach` command. Check it with `outreach --help`.

## Keys

Copy the example file and fill in the keys you need (Windows:
`copy .env.example .env`):

```bash
cp .env.example .env
```

| Key | Needed by | Notes |
| --- | --- | --- |
| `HUNTER_API_KEY` | `outreach companies` and `outreach jobs` | The only key a company run needs. Create one at <https://hunter.io/api-keys>. |
| `ANTHROPIC_API_KEY` | `outreach jobs` only | Used for the two AI calls described under [AI calls](#ai-calls). |
| `OUTREACH_CONTACT_EMAIL` | `outreach jobs` only, optional | Added to the user agent of the web pages a job run fetches, so a site owner who notices can write to you. Without it the run still works and prints a warning. |

`outreach sectors`, `outreach emailed` and `outreach report` need no keys.

`.env` is gitignored: never commit it or paste a key anywhere public. Keys are
checked before anything is spent. A command with a missing key stops at once
with exit status 2 and uses no credits:

```text
HUNTER_API_KEY is not set. Add it to .env before running — the pipeline aborts here so no credits are spent on a broken run.
```

## Quick start: a company run

For a student org asking companies to sponsor or to give a talk. This needs
only the Hunter key.

1. **Find the exact sector name.** Sectors are Hunter's own names, so look them
   up first (add a word to narrow the list):

   ```bash
   outreach sectors software
   ```

   ```text
   IT System Custom Software Development
   Software Development
   Data Security Software Products
   Desktop Computing Software Products
   Embedded Software Products
   Mobile Computing Software Products
   Size bands: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+
   ```

2. **Dry run.** See what the run would do and cost:

   ```bash
   outreach companies --sector "Software Development" --size 51-200 --location TX --dry-run
   ```

   ```text
   Filters: Software Development · 51-200 · Texas
   Hunter found 6 companies.
   Skipped: 0 on an earlier list, 0 where you've emailed someone, 1 with no one matching.
   Dry run: up to 5 companies would be bought, at most 22.5 credits; 50 left.
   ```

   The first line shows how your filters were read. The last line is the
   worst case for the companies it would buy at (5 companies x 3 people x
   1.5 credits), followed by the credits you have.

3. **The real run.** Same command without `--dry-run`:

   ```bash
   outreach companies --sector "Software Development" --size 51-200 --location TX
   ```

   ```text
   Run 1: contacts bought at 4 companies, 11 credits used. Report: reports/2026-10-14-run1-companies-software-development.html
   ```

4. **Open the report** in your browser: `open reports/<file>.html` on macOS,
   `start reports\<file>.html` on Windows, `xdg-open reports/<file>.html` on
   Linux. See [Reading the report](#reading-the-report).

5. **After you email people, mark them** so they are never listed or paid for
   again, and neither is anyone else at their company:

   ```bash
   outreach emailed add jamie@kestrel-analytics.example casey@marlow-robotics.example
   ```

   ```text
   Marked 2 addresses as emailed on 2026-10-14.
   ```

   The next dry run for the same search reports `Skipped: 4 on an earlier list,
   ...`, and a real run moves on to new companies.

## Quick start: a job run

For reaching the hiring managers at companies hiring for your role. This needs
`HUNTER_API_KEY` and `ANTHROPIC_API_KEY`.

```bash
outreach jobs --role "Backend Engineer" --sector "Software Development" --size 51-200 --location TX --dry-run
```

```text
Filters: Software Development · 51-200 · Texas
HN: Ask HN: Who is hiring? (October 2026) — 5 posts, 4 match the role, 4 parsed, 0 would need the LLM fallback, 3 kept after the cap.
Found: 0 from job boards, 3 from HN, 1 from Hunter's hiring search; 0 left out by your filters.
Skipped: 0 on an earlier list, 0 where you've emailed someone, 1 with no one matching.
Dry run: up to 3 companies would be bought, at most 13.5 credits; 50 left.
```

The `HN:` line is the "Who is hiring?" funnel: how many posts were read, how
many name your role, how many the rules could parse, how many only the AI
fallback could (a dry run never uses it) and how many survive the cap. The
`Found:` line counts companies by source. Then the real run, and the same
follow-up as for a company run:

```bash
outreach jobs --role "Backend Engineer" --sector "Software Development" --size 51-200 --location TX
```

```text
Run 1: contacts bought at 3 companies, 6.5 credits used. Report: reports/2026-10-14-run1-jobs-backend-engineer.html
```

`--sector`, `--size` and `--location` are all optional on a job run. Without
them it looks for the role anywhere in the US and makes no sector or size check.

## Commands

Every command prints its own help with `--help`. Exit status is 0 on success,
1 when a run or lookup fails (Hunter refused a search, a run id doesn't exist,
nothing was removed) and 2 for a bad argument or a missing key. Arguments are
all checked before any key is read or any network call is made.

### `outreach jobs`

```bash
outreach jobs --role "Backend Engineer" [--sector NAME]... [--size BAND]... [--location TEXT]
              [--work-mode remote,hybrid,onsite] [--people NAME] [--no-hn] [--dry-run] [--resume RUN_ID]
```

Finds companies hiring for the role, buys the people at them, and writes a
report. One AI call first expands your role into similar job titles. Companies
then come from three places, and are bought at in this order: the job boards
listed in `config.toml` (Greenhouse, Ashby and Lever tokens), the latest Hacker
News "Who is hiring?" thread, and Hunter's own "hiring for this title" search.
A company found twice is merged and keeps its posting.

| Option | Meaning |
| --- | --- |
| `--role TEXT` | The job title to look for, such as "Backend Engineer". Required unless you use `--resume`. |
| `--sector TEXT` | A sector name; repeat it for more. |
| `--size TEXT` | A company size band such as 51-200; repeat it for more. |
| `--location TEXT` | Where the job and the company are: "TX", "Austin, TX" or "Canada". Default: the US. |
| `--work-mode TEXT` | Comma-separated remote, hybrid, onsite. Default: all three. |
| `--people TEXT` | The people list to buy. Default: `jobs.people` in `config.toml`. |
| `--no-hn` | Skip discovery from HN's "Who is hiring?" thread. |
| `--dry-run` | Count the companies and price the run without starting it. |
| `--resume INTEGER` | Finish an earlier job run, with the role, filters and people list it used. |

- A posting whose work mode can't be told is kept rather than guessed away.
  Contract, part-time, intern and temporary postings are always dropped.
- Board and HN companies are checked against `--sector` and `--size` with a
  free Hunter lookup (only if you gave one of them). A company Hunter has no
  data on is kept, labelled "sector unknown", and bought after the known
  matches; a company that is known not to fit goes to the report's Left out
  list. Hunter's own finds were already filtered by Hunter.
- A board token's domain is a guess (`token.com`). Nothing is bought at a
  guessed domain until a free fetch of its homepage answers; if it doesn't, the
  company is listed as left out.
- HN: only companies whose post names your role, work mode and location are
  kept, the first `max_new_companies` (15) in thread order, and the rest are
  listed as left out. Posts the rules can't read go to the AI, at most
  `llm_fallback_max` (20) per run, and its answer is kept only if every field
  appears in the post itself.
- `--resume` buys at the companies an earlier run already found, without
  searching again. The role, filters and people list stored with that run are
  used, and any other flag is ignored. A resume still needs the same keys. It
  can't be combined with `--dry-run` (exit 2).
- Two warnings can print: `OUTREACH_CONTACT_EMAIL` is not set, and no board
  tokens with HN discovery off (then companies can come only from Hunter's
  hiring search).

### `outreach companies`

```bash
outreach companies --sector NAME [--sector NAME]... [--size BAND]... [--location TEXT]
                   [--people NAME] [--dry-run] [--resume RUN_ID]
```

Finds companies in Hunter's directory by sector, size and headquarters
location, buys the people at them, and writes a report. It makes no AI call and
needs only `HUNTER_API_KEY`.

| Option | Meaning |
| --- | --- |
| `--sector TEXT` | A sector name; repeat it for more. Required unless you use `--resume`. |
| `--size TEXT` | A company size band such as 51-200; repeat it for more. |
| `--location TEXT` | Where the company is based: "TX", "Austin, TX" or "Canada". Default: the US. |
| `--people TEXT` | The people list to buy. Default: `companies.people` in `config.toml`. |
| `--dry-run` | Count the companies and price the run without starting it. |
| `--resume INTEGER` | Finish an earlier company run, with the filters and people list it used. |

If Hunter finds nothing, the command says `No company matched these filters.
Try another sector ("outreach sectors <word>") or a wider size or location.`

The lines a run prints:

- `Run N: contacts bought at X companies, Y credits used. Report: <path>`
- `N companies were not bought because credits ran out.` (see
  [How buying works](#how-buying-works))

### `outreach sectors`

```bash
outreach sectors [WORD]
```

Lists Hunter's sector names, one per line (all of them without a word, or only
those containing the word, in any capitalisation), then the size bands. It is
offline and free. A word that matches nothing prints `No sector contains
"WORD".` and exits 1.

### `outreach emailed add`, `list` and `remove`

```bash
outreach emailed add ADDRESS... [--on YYYY-MM-DD]
outreach emailed list
outreach emailed remove ADDRESS...
```

- `add` marks addresses as emailed, today or on the day given with `--on`
  (`Marked 2 addresses as emailed on 2026-10-14.`). Any address works,
  including one the tool never found. Case and surrounding spaces don't matter.
- `list` shows who is marked, newest first: the date, the address and, when the
  tool knows them, the name, title and company.

  ```text
  2026-10-14  casey@marlow-robotics.example  Casey Mockup · Events Manager · Marlow Robotics
  2026-10-14  jamie@kestrel-analytics.example  Jamie Sample · Head of Partnerships · Kestrel Analytics
  ```

- `remove` takes addresses off the list (`Removed 1.`). It names any address
  that wasn't on the list, and exits 1 if it removed nothing.

A marked person shows "emailed <date>" on every later report and is never paid
for again. More than that, **a company where any address is marked is skipped
in later runs, before anything is searched**, because Hunter can't be told to
leave one person out of a search. The skip costs nothing, and the company is
listed with the reason.

### `outreach report`

```bash
outreach report RUN_ID
```

Writes a finished run's report again from what is stored (`Report: <path>`).
It makes no Hunter call and spends nothing. Useful if you deleted the file or
marked more people as emailed since. The run's HN funnel and error lines are
not stored, so the new file shows "not recorded" there.

## Filters

### Sector

Use Hunter's exact category names, such as "Software Development" or
"Marketing Services". Typing is forgiving about capitalisation (the value is
normalised to Hunter's spelling) but not about the words. A name that isn't on
the list stops the command with exit status 2 and suggests near matches:

```text
Unknown sector "Softwar". Did you mean: IT System Custom Software Development; Software Development; Data Security Software Products? Run "outreach sectors" to see them all.
```

Some of Hunter's names contain commas ("Health, Wellness and Fitness"), so
`--sector` is a repeated option, not a comma list: `--sector "Software
Development" --sector "Marketing Services"`. Several sectors, or several
sizes, are searched one combination at a time and merged.

The list is a copy of <https://hunter.io/files/industries.json> bundled in
`src/outreach/data/industries.json`, so listing and checking sectors works
offline. To refresh it, download that file again and replace the copy.

### Size

Hunter's bands, written exactly like this: `1-10`, `11-50`, `51-200`,
`201-500`, `501-1000`, `1001-5000`, `5001-10000`, `10001+`. Repeat `--size`
for more than one. Leave it out for any size. (This replaces the old
2,000-employee cap.)

### Location

`--location` is optional; without it a run is **US only**.

- **Two letters always mean a US state.** `TX` is Texas, `CA` is California
  (not Canada) and `IN` is Indiana.
- `City, ST` is a US city: `"Austin, TX"`. `"Texas"` and other full state
  names work too.
- A country is given by name: `Canada`, `United Kingdom`, or with a city,
  `"Toronto, Canada"`. `US`, `USA` and `United States` mean the whole US.
- Anything else exits with status 2 and an example of each form. The dry run's
  `Filters:` line shows how your value was read.

What the location means depends on the run:

- **On a company run, and for companies Hunter finds on a job run, it is the
  company's headquarters.** Hunter can filter only on that, so the report shows
  the requested place as the HQ of those companies.
- **For job postings (job boards and HN), it is the job's location.** A
  posting passes if its location falls in the place asked for. Inside the US a
  remote job is open to everyone, so a remote US job passes any US state or
  city. Outside the US even a remote posting has to name the country. A posting
  that lists several places counts as being in each state it names ("Austin,
  TX / New York" passes an Austin, TX search; "Austin, MN" does not).
- **Matching outside the US is by country name only.** A posting that says
  "Toronto" without "Canada" won't pass `--location Canada`.

### Work mode (job runs)

`--work-mode remote,hybrid,onsite` keeps postings in those modes (default: all
three). An unknown value exits with status 2: `unknown work mode 'remtoe';
expected one or more of: hybrid, onsite, remote`.

## People lists

A people list says whom to look for at each company. `config.toml` ships two,
and a run uses one of them, chosen by `--people` or by its command's default
(`[jobs] people = "hiring"`, `[companies] people = "sponsors"`).

- **Tiers.** Each tier is a list of job titles, most important tier first. A
  tier is one Hunter search by job title. A run searches tier by tier and stops
  as soon as it has `contacts_per_company` people, so the people in a lower tier
  are paid for only when the tiers above left a slot open. Titles that can name
  one person ("Founder & CTO") belong in the same tier, so that person isn't
  revealed twice.
- **Matching.** Every word of a title entry must appear as a whole word in the
  person's title, in any order and any capitalisation. "cto" matches
  "Co-founder & CTO" but not "Factory Manager".
- **Exclusions.** `exclude_titles` drops anyone whose title contains one of its
  entries, in any capitalisation: "recruit" also drops "Technical Recruiter".
  Hunter has already charged for an address by the time it is dropped, so
  `exclude_titles` is a safety net, not a way to save credits: put the titles
  you want in the tiers.
- **Order.** People on the report are ordered by tier, then by the order of
  titles in the tier. A person Hunter returned whose title matches none of the
  entries is still shown, last, because they were paid for.

The shipped lists:

```toml
[people.hiring]       # used by `outreach jobs`
tiers = [
    ["founder", "co-founder", "cto", "chief technology officer"],
    ["vp engineering", "vp of engineering", "head of engineering", "director of engineering"],
    ["engineering manager", "tech lead"],
]
exclude_titles = ["recruit", "talent", "sourcer"]

[people.sponsors]     # used by `outreach companies`
tiers = [
    ["partnerships", "sponsorships", "university relations", "university recruiting",
     "campus recruiting", "developer relations", "community"],
    ["events", "marketing manager", "head of marketing", "brand"],
    ["founder", "co-founder", "cto", "engineering manager"],
]
```

**Adding your own.** Add a `[people.<name>]` section to `config.toml` with
`tiers` (required) and optionally `exclude_titles`, then pick it per run with
`--people <name>` or make it a command's default:

```toml
[people.speakers]
tiers = [
    ["developer relations", "community", "events"],
    ["engineering manager", "cto"],
]

[companies]
people = "speakers"
```

A list in `config.toml` replaces the built-in list of the same name entirely;
it does not inherit the built-in list's exclusions. Names that aren't defined
anywhere stop the command (`Unknown people list "nobody". Known lists: hiring,
sponsors.`). Companies you have bought for one list are not skipped when you run
another list, so someone could be revealed again under a different list.

## How buying works

Every company the run found goes through these free checks, in this order. The
first one that applies decides what happens to it, and nothing is paid for it.

1. **Already on an earlier list.** A company whose people were bought in an
   earlier run **for the same people list** is not bought again. It is listed
   with that run's number and date. (A company where nobody was found, which
   cost nothing, can come back later.)
2. **Emailed.** A company where any address is on the emailed list is skipped,
   even if you added the address by hand.
3. **Over the cap.** A run buys at most `max_companies` (10) companies. The
   count is of companies where at least one person was bought, so a search that
   comes back empty doesn't use a slot and the next company takes it. Companies
   that came back empty in earlier runs are tried after fresh ones, so
   Hunter's fixed order doesn't put the same dead ends first every time. The
   rest are listed as left out. To buy at them, raise `max_companies` and run
   `--resume RUN_ID`.
4. **Domain not confirmed.** A board token's guessed domain whose homepage
   didn't answer.
5. **The free email count.** Hunter's count of personal addresses at the
   domain comes before any paid search. A company with zero is listed as "No
   one found" and costs nothing. The count is for the whole domain, not for
   your titles, so a company with people on file but none on your list is found
   out only by the search itself (which costs nothing when it returns nothing).

A company that gets through is bought:

1. **Reserve.** The worst case for the company (`contacts_per_company` x
   (`email_cost` + `verify_cost`)) is set aside first, and whatever isn't used
   is released afterwards.
2. **Search by title tier.** Tiers are searched in order, each asking only for
   the slots still open (see [People lists](#people-lists)). Every address
   Hunter returns costs `email_cost`, kept or not.
3. **Confirm addresses.** An address is shown only if it is confirmed.
   - An address confirmed in an earlier run is reused for free.
   - An address that Hunter's search marks `valid`, checked within the last 90
     days (`verified_max_age_days`), counts as confirmed with no further call.
   - Any other address is checked for `verify_cost` (0.5 credit).
   - An address that still isn't confirmed is never shown. The person is still
     listed, marked "address not confirmed", because they were paid for.

**Running out of credits.** The balance is read once, before the first
purchase. A company whose worst case no longer fits in what is left is listed
as left out ("not bought — out of credits"), nothing more is bought, and the
run ends with `N companies were not bought because credits ran out.` When your
credits renew, `outreach companies --resume RUN_ID` (or `outreach jobs
--resume RUN_ID`) finishes the run. If the balance can't be read at all,
nothing is bought and the report says why: Hunter reports the balance only on
plans with unified credits.

## Reading the report

Each run writes one self-contained HTML file to `reports/`, named like
`2026-10-14-run1-companies-software-development.html` (date, run number, kind
and what the run was for).

**Header.** The kind of run ("Company run", or the role followed by "job run"),
your filters as read, the people list, the run number and date, and the
credits: `10 credits used · 40 left`.

**Sections, in order:**

1. **With contacts.** One card per company.
2. **No one found.** Companies with nobody on your list, each with a reason:
   `Hunter has no one matching "sponsors" — cost nothing` when the free count
   was zero, or the same without "cost nothing" when a paid search came back
   empty (everyone it returned may have been an excluded title).
3. **Already on an earlier list.** Companies skipped because an earlier run
   bought them (`on run 3, 14 Oct 2026 — not bought again`) or because you
   emailed someone there (`you emailed pat@alder-pine.example on 20 Sep 2026`).
4. **Left out,** each with its reason: `doesn't match your sector or size`; `no
   full-time posting in the requested work mode or location`; `over this run's
   cap of 10 companies`; `over this run's HN cap of 15`; `not bought — out of
   credits`; `its website didn't answer, so its domain couldn't be confirmed`;
   `failed — <the error>`; or `not reached` (the run stopped before it got
   there).
5. **Run details.** Companies found by source, emails revealed, addresses
   checked, credits and errors, and for a job run the HN funnel (thread, posts
   read and matching the role, parsed by rules or by the AI, filtered out, kept
   or over the cap).

**A card** shows the company name, domain, sector, size band and headquarters.
A card labelled "sector unknown" is a job-run company Hunter has no data on.
On a job run it also shows the posting (title, work mode and a link, "via job
board" or "via HN Who is hiring? (October 2026)"). A company Hunter found as
hiring has no posting link and says "Hiring <role>, per Hunter".

Up to `contacts_per_company` people follow, each with:

- name and title;
- the confirmed email address with a Copy button, or "address not confirmed";
- a LinkedIn link when Hunter supplied one, otherwise a "Search LinkedIn" link,
  which is only a people search for the name and company, never a found profile;
- "emailed <date>" if you marked them.

An address is shown only when Hunter confirmed it. A bounce harms the domain you
send from, so an unconfirmed address is never presented as a working one.

## `config.toml` reference

`config.toml` is read from the folder you run `outreach` in. Everything is
optional except `[paths]`, and a missing required key or a mistyped setting
(`max_companys`) stops the command with a message naming it. Each setting in
the shipped file is at its default, except the paths and the job-board tokens.

| Section | Key | Default | Meaning |
| --- | --- | --- | --- |
| `[paths]` | `db` | required | The SQLite file that remembers runs, companies and who has been emailed. |
| | `reports` | required | The folder each report is written to. |
| `[discovery]` | `greenhouse_tokens`, `ashby_tokens`, `lever_tokens` | `[]` | Job-board tokens a job run searches: the `<token>` in `boards.greenhouse.io/<token>`, `jobs.ashbyhq.com/<token>`, `jobs.lever.co/<token>`. Company runs ignore them. The shipped file lists three Greenhouse tokens as an example; replace them with your own. |
| `[hn]` | `enabled` | `true` | `false` skips the "Who is hiring?" thread. |
| | `max_new_companies` | `15` | Most HN companies a job run keeps, in thread order. |
| | `llm_fallback_max` | `20` | AI calls per run to read posts the rules can't parse. |
| `[hunter]` | `email_cost` | `1` | Credits per revealed address. |
| | `verify_cost` | `0.5` | Credits per address check. Costs are in steps of 0.1. |
| | `verified_max_age_days` | `90` | A `valid` status older than this is checked again. |
| `[run]` | `max_companies` | `10` | Companies a run buys at (1 or more). |
| | `contacts_per_company` | `3` | People per company, 1 to 10 (Hunter's free plan returns at most 10 per search). |
| `[jobs]` | `people` | `"hiring"` | The people list `outreach jobs` uses. |
| `[companies]` | `people` | `"sponsors"` | The people list `outreach companies` uses. |
| `[people.<name>]` | `tiers`, `exclude_titles` | | See [People lists](#people-lists). |

Settings from earlier versions (`[gate]`, `[ranking]`, `[stage]`, the old keys
in `[discovery]`, `[hunter]` and `[hn]`, and `[paths] cache`) are ignored with
a single warning rather than failing:
`Warning: config.toml settings this version no longer uses were ignored: ...`.

## AI calls

There are exactly two AI calls, both in job runs. Company runs make none.

- `expand_titles`: turns your role into similar job titles to search for. One
  call per job run, including a dry run.
- `parse_job_post`: reads an HN post the rules couldn't parse, at most
  `llm_fallback_max` per run and never in a dry run. Every field it returns
  must appear in the post itself, or the post is dropped.

Neither decides who or what is bought. Sector, size, location, the cap, the
people lists, the budget and every address check are plain code and Hunter.

## Tests

```bash
pip install -e ".[dev]"
.venv/Scripts/python -m pytest -q    # Windows; .venv/bin/python on macOS/Linux
```

Every test runs against fakes and recorded fixtures; none touches a live API or
your `.env`. The CLI tests patch out `.env` loading and run in a scratch
directory, so they never see your real keys, config or database.

## Older runs

Runs from earlier versions of this tool stay in the database untouched.
`outreach report` can't rebuild them in the new layout: for one of those it says
`Run N was made by an older version of this tool; see its saved HTML report.` and
exits 1. Their original HTML files in `reports/` are the record. People bought
in those runs count as earlier `hiring` runs, so they aren't bought again. The
older design documents under `docs/superpowers/specs/` are marked superseded;
the current design, with an "As built" section listing what changed during
implementation and what is still unconfirmed about Hunter, is
`docs/superpowers/specs/2026-10-11-contact-finder-design.md`.
