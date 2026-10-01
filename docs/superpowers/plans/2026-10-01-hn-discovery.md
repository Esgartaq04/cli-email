# HN Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `outreach run` also discovers companies from the latest HN "Ask HN: Who is hiring?" thread. It parses posts deterministically, with a capped and validated LLM fallback, filters and pre-ranks them for free, caps them, and feeds them into the Spec A pipeline.

**Architecture:**
- A pure parser in `core/hnparse.py`.
- A thin HN fetcher in `sources/hn.py`, plus a fake.
- A fourth LLM method, `parse_job_post`, whose output is always validated against the post.
- A discovery orchestrator in `pipeline/discovery.py` that returns a capped selection.

The runner merges HN postings with config-token postings before the existing discover loop. It also ingests each kept post as an `hn_post` document during research. Storage moves to v3 with `run_hn_posts`.

**Tech Stack:** Python 3.11+, httpx, Jinja2, Typer, SQLite, pytest. Run tests with `.venv/Scripts/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-10-01-hn-discovery-design.md`. Read it together with Spec A's **As built** section in `docs/superpowers/specs/2026-09-30-startup-stage-targeting-design.md`.

## Global Constraints

- **No test touches a live API.** Never read `.env`, never open `data/pipeline.db`, and never run `outreach run` without `OUTREACH_FAKE_ADAPTERS=1`. CLI tests stay under the autouse isolation fixture in `tests/test_cli.py`.
- `core/` imports nothing that touches the network or disk. HTML-to-text for posts is done in `core/hnparse.py` with the stdlib `html.parser`.
- HN is additive. Any HN failure appends to `summary.errors` with the prefix `hn: ` and the run continues with the config tokens.
- Defaults: `[hn] enabled = true`, `max_new_companies = 15`, `llm_fallback_max = 20`, `recheck_days = 30`.
- The LLM fallback is called only for posts that match the role and failed deterministic parsing, at most `llm_fallback_max` times per run. Its output is validated, never trusted. It never decides stage, size or evidence sufficiency.
- No Hunter call is made for a `skipped_cap` or `skipped_recent` company.
- `hn_post` documents use publisher `news.ycombinator.com`, are dated by the post's `created_at`, and count as first-party. An `hn_post` fetch never confirms a company's domain.
- ATS hosts are `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.ashbyhq.com` and `jobs.lever.co`. The board kinds are `greenhouse`, `ashby` and `lever`.
- These hosts are never a company domain:
  - HN and Algolia: `news.ycombinator.com`, `ycombinator.com`, `hn.algolia.com`.
  - The ATS hosts.
  - `github.com`, `linkedin.com`, `twitter.com`, `x.com`, `docs.google.com`, `forms.gle`, `calendly.com`, `notion.so`, `notion.site`, `wellfound.com`, `angel.co`.
  - Link shorteners: `bit.ly`, `lnkd.in`, `t.co`, `tinyurl.com`.
  - Subdomains of any of the above.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on `feat/hn-discovery`; never push without being asked.

## Review Focus

