# Getting started

This guide takes you from nothing installed to a list of companies, the right
people at each, and their confirmed email addresses, using only a free Hunter
account. It takes about fifteen minutes. The tool never writes or sends email:
you write every message yourself.

The output shown below comes from the real commands run against test doubles
standing in for Hunter, filled with made-up data: every company and person is
invented, and the addresses end in `.example`. Yours will differ. On Windows,
file paths in the output use backslashes.

## 1. Install

You need Python 3.11 or newer. Check with `python3 --version` (macOS and Linux)
or `py --version` (Windows). If it is older or missing, install the latest
Python from <https://www.python.org/downloads/>.

Get the project folder from whoever shared it with you: either
`git clone <the repository's link>`, or download it as a ZIP from the
repository's page (Code, then Download ZIP) and unzip it. Open a terminal and
go into that folder; every command below is run from there.

**macOS or Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

If PowerShell says running scripts is disabled, skip the activate line and put
`.venv\Scripts\` in front of the command each time instead: `pip` becomes
`.venv\Scripts\pip` and `outreach` becomes `.venv\Scripts\outreach`.

Check that it worked:

```bash
outreach --help
```

You will see the list of commands. Whenever you open a new terminal later, go
to the project folder and run the activate line again.

## 2. Make a Hunter account and copy your key

Hunter is the service that finds the people and their email addresses.

1. Sign up for a free account at <https://hunter.io>. The free plan includes 50
   credits a month, which is enough to start (see
   [What 50 credits buy](#9-what-50-credits-buy) below).
2. Open <https://hunter.io/api-keys> and copy your API key, a long string of
   letters and numbers. Treat it like a password.

## 3. Put the key in `.env`

Make a copy of the example file named `.env` (the dot at the start matters):

```bash
cp .env.example .env
```

On Windows use `copy .env.example .env`. Open `.env` in any text editor. It has
three keys; for a company run you only need the first. Paste your key after the
equals sign, with no spaces or quotes, and leave the others empty:

```text
HUNTER_API_KEY=paste-your-key-here
ANTHROPIC_API_KEY=
OUTREACH_CONTACT_EMAIL=
```

`.env` holds your private key, so **never commit it, send it to anyone or paste
it into a chat.** The project already tells git to ignore it. If you ever put
the folder on GitHub, run `git status` first and check `.env` isn't listed.

## 4. Pick a sector

Hunter sorts companies into its own sector names, and the tool needs the exact
name. Search the list with a word:

```bash
outreach sectors marketing
```

```text
Marketing Services
Size bands: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+
```

The last line lists the company size bands you can ask for (`--size 51-200`).
Try other words (`outreach sectors food`) until you find the sector you want,
and copy the name exactly. This step is free and needs no internet.

## 5. Keep your first run small

Open `config.toml` in the project folder and find the `[run]` section. Change
`max_companies` from 10 to 3, so a run buys people at no more than 3 companies:

```toml
[run]
max_companies = 3                # companies a run buys contacts at; the rest are listed as left out
```

(Leave the other lines as they are.) With three people per company, that is
at most 13.5 credits. You can raise it once you've seen how it works.

## 6. Do a dry run first

A dry run asks Hunter which companies match and works out what the real run
would cost. It spends nothing. Replace `TX` with your state, or leave out
`--location TX` to search the whole US:

```bash
outreach companies --sector "Marketing Services" --location TX --dry-run
```

```text
Filters: Marketing Services · any size · Texas
Hunter found 6 companies.
Skipped: 0 on an earlier list, 0 where you've emailed someone, 1 with no one matching.
Dry run: up to 3 companies would be bought, at most 13.5 credits; 50 left.
```

- The first line shows how the tool understood your filters. Two letters are
  always a US state (`CA` is California). To search outside the US, give the
  country's name, such as `--location Canada`.
- `Hunter found 6 companies` is the number of companies matching your filters.
- `Skipped` counts companies it won't pay for: ones from an earlier run, ones
  where you've already emailed someone, and ones where Hunter has no one to
  find. They're all zero on your first run, apart from companies with nobody.
- The last line is the most the run can cost and how many credits you have
  left. If it says "up to 3 companies", the cap from step 5 is in effect.

If Hunter finds nothing, you'll be told to try another sector or a wider size or
location.

## 7. Run it for real

Run the same command without `--dry-run`:

```bash
outreach companies --sector "Marketing Services" --location TX
```

```text
Run 1: contacts bought at 3 companies, 10 credits used. Report: reports/2026-10-14-run1-companies-marketing-services.html
```

It used 10 credits, less than the worst case of 13.5 the dry run showed, because
Hunter had already confirmed some addresses. The report is a web page saved in
the `reports` folder.

## 8. Open the report and reach out

Open the file from the last line in your web browser. In a terminal:

- macOS: `open reports/<the file name>.html`
- Windows: `start reports\<the file name>.html`
- Linux: `xdg-open reports/<the file name>.html`

You'll see one card per company with up to three people: their name, their title
and their email address with a Copy button. An address appears only when Hunter
has confirmed it works. If a person says "address not confirmed", don't guess
one; use the LinkedIn link beside it instead (a "Search LinkedIn" link opens a
search for them, not their profile). Below the cards are the companies that had no one to find and
the ones that were left out, each with the reason.

Now write your emails, by hand, in your own email program.

**Mark the people you emailed.** This keeps them, and anyone else at their
company, out of future lists, so nobody is emailed or paid for twice:

```bash
outreach emailed add jamie@kestrel-analytics.example
```

```text
Marked 1 address as emailed on 2026-10-14.
```

List all the addresses you've marked with `outreach emailed list`:

```text
2026-10-14  jamie@kestrel-analytics.example  Jamie Sample · Head of Partnerships · Kestrel Analytics
```

If you marked one by mistake, `outreach emailed remove <address>` takes it off.
Run the dry run again and you'll see the effect: the companies you've already
had are skipped, so the next real run moves on to new ones.

```text
Skipped: 3 on an earlier list, 0 where you've emailed someone, 1 with no one matching.
Dry run: up to 2 companies would be bought, at most 9 credits; 50 left.
```

## 9. What 50 credits buy

- Revealing an email address costs 1 credit. Checking an address that Hunter
  hasn't already confirmed costs 0.5.
- Searching for companies and counting the people at a company are free, so a
  dry run costs nothing.
- At three people per company, the worst case is 4.5 credits per company, so
  50 credits buy about **11 companies** a month. When Hunter has confirmed
  an address recently it needs no check, and a company can cost as little as 3
  credits.
- Credits come back each month on the free plan. The dry run always shows how
  many you have left, and the report's top line shows what a run used.
- If you run out partway, the run stops buying and lists the rest as "not
  bought — out of credits". When your credits are back, finish it with
  `outreach companies --resume 1` (use the run number it printed).

## When something goes wrong

| What you see | What to do |
| --- | --- |
| `config file not found: config.toml` | You aren't in the project folder. `cd` into it and run the command again. |
| `HUNTER_API_KEY is not set. Add it to .env ...` | The `.env` file is missing, is in the wrong folder, or the key line is empty. It must be called `.env` and sit next to `config.toml`. |
| `Hunter's company search failed: Hunter discover returned HTTP 401` | Hunter didn't accept the key. Copy it again from <https://hunter.io/api-keys>. |
| `... returned HTTP 429` | Your Hunter plan's monthly limit is used up. Wait for it to renew. |
| `... returned HTTP 403` | Hunter refused the call, usually because it was too many in a short time. Wait a minute and try again. |
| `Unknown sector "..."` | Use a name from `outreach sectors <word>`, copied exactly. |
| `Can't read location "..."` | Use a state (`TX`), a city and state (`"Austin, TX"`) or a country name (`Canada`). |

The README has the full details: every command and filter, how the people are
chosen and edited, and what each setting in `config.toml` does.