1. **A company linked only through a shortener or tracking link** (`bit.ly/...`, `lnkd.in/...`) must give no domain, so the post goes to the validated fallback rather than being researched as `bit.ly`. Test: Task 2 `test_shortener_links_are_never_the_company_domain`.
2. **The same company posting twice in the thread,** or two posts linking one domain, must become one company with one kept post (the first). Test: Task 5 `test_one_company_per_domain_first_post_wins`.
3. **An ATS board with no posting for the role** must fall back to the HN post as the posting, so the company isn't excluded. Test: Task 6 `test_ats_board_without_the_role_falls_back_to_the_post`.
4. **An HN company already found through a config token** must not be counted against the cap or duplicated. Its post is still ingested as evidence. Test: Task 6 `test_config_token_company_is_not_capped_but_gets_its_post`.
5. **HTML entities in hrefs** (`https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;acme`) must decode before link parsing. Test: Task 2 `test_entity_encoded_links_decode`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/outreach/types.py` (modify) | `HNPost`, `HNThread`, `ParsedPost`; `SourceClass.HN_POST`; new `StageStatus` values |
| `src/outreach/config.py` (modify) | `HNConfig` and the optional `[hn]` section |
| `src/outreach/core/hnparse.py` (new) | Pure: post text, links, ATS, company domain, the deterministic parse, the LLM-output validator, stage hints |
| `src/outreach/sources/hn.py` (new) | `HNThreadSource(fetcher)` and `FakeHNSource`: find the latest thread and fetch its posts |
| `src/outreach/llm/{base,anthropic_client,fake}.py` (modify) | Fourth method `parse_job_post` |
| `src/outreach/store/migrations.py`, `runs.py`, `dimensions.py` (modify) | v3 `run_hn_posts`; `HNPostRepo`; `RunRepo.researched_since`; `CompanyRepo.find` |
| `src/outreach/pipeline/discovery.py` (new) | `select_hn_candidates`, `HNSelection`, `hn_board_source` |
| `src/outreach/pipeline/runner.py`, `context.py` (modify) | Merge HN into discover; record skips; ingest `hn_post`; skip refetching HN postings; off-domain rule |
| `src/outreach/render/{view,report}.py`, `templates/report.html.j2` (modify) | "via HN" link, exclusion reasons, diagnostics |
| `src/outreach/cli.py`, `config.toml`, `README.md` (modify) | `--no-hn`, wiring, dry run, warning text, docs |

---

### Task 1: Types and config

**Files:**
- Modify: `src/outreach/types.py`, `src/outreach/config.py`, `config.toml`
- Test: `tests/test_types.py`, `tests/test_config.py`

**Interfaces:**
- Produces (`types.py`):
  - `@dataclass(frozen=True) HNPost(item_id: int, posted_at: datetime, html: str)`
  - `@dataclass(frozen=True) HNThread(item_id: int, title: str, posts: tuple[HNPost, ...])`
  - `@dataclass(frozen=True) ParsedPost(company: str, domain: str, ats: tuple[str, str] | None, role: str, location: str, work_mode: WorkMode, employment_type: EmploymentType)`, where `ats` is `(kind, token)`.
  - `SourceClass.HN_POST = "hn_post"`.
  - `StageStatus` gains `"skipped_cap"` and `"skipped_recent"`.
- Produces (`config.py`): `HNConfig(enabled: bool = True, max_new_companies: int = 15, llm_fallback_max: int = 20, recheck_days: int = 30)`, and `Config.hn: HNConfig = HNConfig()`. The `[hn]` section is optional, as is each of its keys.

- [ ] **Step 1: Write failing tests.**
  - `test_hn_section_is_optional_and_defaults`: the base TOML gives `cfg.hn == HNConfig()`.
  - `test_hn_section_is_read`: `[hn]` with `enabled = false` and `max_new_companies = 5` is read back.
  - `test_hn_post_source_class_value`: `SourceClass("hn_post") is SourceClass.HN_POST`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_config.py tests/test_types.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** In `config.toml`:
  - add `"hn_post"` to `first_party_classes`;
  - add a commented `[hn]` block showing the defaults.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `feat: types and config for HN discovery`.

---

### Task 2: Pure post parser

**Files:**
- Create: `src/outreach/core/hnparse.py`
- Test: `tests/core/test_hnparse.py`

**Interfaces:**
- Consumes: `ParsedPost`, `SourceClass`, `EvidenceItem`, `WorkMode` (Task 1). From `core.workmode`: `classify_work_mode`, `classify_employment`. From `core.stage`: `parse_round`, `parse_headcount_statement`. From `core.dedupe`: `canonical_domain`.
- Produces:
  - `post_text(html: str) -> str`: plain text with entities decoded and `<p>` turned into newlines. The first line is the text before the first `<p>`.
  - `post_links(html: str) -> list[str]`: decoded `href` values in document order.
  - `ats_from_links(links: Sequence[str]) -> tuple[str, str] | None`: the first ATS link as `(kind, token)`, where the token is the first path segment, lowercased.
  - `company_domain(links: Sequence[str], first_line: str) -> str | None`: the first link whose host isn't a non-company host, canonicalized. Failing that, a bare `word.tld` token in `first_line`, matched by `\b[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}\b` after removing URLs.
  - `parse_post(html: str) -> ParsedPost | None`: the deterministic parse described in spec step 3.
  - `validate_llm_post(candidate: ParsedPost, html: str) -> ParsedPost | None`: the checks in spec step 4. A company or role longer than 120 characters is rejected. The returned post has `domain` canonicalized.
  - `hint_signals(text: str, posted_at: date) -> list[EvidenceItem]`: one `EvidenceItem(None, 0, 0, "", sentence, SourceClass.HN_POST, "news.ycombinator.com", posted_at, theme)` for each sentence where `parse_round` matches (theme `funding-round`) or `parse_headcount_statement` matches (theme `headcount-statement`).
- Segment rule for the first line split on `|` (segments stripped; index 0 is the company):
  1. Drop the noise segments from the rest:
     - work-mode-only segments: only the words remote, onsite, on-site, hybrid, in office, plus separators;
     - employment-only segments: full-time, part-time, contract, intern, internship;
     - salary segments, matching `[$€£]|\d\s*k\b`.
  2. Of what remains, a segment is **location-like** when any of these holds:
     - `sources.jobboards.region.matches_region(seg, "US")` is true (that module is pure regex, so importing it from `core/` is allowed);
     - it contains a `_NON_US_MARKERS` word;
     - it contains "remote".
  3. `location` is the first location-like segment, or `""` when there is none. `role` is the first segment that isn't location-like, or `""` when there is none.
  4. Work mode and employment are classified over the whole first line with `classify_work_mode(location, role, first_line)` and `classify_employment(role, first_line)`.
  5. **Parsed** requires the company, the domain, a non-empty role, and a location or a non-`unknown` work mode.

- [ ] **Step 1: Write failing tests.** Inline post HTML strings in the test file, modelled on the real thread format.

```python
def test_pipe_format_with_ashby_link_parses():
    html = ('Acme Robotics | Senior Backend Engineer | San Francisco, CA | ONSITE | Full-time'
            '<p>We build warehouse robots. Apply: <a href="https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;acme&#x2F;123">'
            'https://jobs.ashbyhq.com/acme/123</a> More at <a href="https://acmerobotics.com">acmerobotics.com</a>')
    p = parse_post(html)
    assert (p.company, p.domain, p.ats) == ("Acme Robotics", "acmerobotics.com", ("ashby", "acme"))
    assert (p.role, p.location, p.work_mode, p.employment_type) == (
        "Senior Backend Engineer", "San Francisco, CA", "onsite", "full_time")

@pytest.mark.parametrize("href,expected", [
    ("https://boards.greenhouse.io/zeta/jobs/1", ("greenhouse", "zeta")),
    ("https://job-boards.greenhouse.io/Zeta/jobs/1?gh_src=x", ("greenhouse", "zeta")),
    ("https://jobs.lever.co/findigs/abc", ("lever", "findigs")),
    ("https://jobs.ashbyhq.com/stream?utm_source=x", ("ashby", "stream")),
    ("https://www.oysterhr.com/careers?ashby_jid=51d5", None),
])
def test_ats_from_links(href, expected): assert ats_from_links([href]) == expected

def test_entity_encoded_links_decode():
    assert post_links('<a href="https:&#x2F;&#x2F;jobs.lever.co&#x2F;x">y</a>') == ["https://jobs.lever.co/x"]

def test_shortener_links_are_never_the_company_domain():
    assert company_domain(["https://bit.ly/abc", "https://lnkd.in/x"], "Acme | Engineer | Remote") is None

def test_non_company_hosts_are_skipped_for_the_domain():
    links = ["https://github.com/acme", "https://www.linkedin.com/company/acme", "https://acme.dev/jobs"]
    assert company_domain(links, "Acme | Engineer") == "acme.dev"

def test_bare_domain_in_the_first_line_counts():
    assert company_domain([], "Acme (acme.io) | Engineer | REMOTE (US)") == "acme.io"

def test_salary_segments_are_not_locations():
    p = parse_post('Acme | Backend Engineer | Remote (US) | $150k-$190k | Full-time<p><a href="https://acme.io">x</a>')
    assert p.location == "Remote (US)" and p.work_mode == "remote"

def test_email_only_post_without_a_domain_is_unparsed():
    assert parse_post("Acme | Engineer | NYC<p>Email jobs at acme dot com") is None

def test_free_text_post_without_pipes_is_unparsed():
    assert parse_post('We are hiring engineers in Berlin! <a href="https://acme.de">acme.de</a>') is None

def test_validator_rejects_invented_fields():
    html = 'Acme is hiring a Backend Engineer in Austin, TX, remote ok <a href="https://acme.io">site</a>'
    good = ParsedPost("Acme", "acme.io", None, "Backend Engineer", "Austin, TX", "remote", "unknown")
    assert validate_llm_post(good, html) == good
    for bad in (replace(good, domain="other.io"), replace(good, company="Globex"),
                replace(good, role="Staff ML Engineer"), replace(good, location="Denver, CO"),
                replace(good, work_mode="hybrid")):
        assert validate_llm_post(bad, html) is None

def test_hint_signals_find_round_and_headcount():
    hints = hint_signals("We raised a $30M Series B last spring. We're a team of 120 people.", date(2026, 9, 1))
    assert sorted(h.theme for h in hints) == ["funding-round", "headcount-statement"]
    assert all(h.source_class is SourceClass.HN_POST and h.published_at == date(2026, 9, 1) for h in hints)
```
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/core/test_hnparse.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `core/hnparse.py` per the interfaces above.
- [ ] **Step 4: Run** the same command, then the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: pure HN post parser and LLM-output validator`.

---

### Task 3: HN thread source

**Files:**
- Create: `src/outreach/sources/hn.py`, `tests/fixtures/hn_search.json`, `tests/fixtures/hn_thread.json`
- Test: `tests/sources/test_hn.py`

**Interfaces:**
- Consumes: `Fetcher`, `HNPost`, `HNThread`.
- Produces:
  - `class HNSource(Protocol): def latest_thread(self) -> HNThread | None: ...`
  - `HNThreadSource(fetcher: Fetcher)`. Its `latest_thread()` makes two calls:
    - `GET https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10` picks the newest hit whose `title` starts with `Ask HN: Who is hiring?`;
    - `GET https://hn.algolia.com/api/v1/items/{id}` keeps top-level `children` with a non-empty `text`, and gets `posted_at` from `created_at`.

    It returns `None` when no such story exists. It raises `HNUnavailable(message)` on a non-ok fetch or malformed JSON; the message names the URL path and outcome only.
  - `FakeHNSource(thread: HNThread | None = None, error: Exception | None = None)`. Its `.calls: int` counts calls. It raises `error` when set.
- Fixtures:
  - `hn_search.json` has 3 hits: a "Who wants to be hired? (September 2026)" story that is newer by seconds, "Who is hiring? (September 2026)" with id 49522897, and the August thread.
  - `hn_thread.json` is the item tree with 12 top-level children:
    1. pipe + Ashby + company link (Series B mention)
    2. pipe + Greenhouse
    3. pipe + Lever
    4. pipe + own site only (team of 40)
    5. pipe + `?ashby_jid` careers link
    6. email-only
    7. non-US (Berlin)
    8. free text with a company link
    9. a deleted child (`text: null`)
    10. a second post for the same domain as #1
    11. a role that doesn't match ("Product Designer")
    12. pipe + shortener link only

    Plus one nested reply under #1, which must be ignored.

- [ ] **Step 1: Write failing tests.**
  - `test_picks_who_is_hiring_not_wants_to_be_hired`: `thread.item_id == 49522897`.
  - `test_keeps_only_top_level_posts_with_text`: `len(thread.posts) == 11`, and the nested reply is absent.
  - `test_posted_at_parses`.
  - `test_no_thread_returns_none`: the search returns no matching title.
  - `test_fetch_failure_raises_hn_unavailable`: a 500, and the message doesn't contain the full query string.
  - `test_fake_counts_calls_and_raises`.

  Use `httpx.MockTransport` serving the fixtures and a 404 for robots.txt, as `tests/conftest.py` does for boards.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/sources/test_hn.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: HN Who-is-hiring thread source`.

---

### Task 4: Fourth LLM method `parse_job_post`

**Files:**
- Modify: `src/outreach/llm/base.py`, `src/outreach/llm/anthropic_client.py`, `src/outreach/llm/fake.py`
- Test: `tests/llm/test_anthropic_client.py`, `tests/llm/test_fake.py` (new)

**Interfaces:**
- Produces:
  - `LLMClient.parse_job_post(text: str) -> ParsedPost | None`. The protocol docstring becomes: "Exactly four jobs. The gate, stage and size are not among them; parse_job_post only extracts fields that must appear in the post."
  - `AnthropicLLM.parse_job_post`: strict JSON `{"company","domain","ats_kind","ats_token","role","location","work_mode","employment_type"}`, or `null` when the post isn't a job post. Fixed copy for `_PARSE_SYSTEM`: "You extract the hiring company and one job from a Hacker News 'Who is hiring?' post. Copy every field verbatim from the post text — never infer, translate or invent. company: the company name as written. domain: the company's own website domain as it appears in the post. ats_kind/ats_token: only if the post links jobs.ashbyhq.com, boards.greenhouse.io, job-boards.greenhouse.io or jobs.lever.co (kind is ashby, greenhouse or lever; token is the first path segment), else null. role: one role title as written. location: as written. work_mode: remote, hybrid, onsite or unknown. employment_type: full_time, other or unknown. Respond with strict JSON only: one object with exactly those keys, or null." It reuses `_call_json`. A non-dict or `null` reply gives `None`. Unknown `work_mode`/`employment_type` values map to `"unknown"`.
  - `FakeLLM(..., parsed_posts: dict[str, ParsedPost] | None = None)`. `parse_job_post(text)` returns the value of the first key that is a substring of `text`, else `None`, and increments `.parse_calls`.
- [ ] **Step 1: Write failing tests.**
  - `test_parse_job_post_maps_json`: the mock transport returns a JSON object, and the test asserts the `ParsedPost` fields.
  - `test_parse_job_post_null_is_none`.
  - `test_parse_job_post_unknown_mode_is_unknown`.
  - `test_fake_parse_job_post_matches_by_substring_and_counts`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/llm -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: parse_job_post -- the fourth, validated LLM method`.

---

### Task 5: Store v3 and the discovery orchestrator

**Files:**
- Modify: `src/outreach/store/migrations.py`, `src/outreach/store/runs.py`, `src/outreach/store/dimensions.py`, `src/outreach/types.py`, `src/outreach/pipeline/runner.py` (RunSummary fields only), `src/outreach/pipeline/context.py`, `tests/pipeline/factories.py`
- Create: `src/outreach/pipeline/discovery.py`
- Test: `tests/store/test_db.py`, `tests/store/test_runs.py`, `tests/pipeline/test_discovery.py` (new)

**Interfaces:**
- Consumes: Tasks 1–4. From `core.stage`: `classify_stage`, `stage_sort_key`. From `core.workmode`: `posting_matches`. `region.matches_region`.
- Produces (store):
  - `MIGRATIONS[1]` (to v3): `CREATE TABLE run_hn_posts (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), company_id INTEGER NOT NULL REFERENCES companies(id), item_id INTEGER NOT NULL, thread_title TEXT NOT NULL, posted_at TEXT NOT NULL, parse_method TEXT NOT NULL CHECK (parse_method IN ('parsed','llm')), status TEXT NOT NULL CHECK (status IN ('kept','skipped_cap','skipped_recent')), UNIQUE (run_id, item_id))`, followed by `PRAGMA user_version = 3`. Same BEGIN/COMMIT pattern as before.
  - `@dataclass(frozen=True) HNPostRecord(item_id: int, thread_title: str, posted_at: date, parse_method: str, status: str)` lives in `types.py`.
  - `HNPostRepo(conn)` with `insert(run_id, company_id, record: HNPostRecord) -> None` (INSERT OR IGNORE) and `for_company(run_id, company_id) -> HNPostRecord | None`.
  - `RunRepo.researched_since(company_id: int, since: datetime, exclude_run_id: int) -> bool`. True when some other run started at or after `since` has an `evidence` stage `ok` for the company.
  - `CompanyRepo.find(domain: str) -> Company | None`, which canonicalizes first.
- Produces (`pipeline/discovery.py`):
  - `@dataclass(frozen=True) HNCandidate(post: HNPost, parsed: ParsedPost, method: str, stage_hint: StageResult, headcount_hint: int | None)`
  - `@dataclass HNSelection(thread: HNThread | None, kept: list[HNCandidate], known: list[HNCandidate], skipped_recent: list[HNCandidate], skipped_cap: list[HNCandidate], needs_llm: int = 0)`. Here `known` holds the candidates whose domain is already in `known_domains`. They are not ranked, capped or counted in `hn_kept`.
  - `select_hn_candidates(ctx: RunContext, run_id: int, terms: Sequence[str], work_modes: frozenset[WorkMode], known_domains: frozenset[str], summary: RunSummary, allow_llm: bool = True) -> HNSelection`

`select_hn_candidates` behaviour, in order:
1. `thread = ctx.hn.latest_thread()`. On `None`, append `"hn: no Who is hiring? thread found"` and return an empty selection.
2. Count `hn_posts_read`.
3. Prefilter on any term (lowercased) appearing in `post_text`, and count `hn_role_matched`.
4. `parse_post` gives `method="parsed"` and counts `hn_parsed`. Otherwise:
   - if `allow_llm` and the fallback budget remains, call `ctx.llm.parse_job_post(post_text)` then `validate_llm_post`; a valid result gives `"llm"` and counts `hn_llm_parsed`, while an invalid result or an exception counts `hn_llm_rejected`;
   - if not `allow_llm`, count `needs_llm`.
5. Drop any candidate where `matches_region(location, config.discovery.region)` is false or `posting_matches(PostingRef(...), work_modes)` is false, counting `hn_filtered`.
6. Dedupe by domain, first post wins.
7. A domain in `known_domains` goes to `known`. It is not ranked, recency-checked or capped.
8. `stage_hint = classify_stage(None, hint_signals(text, posted_at.date()), ctx.today, ctx.config.stage)`. `headcount_hint` is the maximum of `parse_headcount_statement` over the text.
9. Sort by `(stage_sort_key(stage_hint.stage, headcount_hint), thread order)`.
10. A company with `CompanyRepo.find(domain)` set and `researched_since(id, now - recheck_days, run_id)` goes to `skipped_recent`.
11. The first `max_new_companies` go to `kept`; the rest go to `skipped_cap`.
12. Set `summary.hn_thread_title`, `hn_skipped_recent`, `hn_skipped_cap` and `hn_kept`.

The `RunSummary` fields `hn_thread_title: str | None = None`, `hn_posts_read`, `hn_role_matched`, `hn_parsed`, `hn_llm_parsed`, `hn_llm_rejected`, `hn_filtered`, `hn_skipped_recent`, `hn_skipped_cap` and `hn_kept` (all `int = 0`) are added in `runner.py` in this task. `RunContext` gains `hn: HNSource | None = None`, `hn_posts: HNPostRepo | None = None`, appended last with defaults. `tests/pipeline/factories.py` passes `FakeHNSource()` and `HNPostRepo(conn)`.

- [ ] **Step 1: Write failing tests.**
  - Store tests:
    - `test_migration_to_v3_from_v2_and_v1` keeps contact history and reaches `user_version` 3;
    - `test_hn_post_record_round_trips_and_dedupes`;
    - `test_researched_since_ignores_this_run_and_old_runs`;
    - `test_company_find_canonicalizes`.
  - Discovery tests, using a `FakeHNSource` built from fixture-shaped posts and `FakeLLM(parsed_posts=...)`:

```python
def test_prefilter_runs_before_any_llm_call(): ...        # "Product Designer" post: FakeLLM.parse_calls stays 0
def test_llm_fallback_is_capped(): ...                     # llm_fallback_max=1 with 2 unparsed matching posts → parse_calls == 1
def test_llm_output_that_fails_validation_is_dropped(): ... # FakeLLM returns invented domain → hn_llm_rejected == 1, not kept
def test_non_us_posts_are_filtered(): ...                  # Berlin post → hn_filtered counts it
def test_one_company_per_domain_first_post_wins(): ...     # two posts, same domain → one candidate, the earlier item_id
def test_growth_hints_rank_first_and_cap_applies(): ...    # max_new_companies=2: Series B and team-of-40 posts kept, rest skipped_cap
def test_recently_researched_company_is_skipped(): ...     # prior run with evidence ok for the domain → skipped_recent
def test_known_domains_are_neither_ranked_nor_capped(): ... # max_new_companies=0: known domain lands in selection.known, kept is empty
def test_dry_mode_never_calls_the_llm(): ...               # allow_llm=False → parse_calls == 0, needs_llm counted
def test_no_thread_is_an_hn_error_not_a_crash(): ...
```
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/store tests/pipeline/test_discovery.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: store v3 and the HN discovery selection`.

---

### Task 6: Runner integration

**Files:**
- Modify: `src/outreach/pipeline/runner.py`, `src/outreach/pipeline/discovery.py`
- Test: `tests/pipeline/test_hn_runner.py` (new), plus fetcher routes in `tests/pipeline/factories.py`

**Interfaces:**
- Consumes: `select_hn_candidates`, `HNSelection`, `HNPostRepo`, and the board adapters (`GreenhouseBoardSource`, `AshbyBoardSource`, `LeverBoardSource`, `MultiBoardSource`).
- Produces:
  - `hn_board_source(fetcher: Fetcher, kept: Sequence[HNCandidate]) -> MultiBoardSource`, in `discovery.py`.
  - `_OFF_DOMAIN_CLASSES` gains `SourceClass.HN_POST`.
  - `_sweep_postings` skips postings whose URL host is `news.ycombinator.com`.

Discover-stage changes in `run_pipeline`, after the config-board search and before `_group_by_domain`:
1. Skip HN entirely if `ctx.hn is None` or `not ctx.config.hn.enabled`.
2. `known = {canonical_domain(p.company_domain) for p in postings}`.
3. Call `select_hn_candidates(...)` inside `try`; any exception gives `summary.errors.append(f"hn: {exc}")` and no HN companies.
4. For the kept candidates with an ATS link, search `hn_board_source(ctx.fetcher, those)` with the same terms and region. Any posting whose `canonical_domain(company_domain)` equals `canonical_domain(f"{token}.com")` for a kept candidate is rewritten with `replace(p, company_domain=candidate.parsed.domain, company_name=candidate.parsed.company)`.
5. A kept or known HN candidate with no board posting gets the post as its posting: `PostingRef(parsed.company, parsed.domain, parsed.role, f"https://news.ycombinator.com/item?id={post.item_id}", parsed.location, parsed.work_mode, parsed.employment_type)`.
6. After the discover loop has upserted companies:
   - write an `HNPostRecord` with status `"kept"` for each kept or known candidate;
   - for each skipped candidate, upsert the company, `set_stage(run_id, cid, "discover", status)` and write an `HNPostRecord` with that status.

   Skipped companies are not in `company_ids`.
7. Keep a map from company id to `HNCandidate`. In `_profile_and_extract`, after the surfaces and before the postings sweep:
   - ingest the post as `_ingest_text(ctx, run_id, cid, "news.ycombinator.com", SourceClass.HN_POST, item_url, 200, post_text(html), posted_at.date(), summary)`;
   - record `FetchAttempt(cid, HN_POST, item_url, "ok", 200, 1)`.

   On resume, the map is rebuilt from `ctx.hn_posts.for_company` and a second `latest_thread()` lookup by item id. If that lookup fails, the post just isn't re-ingested, and an `hn:` error is recorded.

- [ ] **Step 1: Write failing tests** with the factories `FakeHNSource` holding a small thread, and the transport serving one Ashby board and the company homepages.

```python
def test_hn_company_with_ats_board_uses_the_posts_domain(): ...       # board postings rewritten to acme.io, not acme.com
def test_ats_board_without_the_role_falls_back_to_the_post(): ...    # board has only "Designer" → HN item URL posting
def test_hn_only_company_gets_an_hn_post_document_the_gate_accepts(): ... # hn_post doc dated by post; finding passes with hn_post evidence
def test_hn_post_never_confirms_a_domain(): ...                       # homepage 404 + only hn_post ok → contacts skipped_domain_unconfirmed
def test_config_token_company_is_not_capped_but_gets_its_post(): ...   # max_new_companies=0; config company still researched with hn_post doc
def test_skipped_companies_spend_nothing(): ...                       # skipped_cap company: no facts call, no contacts find call, discover status recorded
def test_hn_outage_keeps_config_companies(): ...                      # FakeHNSource(error=...) → "hn:" error, config company researched
def test_hn_disabled_skips_discovery(): ...                           # config.hn.enabled False → FakeHNSource.calls == 0
def test_hn_postings_are_not_refetched(): ...                         # no fetch attempt URL on news.ycombinator.com except the hn_post record
```
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/pipeline -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: merge HN discovery into the pipeline`.

---

### Task 7: Report, CLI, docs

**Files:**
- Modify: `src/outreach/render/view.py`, `src/outreach/render/report.py`, `src/outreach/render/templates/report.html.j2`, `src/outreach/cli.py`, `README.md`, `config.toml`
- Test: `tests/render/test_report.py`, `tests/test_end_to_end.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `HNPostRepo.for_company`, the `RunSummary` HN fields, `select_hn_candidates(allow_llm=False)`, `HNThreadSource`, `FakeHNSource`.
- Produces:
  - `ReportCompany.hn_post: tuple[str, str] | None`, as `(label, url)`. The label is `"via HN Who is hiring? (<Month Year of posted_at>)"`. It is rendered as a link through the `http_url` guard.
  - `_EXCLUSIONS` gains `("discover", "skipped_cap")` and `("discover", "skipped_recent")`.
  - `REASON_TEXT` gains `"skipped_cap": "not this run — over the HN cap"` and `"skipped_recent": "researched in the last {recheck_days} days"`. `describe_reason` passes `recheck_days`.
  - Diagnostics gain, all in fresh runs only:
    - `"HN thread"`
    - `"HN posts read / matching role"`
    - `"HN parsed / LLM-parsed / LLM-rejected"`
    - `"HN filtered out"`
    - `"HN kept / skipped (cap) / skipped (recent)"`
  - `_summary_from_storage` recovers `hn_skipped_cap` and `hn_skipped_recent` from stage rows. The other HN counts are "not recorded".
  - CLI: `run --no-hn` (a `bool`) runs with `ctx.config.hn` replaced by `enabled=False`.
  - `build_context` gives `hn=HNThreadSource(fetcher)` and `hn_posts=HNPostRepo(conn)` for real adapters, and `FakeHNSource()` under fakes.
  - The empty-token warning fires only when all three token lists are empty AND HN is disabled.
  - The dry run, when HN is enabled, calls `select_hn_candidates(ctx, 0, terms, work_modes, known, summary, allow_llm=False)` on a throwaway `RunSummary(run_id=0)`. `run_id=0` matches no real run, so the recent check still works. It then prints an extra line: `f"HN: {title} — {read} posts, {matched} match the role, {parsed} parsed, {needs_llm} would need the LLM fallback, {kept} kept after the cap."`. The company count in the credit estimate becomes config companies plus kept HN companies, deduped by domain. An HN error prints a `Warning: HN discovery failed: …` line, and the estimate still prints.
  - README:
    - a "Discovery from Hacker News" section covering the flow, `[hn]` keys, `--no-hn`, the cap and recheck, and the LLM fallback limit with its validation;
    - "exactly three LLM jobs" becomes four, with the guardrail;
    - the `hn_post` source class.
  - `config.toml`: a commented `[hn]` block.
- [ ] **Step 1: Write failing tests.**
  - `test_hn_company_card_links_its_post`
  - `test_skipped_cap_and_recent_are_listed_as_excluded`
  - `test_hn_diagnostics_render`
  - `test_no_hn_flag_disables_discovery`: monkeypatch `FakeHNSource.latest_thread` to count calls.
  - `test_dry_run_reports_hn_funnel_without_llm_calls`: monkeypatch `FakeHNSource.latest_thread` to return a small thread, and assert that `FakeLLM.parse_job_post` is never called. Patch it to raise.
  - `test_empty_tokens_with_hn_enabled_does_not_warn`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/render tests/test_end_to_end.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: HN in the report and CLI; docs`.

---

## Final verification

- [ ] `.venv/Scripts/python -m pytest -q`: all green, with a count of at least 591 plus the new tests.
- [ ] `grep -rn "Exactly three\|exactly three" src README.md` returns nothing.
- [ ] No live run. Handoff: the user runs `outreach run --role "…" --sector "…" --work-mode remote --dry-run` and reads the HN line before a real run.
