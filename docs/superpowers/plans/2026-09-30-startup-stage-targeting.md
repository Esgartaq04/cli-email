# Startup-Stage Targeting Implementation Plan

> **Status:** Executed (subagent-driven, per-task reviews + whole-branch review); merged to `main` in PR #2. Departures are recorded in the spec's **As built** section.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retarget the outreach pipeline at ≤2,000-employee startups: filter postings by work mode and full-time, enrich companies with Hunter facts, classify startup stage deterministically, research "what they're building" instead of bottlenecks, and find LinkedIn profiles for contacts.

**Architecture:** New pure modules in `core/` (`workmode.py`, `stage.py`, `themes.py`) carry every decision; new adapters (Ashby, Lever, Hunter company facts, Hunter email-finder) sit behind protocols with fakes; the runner gains an `enrich` stage and a stage-classification pass; findings (up to 3 per company) replace the single bottleneck; the report is re-laid out by stage. The LLM keeps exactly three methods.

**Tech Stack:** Python 3.11+, httpx, Jinja2, Typer, SQLite, pytest. Run tests with `.venv/Scripts/python -m pytest` (Windows venv).

**Spec:** `docs/superpowers/specs/2026-09-30-startup-stage-targeting-design.md`

## Global Constraints

- **No test touches a live API, and the executor never runs `outreach run` without `OUTREACH_FAKE_ADAPTERS=1` or `--dry-run` with fakes.** Never read `.env`. Any new test reaching `outreach.cli.build_context` must keep `load_dotenv` patched out (see the autouse fixture in `tests/test_cli.py`).
- `core/` imports nothing that touches network or disk.
- The LLM has exactly three methods: `expand_titles`, `extract_claims`, `write_summary`. No fourth.
- Every quote persisted passes the existing substring guard in `extraction/extract.py`.
- Company size cap: `discovery.headcount_max = 2000`; `headcount_min = 1`. A company is dropped **only** when its size is known to exceed the cap.
- Stage thresholds (defaults, in `[stage]` config): seed ≤ 49 employees; growth 50–499; expansion ≥ 500; maturity when founded ≥ 12 years ago **and** last known round ≥ 6 years ago, or IPO/acquired.
- Gate for findings: `min_independent_sources = 1`, `require_first_party = true`, `recency_days = 180`; ≥2 independent sources ⇒ `corroborated`. Max 3 findings per company.
- Facts cache TTL 90 days. Contacts cap 3 per company (unchanged).
- Hunter credit costs are config (`[hunter] enrichment_cost = 1`, `finder_cost = 1`) and are charged against one shared in-run budget seeded from `remaining_credits()`. Hunter's docs do not state which bucket Company Enrichment / Email Finder use; charging them as searches is the conservative assumption.
- Work-mode filter keeps `unknown`; employment filter drops only `other` (contract / part-time / intern / temporary).
- Excluded companies (size, no matching posting) appear in the report's Excluded list — never silently vanish.
- Existing `contacts.contacted_at` history must survive the migration.
- Commit after each task on `feat/v1-pipeline`, message ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Hunter returns only a band that straddles the cap** (`"1001-5000"`, no `employeesCount`) — the company must be *kept* (lower bound 1001 ≤ 2000), not dropped on its midpoint. → Task 11 `test_a_band_straddling_the_cap_is_kept`.
2. **Zero Hunter credits** — enrichment is skipped, every company is still researched with stage `unknown`, nothing raises. → Task 11 `test_zero_credits_still_researches_every_company_as_unknown`.
3. **"Remote - Canada" with `--work-mode remote`** — region filter still excludes it; work mode never overrides region. → Task 3 `test_remote_outside_the_us_is_still_excluded` (Ashby + Lever).
4. **Hunter `linkedin` holding a bare handle or a non-LinkedIn URL** — handle becomes `https://www.linkedin.com/in/<handle>`, foreign host is dropped. → Task 6 `test_linkedin_handle_is_expanded_and_foreign_hosts_rejected`.
5. **Resume after a crash** — no duplicate postings, no duplicate findings, no second enrichment charge. → Task 13 `test_resume_does_not_duplicate_postings_findings_or_enrichment`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/outreach/types.py` (modify) | New literals/enums/dataclasses: `WorkMode`, `EmploymentType`, `Stage`, `FundingRound`, `CompanyFacts`, `StageResult`, `Finding`; new `SourceClass` members; extended `Company`, `PostingRef` |
| `src/outreach/config.py` (modify) | `StageConfig`, `HunterConfig`, new discovery/gate fields, optional sections |
| `src/outreach/core/themes.py` (new) | Closed theme lists: `BUILDING_THEMES`, `SIGNAL_THEMES`, `EXPANSION_SIGNALS`, `ALL_THEMES` |
| `src/outreach/core/workmode.py` (new) | Pure work-mode / employment classification and filter |
| `src/outreach/core/stage.py` (new) | Pure `parse_round`, `parse_headcount_statement`, `classify_stage`, `stage_sort_key`, `exceeds_cap` |
| `src/outreach/sources/jobboards/region.py` (new) | `matches_region` extracted from Greenhouse |
| `src/outreach/sources/jobboards/{ashby,lever,multi}.py` (new) | Board adapters + composite |
| `src/outreach/contacts/facts.py` (new) | `CompanyFactsProvider` protocol, `HunterFactsProvider`, `FakeFactsProvider`, `parse_band` |
| `src/outreach/contacts/hunter.py`, `base.py`, `fake.py` (modify) | `linkedin` field fix, `find_profile` |
| `src/outreach/contacts/quota.py` (modify) | `CreditBudget` replaces `allocate_quota` |
| `src/outreach/store/*` (modify) | Versioned migration, facts columns, `run_company_stages`, `run_postings`, `findings` |
| `src/outreach/extraction/changelog.py` (new) | Split a changelog page into dated entries |
| `src/outreach/sources/surfaces/github.py` (new) | Homepage org scan, repos JSON parse |
| `src/outreach/sources/surfaces/standard.py`, `sources/base.py` (modify) | New surface list with alternates |
| `src/outreach/llm/anthropic_client.py` (modify) | New prompts over `core/themes` |
| `src/outreach/pipeline/runner.py`, `context.py` (modify) | Enrich stage, research surfaces, classification, findings, stage-ordered contacts |
| `src/outreach/render/view.py` (new) | `build_view` and helpers moved out of `cli.py` |
| `src/outreach/render/report.py`, `templates/report.html.j2` (modify) | New view model and layout |
| `src/outreach/cli.py`, `config.toml`, `README.md` (modify) | Flags, wiring, docs |

---

### Task 1: Types and config foundation

**Files:**
- Modify: `src/outreach/types.py`, `src/outreach/config.py`, `config.toml`
- Test: `tests/test_types.py`, `tests/test_config.py`

**Interfaces:**
- Produces (in `types.py`):
  - `WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]`; `ALL_WORK_MODES: frozenset[WorkMode] = frozenset({"remote", "hybrid", "onsite"})`
  - `EmploymentType = Literal["full_time", "other", "unknown"]`
  - `StageStatus` adds `"excluded_size"`, `"excluded_no_matching_posting"`, `"no_findings"`.
  - `SourceClass` adds `PRESS="press"`, `ABOUT="about"`, `HOMEPAGE="homepage"`, `DEV_DOCS="dev_docs"` (keep `STATUS_PAGE` for old rows).
  - `class Stage(str, Enum)`: `SEED_STARTUP="seed_startup"`, `GROWTH="growth"`, `EXPANSION="expansion"`, `MATURITY="maturity"`, `UNKNOWN="unknown"`.
  - `@dataclass(frozen=True) FundingRound(kind: str, announced: date | None)` — `kind` ∈ `"pre_seed" | "seed" | "series_a".."series_z" | "ipo" | "acquired"`.
  - `@dataclass(frozen=True) CompanyFacts(headcount: int | None, headcount_band: str | None, founded_year: int | None, funding_rounds: tuple[FundingRound, ...], tags: tuple[str, ...], source: str)`
  - `@dataclass(frozen=True) StageResult(stage: Stage, reasons: tuple[str, ...])`
  - `@dataclass(frozen=True) Finding(id: int | None, company_id: int, theme: str, claim: str, summary: str, corroborated: bool, passed: bool, reason: str, evidence_ids: tuple[int, ...])` — a miss is `Finding(None, cid, "", "", "", False, False, reason, ())`, mirroring today's failed `Bottleneck` row.
  - `Company` gains, **with defaults, after existing fields**: `headcount_band: str | None = None`, `founded_year: int | None = None`, `funding_rounds: tuple[FundingRound, ...] = ()`, `tags: tuple[str, ...] = ()`, `github_org: str | None = None`, `facts_fetched_at: datetime | None = None`.
  - `PostingRef` gains `work_mode: WorkMode = "unknown"`, `employment_type: EmploymentType = "unknown"`.
- Produces (in `config.py`):
  - `StageConfig(seed_max_headcount: int = 49, growth_max_headcount: int = 499, maturity_min_age_years: int = 12, maturity_min_years_since_funding: int = 6, signal_recency_days: int = 365)`
  - `HunterConfig(enrichment_cost: int = 1, finder_cost: int = 1, linkedin_lookup: bool = True, facts_ttl_days: int = 90)`
  - `GateConfig` gains `max_findings_per_company: int = 3`.
  - `DiscoveryConfig` gains `ashby_tokens: tuple[str, ...] = ()`, `lever_tokens: tuple[str, ...] = ()`, `max_changelog_entries: int = 5`, `max_press_posts: int = 3`, `max_github_repos: int = 5`.
  - `Config` gains `stage: StageConfig = StageConfig()`, `hunter: HunterConfig = HunterConfig()`. `[stage]` and `[hunter]` TOML sections are optional; every new discovery key is optional (`.get`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
def test_new_sections_are_optional_and_default(tmp_path):
    cfg = load_config(write(tmp_path, BASE))           # BASE = the existing test TOML
    assert cfg.stage.growth_max_headcount == 499
    assert cfg.hunter.facts_ttl_days == 90 and cfg.hunter.linkedin_lookup is True
    assert cfg.gate.max_findings_per_company == 3
    assert cfg.discovery.ashby_tokens == () and cfg.discovery.lever_tokens == ()

def test_new_sections_are_read_when_present(tmp_path):
    toml = BASE + '\n[stage]\nseed_max_headcount = 30\n[hunter]\nlinkedin_lookup = false\nfinder_cost = 2\n'
    cfg = load_config(write(tmp_path, toml.replace('greenhouse_tokens = []',
          'greenhouse_tokens = []\nashby_tokens = ["acme"]\nlever_tokens = ["zeta"]')))
    assert cfg.stage.seed_max_headcount == 30
    assert cfg.hunter.linkedin_lookup is False and cfg.hunter.finder_cost == 2
    assert cfg.discovery.ashby_tokens == ("acme",) and cfg.discovery.lever_tokens == ("zeta",)

# tests/test_types.py
def test_posting_ref_defaults_to_unknown_mode_and_type():
    p = PostingRef("Co", "co.example", "Engineer", "u", "Remote")
    assert p.work_mode == "unknown" and p.employment_type == "unknown"

def test_company_positional_construction_still_works():
    c = Company(1, "co.example", "Co", None, None)
    assert c.funding_rounds == () and c.github_org is None
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_config.py tests/test_types.py -v` — Expected: FAIL (attributes missing).
- [ ] **Step 3: Implement** the types and config above. Update `config.toml`: `headcount_min = 1`, `headcount_max = 2000`, `min_independent_sources = 1`, add `"press", "about"` to `first_party_classes`, add empty `ashby_tokens = []`, `lever_tokens = []`, and commented `[stage]` / `[hunter]` sections showing the defaults. Leave the user's `greenhouse_tokens` untouched.
- [ ] **Step 4: Run** the full suite `.venv/Scripts/python -m pytest -q` — Expected: all pass (additive change).
- [ ] **Step 5: Commit** `feat: types and config for stage targeting`

---

### Task 2: Work-mode and employment classification

**Files:**
- Create: `src/outreach/core/workmode.py`
- Test: `tests/core/test_workmode.py`

**Interfaces:**
- Consumes: `WorkMode`, `EmploymentType`, `ALL_WORK_MODES`, `PostingRef` (Task 1).
- Produces:
  - `classify_work_mode(location: str, title: str, content: str = "") -> WorkMode`
  - `classify_employment(title: str, content: str = "", declared: str | None = None) -> EmploymentType`
  - `parse_work_modes(flag: str | None) -> frozenset[WorkMode]` — raises `ValueError` naming the bad value.
  - `posting_matches(posting: PostingRef, wanted: frozenset[WorkMode]) -> bool` — `employment_type != "other"` and (`work_mode == "unknown"` or `work_mode in wanted`).

Rules: location+title are checked first, `hybrid` before `remote` before `onsite`; `content` is consulted only if they are inconclusive, with stricter patterns (`hybrid`, `\d+ days (a|per) week in (the|our) office` ⇒ hybrid; `fully remote`, `100% remote`, `remote-first` ⇒ remote; `on-site`, `onsite`, `in-office`, `in office 5 days` ⇒ onsite). Word-boundary matching. `declared` maps Ashby/Lever values case-insensitively after stripping `-`, `_`, spaces: `fulltime` ⇒ full_time; `parttime|intern|internship|contract|contractor|temporary|temp` ⇒ other.

- [ ] **Step 1: Write the failing truth-table test**

```python
@pytest.mark.parametrize("location,title,content,expected", [
    ("Remote - US", "Backend Engineer", "", "remote"),
    ("San Francisco, CA (Hybrid)", "Backend Engineer", "", "hybrid"),
    ("Remote or Hybrid - NYC", "Engineer", "", "hybrid"),
    ("New York, NY", "Engineer", "", "unknown"),
    ("New York, NY", "Engineer", "<p>This role is on-site in our NYC office.</p>", "onsite"),
    ("New York, NY", "Engineer", "We work 3 days a week in the office.", "hybrid"),
    ("Chicago, IL", "Engineer", "Our remote monitoring product", "unknown"),  # bare 'remote' in content is noise
    ("", "Staff Engineer (Remote)", "", "remote"),
])
def test_classify_work_mode(location, title, content, expected):
    assert classify_work_mode(location, title, content) == expected

@pytest.mark.parametrize("title,content,declared,expected", [
    ("Engineer", "", "FullTime", "full_time"),
    ("Engineer", "", "Full-time", "full_time"),
    ("Engineer", "", "Contract", "other"),
    ("Software Engineering Intern", "", None, "other"),
    ("Engineer (Contract)", "", None, "other"),
    ("Engineer", "This is a full-time position.", None, "full_time"),
    ("Engineer", "", None, "unknown"),
])
def test_classify_employment(title, content, declared, expected):
    assert classify_employment(title, content, declared) == expected

def test_parse_work_modes():
    assert parse_work_modes(None) == ALL_WORK_MODES
    assert parse_work_modes("remote") == frozenset({"remote"})
    assert parse_work_modes("hybrid, on-site") == frozenset({"hybrid", "onsite"})
    with pytest.raises(ValueError, match="sometimes"):
        parse_work_modes("remote,sometimes")

def test_posting_matches_keeps_unknown_mode_and_drops_non_full_time():
    wanted = frozenset({"remote"})
    assert posting_matches(ref(work_mode="remote", employment_type="full_time"), wanted)
    assert posting_matches(ref(work_mode="unknown", employment_type="unknown"), wanted)
    assert not posting_matches(ref(work_mode="onsite", employment_type="full_time"), wanted)
    assert not posting_matches(ref(work_mode="remote", employment_type="other"), wanted)
```
(`ref(**kw)` is a local helper building a `PostingRef` with those fields.)

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/core/test_workmode.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `core/workmode.py` per the rules above.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat: pure work-mode and employment classification`

---

### Task 3: Job boards — region helper, Greenhouse tagging, Ashby, Lever, composite

**Files:**
- Create: `src/outreach/sources/jobboards/region.py`, `ashby.py`, `lever.py`, `multi.py`; fixtures `tests/fixtures/ashby_board.json`, `tests/fixtures/lever_postings.json`
- Modify: `src/outreach/sources/jobboards/greenhouse.py`
- Test: `tests/sources/test_ashby.py`, `tests/sources/test_lever.py`, `tests/sources/test_multi_board.py`, `tests/sources/test_greenhouse.py`, `tests/contract/test_source_contracts.py`

**Interfaces:**
- Consumes: `classify_work_mode`, `classify_employment` (Task 2); `html_to_text` (`extraction/htmltext.py`); `canonical_domain`.
- Produces:
  - `matches_region(location: str, region: str, country: str | None = None) -> bool` — moved verbatim from `GreenhouseBoardSource._matches_region` (keep that method as a one-line delegate so existing tests pass). When `country` is given it decides: US iff `country.strip().upper() in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}`.
  - `AshbyBoardSource(fetcher: Fetcher, tokens: Sequence[str])` — `.search(role_terms, region) -> list[PostingRef]`. GET `https://api.ashbyhq.com/posting-api/job-board/{token}`; skip `isListed is False`; title substring match like Greenhouse; region via `matches_region(location, region, address.postalAddress.addressCountry)`; `work_mode` from `workplaceType` (`Remote`/`Hybrid`/`OnSite`), else `isRemote is True` ⇒ remote, else `classify_work_mode(location, title)`; `employment_type = classify_employment(title, declared=employmentType)`; `company_name=token`, `company_domain=canonical_domain(f"{token}.com")`, `url=jobUrl`.
  - `LeverBoardSource(fetcher, tokens)` — GET `https://api.lever.co/v0/postings/{token}?mode=json` (a JSON list); title `text`; location `categories.location`; region via `matches_region(location, region, country)`; `workplaceType` `remote`/`hybrid`/`on-site` ⇒ mode, `unspecified` ⇒ `classify_work_mode`; `employment_type = classify_employment(text, declared=categories.commitment)`; `url=hostedUrl`.
  - Greenhouse: board URL becomes `.../jobs?content=true`; each posting gets `work_mode = classify_work_mode(location, title, html_to_text(html.unescape(job["content"])))` and `employment_type = classify_employment(title, same text)`.
  - `MultiBoardSource(sources: Sequence[JobBoardSource])` — `.search` concatenates results; an exception from one source is caught and appended to `.errors: list[str]` as `f"{type(source).__name__}: {exc}"`.

All adapters tolerate malformed JSON / non-dict items exactly like `GreenhouseBoardSource` (skip, never raise).

- [ ] **Step 1: Write fixtures and failing tests.** `ashby_board.json`: `{"apiVersion":"1","jobs":[...]}` with four jobs — (a) "Backend Engineer", `workplaceType:"Remote"`, `employmentType:"FullTime"`, addressCountry "United States"; (b) same title, `workplaceType:"Remote"`, addressCountry "Canada", location "Remote - Canada"; (c) "Backend Engineer Intern", `employmentType:"Intern"`, US; (d) `isListed:false`. `lever_postings.json`: a list of three — (a) `text:"Backend Engineer"`, `workplaceType:"hybrid"`, `country:"US"`, `categories:{location:"New York, NY", commitment:"Full-time"}`; (b) `workplaceType:"remote"`, `country:"CA"`, location "Remote - Canada"; (c) `workplaceType:"unspecified"`, `country:"US"`, location "Remote - US".

```python
def test_ashby_maps_structured_fields(ashby_source):
    [p] = [p for p in ashby_source.search(["backend engineer"], "US") if "Intern" not in p.title]
    assert (p.work_mode, p.employment_type) == ("remote", "full_time")
    assert p.company_domain == "acme.com" and p.url.startswith("https://jobs.ashbyhq.com/")

def test_ashby_intern_is_tagged_other_and_unlisted_is_skipped(ashby_source):
    found = ashby_source.search(["backend engineer"], "US")
    assert {p.employment_type for p in found if "Intern" in p.title} == {"other"}
    assert len(found) == 2   # (a) and (c); (b) is Canada, (d) unlisted

@pytest.mark.parametrize("source_fixture", ["ashby_source", "lever_source"])
def test_remote_outside_the_us_is_still_excluded(source_fixture, request):
    found = request.getfixturevalue(source_fixture).search(["backend engineer"], "US")
    assert not any("Canada" in p.location for p in found)

def test_lever_maps_workplace_type_and_falls_back_to_text(lever_source):
    modes = sorted(p.work_mode for p in lever_source.search(["backend engineer"], "US"))
    assert modes == ["hybrid", "remote"]

def test_greenhouse_reads_mode_from_posting_content(...):
    # board JSON job with location "New York, NY" and content "&lt;p&gt;3 days a week in the office&lt;/p&gt;"
    assert posting.work_mode == "hybrid"

def test_multi_board_keeps_other_sources_when_one_raises():
    multi = MultiBoardSource([Exploding(), FakeJobBoardSource([posting])])
    assert multi.search(["x"], "US") == [posting]
    assert multi.errors and "Exploding" in multi.errors[0]
```
Sources are built with `Fetcher(httpx.Client(transport=httpx.MockTransport(...)), sleep=lambda s: None)` serving the fixture files and 404 for robots.txt, as `tests/sources/test_greenhouse.py` does. Add Ashby and Lever (fixture-backed) to the parametrized list in `tests/contract/test_source_contracts.py`. Update any `test_greenhouse.py` mock that matches the board URL exactly to accept the `?content=true` query.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/sources tests/contract -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** `region.py`, the three adapters, `multi.py`, and the Greenhouse changes.
- [ ] **Step 4: Run** the same command, then the full suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Ashby and Lever boards, work-mode tagging on every posting`

---

### Task 4: Theme lists and the stage classifier

**Files:**
- Create: `src/outreach/core/themes.py`, `src/outreach/core/stage.py`
- Test: `tests/core/test_stage.py`

**Interfaces:**
- Consumes: `Stage`, `StageResult`, `FundingRound`, `CompanyFacts`, `EvidenceItem` (Task 1); `StageConfig`.
- Produces (`core/themes.py`):
  - `BUILDING_THEMES = ("product-launch", "active-build", "platform-infra", "ai-ml", "public-api-sdk", "open-source", "new-market")`
  - `SIGNAL_THEMES = ("funding-round", "new-office", "acquisition", "headcount-statement", "new-product-line")`
  - `EXPANSION_SIGNALS = frozenset({"new-office", "new-market", "new-product-line", "acquisition"})`
  - `ALL_THEMES = BUILDING_THEMES + SIGNAL_THEMES`
- Produces (`core/stage.py`):
  - `parse_round(text: str, announced: date | None = None) -> FundingRound | None` — regex, case-insensitive: `pre-?seed` ⇒ `pre_seed`; `\bseed\b` (round context: "seed round", "seed funding", "raised … seed") ⇒ `seed`; `series\s+([a-z])\b` ⇒ `series_<letter>`; `\bIPO\b|initial public offering|went public|began trading on (the )?(NYSE|NASDAQ)` ⇒ `ipo`; `(was |been |is being )?acquired by` ⇒ `acquired`. Highest match wins (ipo/acquired > latest series letter > seed > pre_seed). `"we acquired X"` is **not** `acquired`.
  - `parse_headcount_statement(text: str) -> int | None` — `(?:team of|over|more than|nearly|about|almost)?\s*(\d{1,3}(?:,\d{3})*|\d+)\+?\s+(?:people|employees|team members|teammates|staff)`; returns the int.
  - `classify_stage(facts: CompanyFacts | None, signals: Sequence[EvidenceItem], today: date, config: StageConfig) -> StageResult`
  - `stage_sort_key(stage: Stage, headcount: int | None) -> tuple[int, bool, int]` — priority `GROWTH=0, EXPANSION=0, UNKNOWN=1, SEED_STARTUP=2, MATURITY=3`, then unknown headcount last, then headcount ascending.
  - `parse_band(band: str | None) -> tuple[int, int | None] | None` — `"11-50"` ⇒ `(11, 50)`; `"10K+"`/`"10000+"` ⇒ `(10000, None)`; garbage or `None` ⇒ `None`. (Re-exported by `contacts/facts.py` in Task 5.)
  - `exceeds_cap(headcount: int | None, band: str | None, cap: int) -> bool` — if `parse_band(band)` gives `(low, _)`, return `low > cap`; else `headcount is not None and headcount > cap`. (Consumed by Task 11.)

`classify_stage` algorithm (first match wins, funding beats headcount):
1. `rounds = facts.funding_rounds` if non-empty, else rounds parsed from signal items with theme `funding-round` via `parse_round(item.quote, item.published_at)`.
2. `latest` = round with the greatest `announced` (undated sort before dated), ties by kind rank.
3. `headcount = facts.headcount`, else max of `parse_headcount_statement(quote)` over `headcount-statement` signals.
4. MATURITY if `latest.kind in {"ipo","acquired"}`; or `founded_year` and `today.year - founded_year >= maturity_min_age_years` and `latest.announced` and `(today - latest.announced).days >= 365 * maturity_min_years_since_funding`.
5. Else from `latest.kind`: `series_c` or later ⇒ EXPANSION; `series_a`/`series_b` ⇒ GROWTH; `seed`/`pre_seed` ⇒ SEED_STARTUP.
6. Else from headcount: `> growth_max_headcount` ⇒ EXPANSION; `> seed_max_headcount` ⇒ GROWTH; else SEED_STARTUP.
7. Else UNKNOWN.
8. If the result is GROWTH and any signal has theme in `EXPANSION_SIGNALS` with `published_at` within `signal_recency_days` of `today` ⇒ EXPANSION.

Reasons, in this order and exact format: `"Series B (2026-03)"` / `"Seed"` (kind title-cased with `_`→space, `IPO`, `Acquired`; month appended when dated); `"~350 employees"`; `"founded 2019"`; `"expansion signal: new office"` (theme with `-`→space). Omit parts that are unknown.

- [ ] **Step 1: Write failing tests** — one test per rule plus parsers:

```python
T = date(2026, 9, 30); C = StageConfig()
def facts(headcount=None, rounds=(), founded=None):
    return CompanyFacts(headcount, None, founded, tuple(rounds), (), "hunter")
def sig(theme, quote, when=T): return EvidenceItem(1, 1, 1, "c", quote, SourceClass.PRESS, "x.example", when, theme)

@pytest.mark.parametrize("f,signals,expected", [
    (facts(rounds=[FundingRound("ipo", date(2021, 5, 1))]), [], Stage.MATURITY),
    (facts(founded=2010, rounds=[FundingRound("series_b", date(2018, 1, 1))], headcount=300), [], Stage.MATURITY),
    (facts(rounds=[FundingRound("series_c", date(2025, 1, 1))], headcount=120), [], Stage.EXPANSION),  # funding beats headcount
    (facts(rounds=[FundingRound("series_b", date(2026, 3, 1))], headcount=800), [], Stage.GROWTH),     # funding beats headcount
    (facts(headcount=650), [], Stage.EXPANSION),
    (facts(headcount=120), [], Stage.GROWTH),
    (facts(headcount=20), [], Stage.SEED_STARTUP),
    (facts(rounds=[FundingRound("seed", None)]), [], Stage.SEED_STARTUP),
    (None, [], Stage.UNKNOWN),
    (None, [sig("funding-round", "We raised a $30M Series B led by Acme.")], Stage.GROWTH),
    (facts(headcount=200), [sig("new-office", "We just opened our London office.")], Stage.EXPANSION),
    (facts(headcount=200), [sig("new-office", "Opened London.", date(2024, 1, 1))], Stage.GROWTH),  # stale signal
    (None, [sig("headcount-statement", "We're a team of 150 people.")], Stage.GROWTH),
])
def test_classify_stage_truth_table(f, signals, expected):
    assert classify_stage(f, signals, T, C).stage is expected

def test_reasons_are_rendered_in_a_fixed_format():
    r = classify_stage(CompanyFacts(350, "201-500", 2019,
                       (FundingRound("series_b", date(2026, 3, 1)),), (), "hunter"), [], T, C)
    assert r.reasons == ("Series B (2026-03)", "~350 employees", "founded 2019")

@pytest.mark.parametrize("text,kind", [
    ("announced our $40M Series C", "series_c"), ("closed a pre-seed round", "pre_seed"),
    ("raised a $3M seed round", "seed"), ("was acquired by BigCo", "acquired"),
    ("completed its initial public offering", "ipo"), ("we acquired Tinyco", None),
    ("Series A in 2021, then a Series B this year", "series_b"),
])
def test_parse_round(text, kind):
    got = parse_round(text)
    assert (got.kind if got else None) == kind

def test_stage_sort_key_orders_targets_first_then_smaller():
    keys = [stage_sort_key(Stage.MATURITY, 10), stage_sort_key(Stage.EXPANSION, 900),
            stage_sort_key(Stage.GROWTH, 80), stage_sort_key(Stage.UNKNOWN, None),
            stage_sort_key(Stage.SEED_STARTUP, 5), stage_sort_key(Stage.GROWTH, None)]
    assert sorted(keys) == [keys[2], keys[1], keys[5], keys[3], keys[4], keys[0]]

@pytest.mark.parametrize("headcount,band,expected", [
    (3000, "1001-5000", False), (None, "5001-10000", True), (2500, None, True),
    (None, None, False), (1999, None, False), (None, "10000+", True)])
def test_exceeds_cap(headcount, band, expected):
    assert exceeds_cap(headcount, band, 2000) is expected

@pytest.mark.parametrize("band,expected", [("11-50", (11, 50)), ("10K+", (10000, None)),
                                           ("10000+", (10000, None)), ("lots", None), (None, None)])
def test_parse_band(band, expected): assert parse_band(band) == expected
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/core/test_stage.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `core/themes.py` and `core/stage.py`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: deterministic startup-stage classifier`

---

### Task 5: Company facts provider (Hunter Company Enrichment)

**Files:**
- Create: `src/outreach/contacts/facts.py`, `tests/fixtures/hunter_company.json`
- Test: `tests/contacts/test_facts.py`

**Interfaces:**
- Consumes: `CompanyFacts`, `FundingRound` (Task 1); `parse_round` (Task 4); `hunter._BASE_URL`.
- Produces:
  - `class CompanyFactsProvider(Protocol): def company_facts(self, domain: str) -> CompanyFacts | None: ...`
  - `parse_band` — re-exported from `core/stage.py` (Task 4); `core/` must not import from `contacts/`.
  - `HunterFactsProvider(api_key: str, client: httpx.Client | None = None)` — GET `https://api.hunter.io/v2/companies/find?domain=…&api_key=…`; HTTP 404 ⇒ `None`; other ≥400 ⇒ `raise_for_status()`. `headcount` = `metrics.employeesCount` if a positive int, else band midpoint `(low+high)//2`, else low for open bands. `founded_year` int or None. `funding_rounds`: for each dict in `fundingRounds`, kind = `parse_round` of the first string among keys `type`, `round`, `series`, `name`; date = first parseable `date.fromisoformat(v[:10])` among `date`, `announcedOn`, `announced_on`, `announced_at`; items without a recognizable kind are skipped. `tags` = strings only. `source="hunter"`.
  - `FakeFactsProvider(facts_by_domain: dict[str, CompanyFacts], fail_on: frozenset[str] = frozenset())` — `.calls: list[str]`; raises `RuntimeError` for domains in `fail_on`; returns `None` for unknown domains.

- [ ] **Step 1: Write fixture + failing tests.** Fixture mirrors Hunter's documented shape: `{"data": {"name": "Acme", "domain": "acme.com", "foundedYear": 2019, "metrics": {"employees": "201-500", "employeesCount": 342}, "fundingRounds": [{"type": "Series B", "date": "2026-03-04"}, {"type": "Seed", "date": "2020-01-10"}], "tags": ["fintech", 7]}}`.

```python
def test_hunter_facts_parse_the_documented_shape(provider_serving_fixture):
    f = provider_serving_fixture.company_facts("acme.com")
    assert (f.headcount, f.headcount_band, f.founded_year) == (342, "201-500", 2019)
    assert f.funding_rounds[0] == FundingRound("series_b", date(2026, 3, 4))
    assert f.tags == ("fintech",) and f.source == "hunter"

def test_band_midpoint_is_used_without_an_exact_count(provider_serving({"data": {"metrics": {"employees": "51-200"}}})):
    assert provider.company_facts("x.com").headcount == 125

def test_unknown_company_is_none_not_an_error(provider_returning_404):
    assert provider.company_facts("nobody.example") is None

def test_malformed_funding_items_are_skipped(...):  # fundingRounds: [5, {"type": "grant"}, {"round": "Series A"}]
    assert [r.kind for r in f.funding_rounds] == ["series_a"]
```
Build providers with `httpx.Client(transport=httpx.MockTransport(...))` exactly as `tests/contacts/test_hunter.py` does.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/contacts/test_facts.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `contacts/facts.py`, using `core.stage.parse_band` for the band midpoint.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Hunter company-facts provider`

---

### Task 6: LinkedIn — fix the domain-search field, add email-finder lookup

**Files:**
- Modify: `src/outreach/contacts/hunter.py`, `src/outreach/contacts/base.py`, `src/outreach/contacts/fake.py`
- Test: `tests/contacts/test_hunter.py`, `tests/contacts/test_provider_contract.py`

**Interfaces:**
- Produces:
  - `ContactProvider.find_profile(domain: str, first_name: str, last_name: str) -> str | None`
  - `hunter._safe_linkedin_url(value: object) -> str | None` — replaces the use of `_safe_profile_url` for LinkedIn: accepts `http(s)://` URLs whose host is `linkedin.com` or ends with `.linkedin.com`; a bare handle matching `^[A-Za-z0-9_-]{3,100}$` becomes `https://www.linkedin.com/in/<handle>`; anything else ⇒ `None`.
  - `HunterProvider.find` reads `entry.get("linkedin")`, falling back to `entry.get("linkedin_url")`.
  - `HunterProvider.find_profile` — GET `email-finder` with `domain`, `first_name`, `last_name`; returns `_safe_linkedin_url(data.linkedin_url)`; HTTP 404 ⇒ `None`.
  - `FakeContactProvider(people_by_domain, credits, profiles: dict[str, str] | None = None)` — `find_profile` returns `profiles.get(f"{first_name} {last_name}")`, recording `.profile_calls: list[str]`.

- [ ] **Step 1: Update and add failing tests.** Change the existing fixtures in `test_hunter.py` that send `"linkedin_url"` in domain-search entries to `"linkedin"` (keep one test proving the `linkedin_url` fallback). Add:

```python
def test_domain_search_reads_the_linkedin_field(...):  # entry {"linkedin": "https://www.linkedin.com/in/marisol"}
    assert people[0].profile_url == "https://www.linkedin.com/in/marisol"

@pytest.mark.parametrize("value,expected", [
    ("marisol-okonkwo", "https://www.linkedin.com/in/marisol-okonkwo"),
    ("https://evil.example/in/m", None), ("javascript:alert(1)", None),
    ("https://uk.linkedin.com/in/m", "https://uk.linkedin.com/in/m"), (None, None)])
def test_linkedin_handle_is_expanded_and_foreign_hosts_rejected(value, expected):
    assert _safe_linkedin_url(value) == expected

def test_find_profile_uses_email_finder(...):  # MockTransport asserts path "/v2/email-finder" and params
    assert provider.find_profile("acme.com", "Marisol", "Okonkwo") == "https://www.linkedin.com/in/marisol"
```
Extend `test_provider_contract.py` so both fake and Hunter (mocked) satisfy `find_profile` returning `str | None`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/contacts -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix: read Hunter's linkedin field; add email-finder profile lookup`

---

### Task 7: Store v2 — migration and repositories

**Files:**
- Create: `src/outreach/store/migrations.py`
- Modify: `src/outreach/store/db.py`, `store/dimensions.py`, `store/runs.py`
- Test: `tests/store/test_db.py`, `tests/store/test_dimensions.py`, `tests/store/test_runs.py`

**Interfaces:**
- Produces:
  - `store/migrations.py`: `MIGRATIONS: tuple[str, ...]` (index 0 migrates user_version 0/1 → 2) and `migrate(conn) -> None` which reads `PRAGMA user_version`, applies each pending script in one transaction, then sets `user_version`. `db.connect` calls it after `executescript(schema.sql)`. `schema.sql` is **not** edited — v1 tables stay as-is and migrations layer on top, so a fresh DB and the user's existing `data/pipeline.db` take the same path.
  - Migration v2 SQL: `ALTER TABLE companies ADD COLUMN` `headcount_band TEXT`, `founded_year INTEGER`, `funding_rounds TEXT`, `tags TEXT`, `facts_source TEXT`, `facts_fetched_at TEXT`, `github_org TEXT`; `ALTER TABLE runs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1`; `CREATE TABLE run_company_stages (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), company_id INTEGER NOT NULL REFERENCES companies(id), stage TEXT NOT NULL, reasons TEXT NOT NULL, UNIQUE (run_id, company_id))` — a separate table because `run_companies.stage` already means *pipeline step*; `CREATE TABLE run_postings (id INTEGER PRIMARY KEY, run_id …, company_id …, title TEXT NOT NULL, url TEXT NOT NULL, location TEXT NOT NULL, work_mode TEXT NOT NULL, employment_type TEXT NOT NULL, UNIQUE (run_id, url))`; `CREATE TABLE findings (id INTEGER PRIMARY KEY, run_id …, company_id …, theme TEXT NOT NULL, claim TEXT NOT NULL, summary TEXT NOT NULL, corroborated INTEGER NOT NULL, passed INTEGER NOT NULL, reason TEXT NOT NULL, evidence_ids TEXT NOT NULL)`.
  - `CompanyRepo.set_facts(company_id: int, facts: CompanyFacts, fetched_at: datetime) -> None` — writes headcount, `headcount_source = facts.source`, band, founded year, rounds (JSON `[{"kind", "announced"}]`), tags (JSON list), `facts_source`, `facts_fetched_at`. `CompanyRepo.mark_facts_checked(company_id, fetched_at)` — stamps `facts_fetched_at` only (Hunter had nothing). `CompanyRepo.set_github_org(company_id, org: str | None)`. `CompanyRepo.get` fills all new `Company` fields.
  - `RunRepo.create(...)` inserts `schema_version = 2`; `RunRepo.schema_version(run_id) -> int`; `RunRepo.set_company_stage(run_id, company_id, result: StageResult)` (upsert); `RunRepo.company_stage(run_id, company_id) -> StageResult | None`.
  - `PostingRepo(conn)`: `insert(run_id: int, company_id: int, posting: PostingRef) -> None` (INSERT OR IGNORE); `for_company(run_id, company_id) -> list[PostingRef]` (company_name/domain filled from `companies`).
  - `FindingRepo(conn)`: `insert(run_id, finding: Finding) -> int`; `for_company(run_id, company_id) -> list[Finding]`; `for_run(run_id) -> list[Finding]`; `clear_for_company(run_id, company_id) -> int`.
  - `BottleneckRepo` stays for reading old rows only.

- [ ] **Step 1: Write failing tests**

```python
def test_migration_upgrades_a_v1_database_and_keeps_contacted_history(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db"); conn.executescript(V1_SCHEMA_SQL)  # read from schema.sql
    conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A')")
    conn.execute("INSERT INTO contacts (company_id, full_name, title, email_status, contacted_at) "
                 "VALUES (1,'Ann','CTO','verified','2026-09-01T00:00:00')"); conn.commit(); conn.close()
    new = connect(tmp_path / "old.db")
    assert new.execute("PRAGMA user_version").fetchone()[0] == 2
    assert ContactRepo(new).for_company(1)[0].contacted_at is not None

def test_connect_is_idempotent_across_versions(tmp_path):
    connect(tmp_path / "t.db").close(); conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

def test_facts_round_trip(tmp_path):
    cid = repo.upsert("a.example", "A", None, None)
    repo.set_facts(cid, CompanyFacts(342, "201-500", 2019,
                   (FundingRound("series_b", date(2026, 3, 4)),), ("fintech",), "hunter"),
                   datetime(2026, 9, 30))
    c = repo.get(cid)
    assert (c.headcount, c.headcount_source, c.headcount_band, c.founded_year) == (342, "hunter", "201-500", 2019)
    assert c.funding_rounds == (FundingRound("series_b", date(2026, 3, 4)),) and c.tags == ("fintech",)

def test_stage_round_trips_and_upserts(tmp_path):
    runs.set_company_stage(r, c, StageResult(Stage.GROWTH, ("~120 employees",)))
    runs.set_company_stage(r, c, StageResult(Stage.EXPANSION, ("x",)))
    assert runs.company_stage(r, c) == StageResult(Stage.EXPANSION, ("x",))

def test_postings_ignore_duplicates_within_a_run(tmp_path):
    postings.insert(r, c, p); postings.insert(r, c, p)
    assert postings.for_company(r, c) == [p]      # p has work_mode="remote", employment_type="full_time"

def test_findings_round_trip_and_clear(tmp_path): ...
def test_new_runs_are_schema_version_2(tmp_path): assert runs.schema_version(runs.create(...)) == 2
```
Update `test_connect_creates_all_tables` to include the three new tables.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/store -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** tests/store then the full suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat: store v2 -- facts, stages, postings, findings`

---

### Task 8: LLM prompts over the new themes

**Files:**
- Modify: `src/outreach/llm/anthropic_client.py`
- Test: `tests/llm/test_anthropic_client.py`

**Interfaces:**
- Consumes: `ALL_THEMES`, `BUILDING_THEMES`, `SIGNAL_THEMES` (Task 4).
- Produces: `THEMES` is removed; `extract_claims` keeps a claim only if its normalized theme is in `ALL_THEMES`. Method signatures are unchanged.

Prompt copy (fixed):
- `_EXTRACT_SYSTEM`: "You extract factual claims about what a company is building, shipping or working on, and about its growth stage, from the given page text. Only claims about the company itself qualify: skip customer testimonials, perks and benefits, and generic job responsibilities. For a job posting, keep only statements describing what the team is building or the initiative the hire joins." + the existing verbatim-quote and strict-JSON sentences + "The theme MUST be exactly one of these slugs: " + a help string covering all twelve themes (building: product-launch = shipped or launched a product/feature; active-build = currently building or about to build; platform-infra = internal platform or infrastructure work; ai-ml = AI/ML features or models; public-api-sdk = public API, SDK, integrations; open-source = open-source projects; new-market = entering a new market, segment or region. signals: funding-round = a named funding round, IPO or acquisition of the company; new-office = opening an office or hub; acquisition = the company acquiring another company; headcount-statement = a stated team or employee count; new-product-line = launching a distinct new product line).
- `_SUMMARY_SYSTEM`: "You write a two-to-three sentence, neutral, factual summary of what a company is building and where it is heading, grounded only in the claim and quotes given to you." + existing strict-JSON sentence.

- [ ] **Step 1: Update tests.** Replace `THEMES` imports with `ALL_THEMES`; `test_the_extraction_prompt_lists_every_allowed_theme` iterates `ALL_THEMES`; add `test_a_legacy_bottleneck_theme_is_now_dropped` (a reply with theme `"reliability"` yields no claims) and `test_the_summary_prompt_is_about_what_they_are_building` (captured request `system` contains `"what a company is building"`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/llm -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: extraction targets what they're building and stage signals`

---

### Task 9: Changelog entries, GitHub helpers, surface list

**Files:**
- Create: `src/outreach/extraction/changelog.py`, `src/outreach/sources/surfaces/github.py`, `tests/fixtures/changelog.html`, `tests/fixtures/homepage.html`, `tests/fixtures/github_repos.json`
- Modify: `src/outreach/sources/base.py`, `src/outreach/sources/surfaces/standard.py`
- Test: `tests/test_changelog.py`, `tests/sources/test_github.py`, `tests/sources/test_surfaces.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ChangelogEntry(published_at: date, heading: str, text: str)`; `split_changelog(html: str) -> list[ChangelogEntry]` — newest first. An entry starts at an `h1`–`h4` whose text (or a `<time datetime>` inside it) parses as a date; its text runs until the next dated heading. Accepted formats: ISO `2026-09-12`, `September 12, 2026`, `Sep 12, 2026`, `12 Sep 2026`, `12 September 2026`. Dates outside headings are ignored.
  - `find_github_org(html: str) -> str | None` — first `href` on `github.com/<org>` or `github.com/<org>/<repo>`, skipping reserved first segments (`features`, `about`, `pricing`, `login`, `join`, `sponsors`, `orgs`, `topics`, `marketplace`, `enterprise`, `site`, `security`). Returns lowercased org.
  - `@dataclass(frozen=True) Repo(name: str, description: str, url: str, pushed_at: date)`; `parse_repos(body: str, today: date, recency_days: int, limit: int) -> list[Repo]` — from `https://api.github.com/orgs/{org}/repos?sort=pushed&per_page=20` JSON; drops `fork`/`archived` and repos pushed before `today - recency_days`; newest first; capped at `limit`; malformed JSON ⇒ `[]`.
  - `SurfaceTarget` gains `alternates: tuple[str, ...] = ()`.
  - `surface_targets(domain: str, github_org: str | None) -> list[SurfaceTarget]` returns, in order: careers `/careers`; eng blog `/blog`; changelog `/changelog`; press `/press` (alternates `/news`, `/newsroom`); about `/about`; dev docs `/docs` (alternates `/developers`, `/api`); github `https://api.github.com/orgs/{org}/repos?sort=pushed&per_page=20` when org known. Status page removed.

- [ ] **Step 1: Write fixtures and failing tests.** `changelog.html` has three dated `h2` entries (`Sep 12, 2026` / `2026-08-01` / `12 March 2024`), undated intro text, and a footer `© 2019`. `homepage.html` has `href="https://github.com/features"` before `href="https://github.com/AcmeHQ/sdk"`. `github_repos.json` has four repos: one fresh, one fork, one archived, one pushed 2023.

```python
def test_changelog_splits_on_dated_headings_newest_first():
    entries = split_changelog(FIXTURE)
    assert [e.published_at for e in entries] == [date(2026, 9, 12), date(2026, 8, 1), date(2024, 3, 12)]
    assert "Webhooks v2" in entries[0].text and "Webhooks v2" not in entries[1].text

def test_changelog_footer_dates_are_not_entries():
    assert all(e.published_at.year != 2019 for e in split_changelog(FIXTURE))

def test_a_changelog_without_dated_headings_has_no_entries():
    assert split_changelog("<h2>Improvements</h2><p>Faster.</p>") == []

def test_find_github_org_skips_reserved_paths():
    assert find_github_org(HOMEPAGE) == "acmehq"

def test_parse_repos_keeps_fresh_non_fork_non_archived():
    repos = parse_repos(REPOS_JSON, date(2026, 9, 30), 180, 5)
    assert [r.name for r in repos] == ["sdk"] and repos[0].pushed_at == date(2026, 9, 20)

def test_targets_cover_every_expected_surface():   # replaces the existing test
    classes = [t.source_class for t in surface_targets("a.example", "acme")]
    assert classes == [CAREERS_PAGE, ENG_BLOG, CHANGELOG, PRESS, ABOUT, DEV_DOCS, GITHUB]
    press = surface_targets("a.example", None)[3]
    assert press.alternates == ("https://a.example/news", "https://a.example/newsroom")
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_changelog.py tests/sources -v` — Expected: FAIL.
- [ ] **Step 3: Implement** (reuse `HTMLParser` in the style of `extraction/pubdate.py`; heading text is extracted with `html_to_text`).
- [ ] **Step 4: Run** — Expected: PASS (pipeline tests may now fail on the dropped status page; they are rewritten in Task 11–13 — if any fail here, note them and leave them for Task 11).
- [ ] **Step 5: Commit** `feat: dated changelog entries, GitHub org and repo helpers, new surface list`

---

### Task 10: Credit budget

**Files:**
- Modify: `src/outreach/contacts/quota.py`
- Test: `tests/contacts/test_quota.py`

**Interfaces:**
- Produces: `class CreditBudget: __init__(self, remaining: int)`; `try_spend(self, cost: int) -> bool` (never goes negative; `cost <= 0` always succeeds); `.remaining: int`; `.spent: int`. `allocate_quota` / `QuotaPlan` are deleted (the runner stops using them in Task 11).

- [ ] **Step 1: Replace `tests/contacts/test_quota.py`**

```python
def test_spends_until_the_budget_runs_out():
    b = CreditBudget(3)
    assert [b.try_spend(1) for _ in range(4)] == [True, True, True, False]
    assert (b.remaining, b.spent) == (0, 3)
def test_a_charge_larger_than_what_is_left_is_refused_whole():
    b = CreditBudget(1); assert b.try_spend(2) is False and b.remaining == 1
def test_negative_start_is_zero(): assert CreditBudget(-5).remaining == 0
def test_free_calls_always_succeed(): assert CreditBudget(0).try_spend(0) is True
```
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/contacts/test_quota.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `CreditBudget`; keep `allocate_quota` in place until Task 11 removes its only caller, then delete it there.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: shared in-run credit budget`

---

### Task 11: Runner — discover filter and enrich stage

**Files:**
- Modify: `src/outreach/pipeline/runner.py`, `src/outreach/pipeline/context.py`, `tests/pipeline/factories.py`, `src/outreach/cli.py` (`build_context` only: pass `facts_provider` — `FakeFactsProvider({})` under fakes, `HunterFactsProvider(HUNTER_API_KEY)` otherwise — plus `PostingRepo`/`FindingRepo`; board wiring waits for Task 15)
- Test: `tests/pipeline/test_enrich.py` (new), `tests/pipeline/test_runner.py`

**Interfaces:**
- Consumes: `posting_matches` (Task 2), `exceeds_cap` (Task 4), `CompanyFactsProvider` (Task 5), `PostingRepo`, `FindingRepo`, `CompanyRepo.set_facts/mark_facts_checked/set_github_org` (Task 7), `find_github_org` (Task 9), `CreditBudget` (Task 10).
- Produces:
  - `RunContext` gains `facts_provider: CompanyFactsProvider`, `postings: PostingRepo`, `findings: FindingRepo`; `bottlenecks` is removed.
  - `run_pipeline(ctx, role_title, sector, resume_run_id=None, work_modes: frozenset[WorkMode] = ALL_WORK_MODES) -> RunSummary`
  - `STAGES = ("discover", "enrich", "evidence", "contacts", "synthesize")`
  - `RunSummary` gains `excluded_size: int = 0`, `excluded_no_matching_posting: int = 0`, `facts_fetched: int = 0`, `facts_cached: int = 0`, `stage_counts: dict[str, int]`; `no_bottleneck` is renamed `no_findings`. `credits_before` is now read **once, before enrich**, seeding `budget = CreditBudget(credits_before)` (a failing `remaining_credits()` ⇒ error recorded, budget 0). If `job_board` has an `errors` attribute (MultiBoardSource), its entries are appended to `summary.errors` as `"discover: …"`.
  - `factories.build_context(...)` gains keyword args `facts: dict[str, CompanyFacts] | None = None`, `facts_fail_on: frozenset[str] = frozenset()`, and serves `https://<domain>/` (200, a small homepage with `href="https://github.com/goodco"` for good.example) for good/thin; ghost.example still 404s everywhere. Factory postings gain explicit modes — good.example `work_mode="unknown"`, thin.example `work_mode="onsite"`, both `employment_type="full_time"`. Factory claims switch theme from `reconciliation-throughput` to `active-build`; `CONFIG` gets `min_independent_sources=1`, `first_party_classes` += `press`, `about`, discovery `(1, 2000, "US", ())`.

Behaviour:
- **discover:** per canonical domain, `matching = [p for p in group if posting_matches(p, work_modes)]`. Company is always upserted. If `matching` is empty ⇒ `set_stage(..., "discover", "excluded_no_matching_posting")`, count it, and leave it out of `company_ids`. Otherwise insert each matching posting into `run_postings`, mark discover `ok`, and keep only the matching postings for `_sweep_postings`.
- **enrich** (per company, skipped if the stage is already `ok` or `excluded_size`): fetch `https://{domain}/` and record a `HOMEPAGE` fetch attempt (document_count 0). If not ok ⇒ enrich `skipped_domain_unconfirmed`; the company continues to research. If ok ⇒ `set_github_org(find_github_org(body))`. Facts: if `facts_fetched_at` is within `hunter.facts_ttl_days` of `ctx.today` ⇒ cached (count `facts_cached`). Else if `budget.try_spend(hunter.enrichment_cost)` ⇒ call the provider; a result ⇒ `set_facts`, `None` ⇒ `mark_facts_checked`; count `facts_fetched`. An exception ⇒ enrich `failed` with the error text, and the company continues. No budget ⇒ enrich `skipped_quota`, and the company continues. Then, if `exceeds_cap(headcount, headcount_band, discovery.headcount_max)` ⇒ enrich `excluded_size`, count it, and remove the company from research. Otherwise enrich is `ok` (unless one of the statuses above was already set).
- **contacts guard** is unchanged. The homepage attempt (class `HOMEPAGE`, outcome ok) now confirms a domain.
- The contacts stage temporarily keeps its current ordering but spends from `budget` (`try_spend(1)` per company, else `skipped_quota`). Delete `allocate_quota`.

- [ ] **Step 1: Write failing tests** in `tests/pipeline/test_enrich.py`:

```python
def test_a_company_over_the_cap_is_excluded_before_any_llm_call():
    ctx = build_context(facts={"thin.example": CompanyFacts(5000, "5001-10000", 2005, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    thin = company_id(ctx, "thin.example")
    assert ctx.runs.stage_status(s.run_id, thin, "enrich") == "excluded_size"
    assert ctx.runs.stage_status(s.run_id, thin, "evidence") is None
    assert s.excluded_size == 1

def test_a_band_straddling_the_cap_is_kept():
    ctx = build_context(facts={"thin.example": CompanyFacts(3000, "1001-5000", None, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "thin.example"), "evidence") == "ok"

def test_zero_credits_still_researches_every_company_as_unknown():
    ctx = build_context(credits=0)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "enrich") == "skipped_quota"
    assert ctx.runs.stage_status(s.run_id, good, "evidence") == "ok"
    assert ctx.facts_provider.calls == []

def test_facts_fresher_than_the_ttl_are_not_bought_again():
    ctx = build_context(facts={"good.example": CompanyFacts(80, "51-200", 2020, (), (), "hunter")})
    run_pipeline(ctx, "Backend Engineer", "fintech"); run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.facts_provider.calls.count("good.example") == 1

def test_an_enrichment_outage_keeps_the_company():
    ctx = build_context(facts_fail_on=frozenset({"good.example"}))
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "enrich") == "failed"
    assert ctx.runs.stage_status(s.run_id, good, "evidence") == "ok"

def test_a_company_with_no_posting_in_the_requested_mode_is_excluded_not_dropped():
    # factory postings: good.example "Chicago, IL" (unknown), thin.example work_mode="onsite"
    s = run_pipeline(ctx, "Backend Engineer", "fintech", work_modes=frozenset({"remote"}))
    thin = company_id(ctx, "thin.example")
    assert ctx.runs.stage_status(s.run_id, thin, "discover") == "excluded_no_matching_posting"
    assert thin in ctx.runs.companies_for_run(s.run_id)
    assert s.excluded_no_matching_posting == 1

def test_the_homepage_github_link_is_remembered():
    assert ctx.companies.get(company_id(ctx, "good.example")).github_org == "goodco"
```
(`company_id(ctx, domain)` is a small helper in `factories.py` that looks the id up from `companies`.) Update `tests/pipeline/test_runner.py` for the renames (`bottlenecks` → `findings`, `no_bottleneck` → `no_findings`). Rewrite `test_quota_shortfall_marks_companies_skipped_not_missing` against the shared budget: with `credits=2`, both companies are enriched and neither gets contacts.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/pipeline -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Until Task 13 ports synthesis, keep `_synthesize` writing a single `Finding` in the old `Bottleneck` shape so the suite stays green.
- [ ] **Step 4: Run** the full suite — Expected: PASS except the report/e2e tests that assert the old section names; list them and mark each `@pytest.mark.xfail(reason="report re-layout in Task 14", strict=True)` so Task 14 has to flip them.
- [ ] **Step 5: Commit** `feat: work-mode filter at discovery and a size-capped enrich stage`

---

### Task 12: Runner — research surfaces

**Files:**
- Modify: `src/outreach/pipeline/runner.py`, `tests/pipeline/factories.py`
- Test: `tests/pipeline/test_reading_sources.py`, `tests/pipeline/test_runner.py`

**Interfaces:**
- Consumes: `surface_targets`, `SurfaceTarget.alternates`, `split_changelog`, `parse_repos` (Task 9); `Company.github_org` (Task 7).
- Produces:
  - `_ingest_text(ctx, run_id, company_id, domain, source_class, url, status, text, published_at, summary) -> None` — the store-and-extract half of today's `_ingest`. `_ingest` becomes: `html_to_text`, then work out the date, then `_ingest_text`.
  - `CURRENT_STATE_CLASSES = {CAREERS_PAGE, JOB_POSTING, ABOUT}` (status page removed; `render/report.py`'s mirror is updated in Task 14).
  - `_sweep_blog(..., limit: int)`: blog uses `max_blog_posts`, press uses `max_press_posts`.

Behaviour per surface:
- **Alternates:** try `url`, then each alternate, and stop at the first ok. Each try is recorded as a fetch attempt.
- **Press:** index handled like a blog with posts dated from their own markup (`_sweep_blog` with `limit=max_press_posts`), class `PRESS`.
- **Changelog:** `split_changelog(body)`. The newest `max_changelog_entries` entries are ingested as separate documents with url `f"{url}#{entry.published_at.isoformat()}"`, text `f"{entry.heading}\n{entry.text}"` and `published_at=entry.published_at`. If there are no dated entries, the whole page is ingested undated, as today.
- **Dev docs:** only a fetch attempt is recorded (document_count 0); nothing is ingested.
- **GitHub:** with `company.github_org` known, `parse_repos(body, ctx.today, gate.recency_days, max_github_repos)`. Each repo is ingested as a `GITHUB` document with text `f"{name}: {description}"`, url `repo.url` and `published_at=repo.pushed_at`. With no org, keep the existing `skipped_no_github_org` record.

- [ ] **Step 1: Write failing tests** (extend `factories._transport` to serve a changelog with two dated entries, a `/news` index with one dated post (and 404 for `/press`), `/developers` 200, and `api.github.com/orgs/goodco/repos…` returning one fresh repo):

```python
def test_each_changelog_entry_is_its_own_dated_document(): ...
    # two CHANGELOG documents for good.example, urls ending "#2026-09-12" and "#2026-08-01"
def test_press_falls_back_to_news_and_reads_dated_posts(): ...
    # fetch_attempts show /press http_error then /news ok; one PRESS document with parsed date
def test_dev_docs_are_recorded_for_hooks_but_never_extracted(): ...
    # a DEV_DOCS attempt with outcome ok, document_count 0; no DEV_DOCS source_documents
def test_recent_github_repos_become_dated_first_party_evidence(): ...
    # one GITHUB document, published_at == repo pushed date
def test_the_status_page_is_no_longer_fetched(): ...
    # no fetch attempt URL starts with "https://status."
```
Update `test_only_current_state_surfaces_are_dated_with_the_fetch_date` for the new class set.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/pipeline -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite — Expected: PASS (xfails from Task 11 still xfail).
- [ ] **Step 5: Commit** `feat: research press, about, dated changelog entries and GitHub repos`

---

### Task 13: Runner — stage classification, findings, stage-ordered contacts, LinkedIn lookup

**Files:**
- Modify: `src/outreach/pipeline/runner.py`
- Test: `tests/pipeline/test_findings.py` (new), `tests/pipeline/test_runner.py`

**Interfaces:**
- Consumes: `classify_stage`, `stage_sort_key` (Task 4); `BUILDING_THEMES`, `SIGNAL_THEMES`, `EXPANSION_SIGNALS` (Task 4); `evaluate`, `cluster_by_theme`; `FindingRepo`, `RunRepo.set_company_stage` (Task 7); `ContactProvider.find_profile` (Task 6); `CreditBudget` (Task 10).
- Produces:
  - `facts_of(company: Company) -> CompanyFacts | None` (runner-private). Returns `None` when the company has no headcount, band, founded year or rounds.
  - Pipeline order: discover → enrich → evidence → **classify** (not a checkpoint; recomputed on every run/resume for every non-excluded company, including ones whose evidence failed) → contacts → synthesize.
  - `summary.stage_counts[stage.value]` incremented per classified company.

Behaviour:
- **Classify:**
  - `signals` = items whose theme is in `SIGNAL_THEMES` or equals `"new-market"`.
  - `classify_stage(facts_of(company), signals, ctx.today, ctx.config.stage)`, then `runs.set_company_stage(...)`.
- **Contacts:**
  - Pending companies are ordered by `stage_sort_key(stage, headcount)`, then company id.
  - Each company costs `budget.try_spend(1)`; if it can't be paid, the company is marked `skipped_quota`.
  - Inside `_resolve_contacts`, after ranking, for each person whose `profile_url` is `None`, whose stored contact has no `profile_url` either, and whose name has at least two tokens, with `config.hunter.linkedin_lookup` on and `budget.try_spend(hunter.finder_cost)` succeeding: `find_profile(domain, first_token, last_token)`. An exception there is swallowed per person and recorded as `summary.errors` `"linkedin {company_id} {name}: {exc}"`. The found URL goes into the `PersonRef` that is upserted.
  - `_resolve_contacts` now takes `budget` and `summary`.
- **Synthesize:**
  - `building` = items whose theme is in `BUILDING_THEMES`.
  - Evaluate each theme cluster with `evaluate`.
  - Passing clusters are ordered by `(-independent_sources, -len(cluster), theme)` and the first `gate.max_findings_per_company` are kept. Each becomes `Finding(None, cid, theme, cluster[0].claim, llm.write_summary(cluster[0].claim, quotes), corroborated=independent_sources >= 2, passed=True, reason="passed", evidence_ids=verdict.evidence_ids)`.
  - No passing theme ⇒ one miss row with `_failure_reason` computed over `building` only.
  - `findings.clear_for_company(run_id, cid)` runs before inserting.
  - `summary.evidenced` = companies with ≥1 finding; `summary.no_findings` = the rest.

- [ ] **Step 1: Write failing tests**

```python
def test_up_to_three_findings_most_corroborated_first(): ...
    # FakeLLM claims across 4 building themes; active-build quoted on careers + blog (2 sources)
    # → 3 findings, first theme "active-build", corroborated True; others corroborated False
def test_stage_signals_never_become_findings(): ...
    # a "funding-round" claim with a real quote → no Finding with theme "funding-round"
def test_a_quoted_series_b_sets_the_stage_when_hunter_has_no_rounds(): ...
    # claim theme funding-round, quote "We raised a $30M Series B" on the careers page (current-state date)
    # → runs.company_stage(...).stage is Stage.GROWTH and "Series B" in reasons
def test_contacts_credits_go_to_growth_companies_first(): ...
    # facts: thin.example 120 employees (growth), good.example 20 (seed); credits=3 (2 enrich + 1 contacts)
    # → thin contacts "ok", good contacts "skipped_quota"
def test_missing_linkedin_is_looked_up_once_and_remembered(): ...
    # FakeContactProvider(profiles={"Marisol Okonkwo": "https://www.linkedin.com/in/marisol"})
    # run twice → profile_calls == ["Marisol Okonkwo"]; stored contact profile_url set
def test_linkedin_lookup_respects_the_config_switch_and_budget(): ...
def test_a_linkedin_lookup_failure_does_not_lose_the_contact(): ...
def test_resume_does_not_duplicate_postings_findings_or_enrichment():
    ctx = build_context(facts={"good.example": CompanyFacts(80, "51-200", 2020, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    ctx.runs.set_stage(s.run_id, company_id(ctx, "good.example"), "synthesize", "failed")
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=s.run_id)
    good = company_id(ctx, "good.example")
    assert len(ctx.postings.for_company(s.run_id, good)) == 1
    assert len([f for f in ctx.findings.for_company(s.run_id, good) if f.passed]) == \
           len({f.theme for f in ctx.findings.for_company(s.run_id, good) if f.passed})
    assert ctx.facts_provider.calls.count("good.example") == 1
```
Port every remaining `test_runner.py` / `test_reading_sources.py` assertion about bottlenecks to findings. Keep the intent of `test_blog_posts_alone_cannot_pass_the_gate` as `test_undated_blog_index_text_alone_cannot_pass_the_gate`: recency still applies under `min=1`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/pipeline -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite — Expected: PASS (Task 11 xfails still xfail).
- [ ] **Step 5: Commit** `feat: stage classification, building findings, stage-ordered contacts, LinkedIn lookup`

---

### Task 14: Report — view model, layout, LinkedIn links, pre-v2 guard

**Files:**
- Create: `src/outreach/render/view.py`
- Modify: `src/outreach/render/report.py`, `src/outreach/render/templates/report.html.j2`, `src/outreach/cli.py` (`build_view` moves out, re-exported; `report` command)
- Test: `tests/render/test_report.py`, `tests/test_report_findings.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: everything persisted by Tasks 7–13.
- Produces (`render/report.py`):
  - `STAGE_LABELS = {"growth": "Growth & Establishment", "expansion": "Expansion", "seed_startup": "Seed / Startup", "maturity": "Maturity", "unknown": "Stage unknown"}`
  - `linkedin_search_url(full_name: str, company_name: str) -> str` = `"https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{full_name} {company_name}")`
  - `ReportPosting(title, url, work_mode)`; `ReportFinding(theme, claim, summary, corroborated: bool, evidence: list[ReportEvidence])`; `ReportHook(kind: Literal["repo", "docs"], label: str, url: str, when: date | None)`; `ReportExcluded(name, domain, reason: str)`.
  - `ReportContact` gains `linkedin_search: str | None` (set only when `profile_url` is None).
  - `ReportCompany` gains `stage: str`, `stage_label: str`, `stage_reasons: list[str]`, `founded_year: int | None`, `headcount_band: str | None`, `latest_round: str | None`, `postings: list[ReportPosting]`, `findings: list[ReportFinding]`, `hooks: list[ReportHook]`; `claim`/`summary`/`evidence` are removed.
  - `ReportView` sections: `target_stage: list[ReportCompany]`, `other_stages`, `no_findings`, `excluded: list[ReportExcluded]` (replacing `evidenced` / `no_bottleneck`).
  - `REASON_TEXT` adds `"excluded_size"` (rendered as `f"over {headcount_max:,} employees"`) and `"excluded_no_matching_posting": "no full-time posting in the requested work mode"`; `"insufficient_independent_sources"` text uses `min_independent_sources`. `CURRENT_STATE_CLASSES` becomes `{"careers_page", "job_posting", "about"}`.
- Produces (`render/view.py`): `build_view(ctx: RunContext, summary: RunSummary, fresh: bool = True) -> ReportView`, plus the helpers moved from `cli.py` (`_rank_and_cap_contacts`, `_findings` → renamed `_theme_breakdown`). `cli.py` does `from outreach.render.view import build_view` so existing imports keep working.

Layout rules:
- **Sections, each with an `id`, in this order:** `target-stage` ("Growth & Expansion"), `other-stages`, `no-findings` ("No building evidence"), `excluded`, `diagnostics`.
  - Companies with ≥1 passing finding go to `target-stage` when their stage is growth/expansion, otherwise to `other-stages`.
  - Every section is sorted by `stage_sort_key`.
- **Card, top to bottom:**
  1. Name and domain; a stage badge (`class="stage stage-<value>"`) with its reasons joined by ` · `; headcount with its source; founded year; latest round.
  2. Postings, each with a `work-mode` tag.
  3. "What they're building" findings. A `corroborated` badge appears when true. Quotes link to their sources.
  4. "Hooks for a build": GITHUB documents for the run → repo hooks; DEV_DOCS attempts with outcome ok → docs hooks.
  5. Contacts: a LinkedIn link labelled "LinkedIn", or a link labelled "Search LinkedIn" to the search URL.
  6. Coverage log.
- **Header:** "≤ {headcount_max:,} employees" replaces the old band.
- **Diagnostics** adds: excluded by size, excluded by work mode, facts fetched / cached, and stage counts.
- **`report` command:** when `ctx.runs.schema_version(run_id) < 2`, print `"Run {id} is a pre-v2 run; see its saved HTML report."` and exit with code 1. The reconstructed summary is otherwise built from `findings.for_run`.

- [ ] **Step 1: Write/rewrite failing tests.** Flip the Task 11 xfails to real tests against the new layout:

```python
def test_sections_render_in_stage_order(tmp_path):
    html = render(view_with(target=[growth_card], other=[seed_card], none=[x], excluded=[y]))
    idx = [html.index(f'id="{s}"') for s in ("target-stage", "other-stages", "no-findings", "excluded", "diagnostics")]
    assert idx == sorted(idx)

def test_stage_badge_and_reasons_render():
    assert 'class="stage stage-growth"' in html and "Series B (2026-03) · ~120 employees" in html

def test_a_contact_without_a_profile_gets_a_labelled_search_link():
    assert "Search LinkedIn" in html
    assert "linkedin.com/search/results/people/?keywords=Tomas+Reyes+Thin+Co" in html

def test_a_found_profile_is_linked_as_linkedin_not_search():
    assert 'href="https://www.linkedin.com/in/marisol"' in html and "Search LinkedIn" not in card_html

def test_corroborated_badge_only_on_two_source_findings(): ...
def test_excluded_companies_are_listed_with_their_reason():
    assert "over 2,000 employees" in html and "no full-time posting in the requested work mode" in html

def test_report_header_states_the_enforced_size_cap(tmp_path):   # replaces the "unenforced band" test
    assert "≤ 2,000 employees" in html

def test_hooks_list_recent_repos_and_developer_docs(tmp_path): ...   # end-to-end via factories
def test_report_command_refuses_a_pre_v2_run(...):                 # runs row with schema_version 1
    assert result.exit_code == 1 and "pre-v2 run" in result.output
```
Keep the existing honesty tests (`not recorded` diagnostics, failed research still listed, unverified contact never shows an email, current-state date labeled distinctly, contacts re-ranked and capped), ported to the new field names.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/render tests/test_report_findings.py tests/test_end_to_end.py tests/test_cli.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `render/view.py`, the dataclasses and the template (reuse the existing CSS tokens and quote styling in `report.html.j2`; add `.stage`, `.work-mode`, `.corroborated`, `.hooks` styles).
- [ ] **Step 4: Run** the full suite — Expected: PASS, no xfails left.
- [ ] **Step 5: Commit** `feat: stage-ordered report with findings, hooks and LinkedIn links`

---

### Task 15: CLI wiring, dry-run estimate, docs

**Files:**
- Modify: `src/outreach/cli.py`, `README.md`, `config.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `parse_work_modes` (Task 2); `AshbyBoardSource`, `LeverBoardSource`, `MultiBoardSource` (Task 3); `HunterFactsProvider`, `FakeFactsProvider` (Task 5); `PostingRepo`, `FindingRepo` (Task 7).
- Produces:
  - `outreach run --role … --sector … [--work-mode remote|hybrid|onsite[,…]] [--dry-run] [--resume N]`. A bad `--work-mode` prints the `ValueError` message and exits 2 before `build_context` spends anything.
  - `build_context` wires `MultiBoardSource([Greenhouse, Ashby, Lever])`, with each board built only when its token list is non-empty, plus `HunterFactsProvider(HUNTER_API_KEY)`. With fakes: `FakeFactsProvider({})`.
  - The empty-token warning now fires only when **all three** token lists are empty: `"Warning: no greenhouse_tokens, ashby_tokens or lever_tokens in config.toml -- this run will discover zero companies."`
  - Dry run: postings are filtered by work mode first, then it prints `f"Dry run: {n} companies matched. A full run would use up to {n * enrich} enrichment + {n} contact-search + {n * cap * finder} LinkedIn-lookup credits ({total} total); {remaining} remain."`, where `finder` is 0 when `linkedin_lookup` is false. The existing failure path keeps `"could not be checked"` and a non-zero exit.
  - README: new purpose paragraph, stage table, `--work-mode`, the Ashby and Lever token keys, the `[stage]` / `[hunter]` config, and a credit-cost note. The note says Hunter doesn't document which bucket enrichment and email-finder draw from, and advises checking the account page after the first run.

- [ ] **Step 1: Write failing tests** (the autouse isolation fixture stays in place; every test sets `OUTREACH_FAKE_ADAPTERS=1`):

```python
def test_bad_work_mode_exits_before_anything_runs(monkeypatch):
    r = runner.invoke(app, ["run", "--role", "X", "--sector", "y", "--work-mode", "sometimes", "--dry-run"])
    assert r.exit_code == 2 and "sometimes" in r.output

def test_dry_run_itemizes_credit_buckets(monkeypatch):
    r = runner.invoke(app, [..., "--work-mode", "remote", "--dry-run"])
    assert r.exit_code == 0 and "enrichment" in r.output and "LinkedIn-lookup" in r.output

def test_empty_token_lists_warn_about_all_three_boards(monkeypatch):   # replaces the greenhouse-only warning test
    assert "no greenhouse_tokens, ashby_tokens or lever_tokens" in r.output
```
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_cli.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the CLI changes and the README / `config.toml` updates.
- [ ] **Step 4: Run** the full suite `.venv/Scripts/python -m pytest -q` — Expected: all pass. Then check isolation: `grep -rn "load_dotenv\|build_context" tests/` shows every `outreach.cli.build_context` path under the autouse fixture.
- [ ] **Step 5: Commit** `feat: --work-mode, multi-board wiring, itemized dry-run, docs`

---

## Final verification

- [ ] `.venv/Scripts/python -m pytest -q`: all green, and the count is ≥ the 209 baseline plus the new tests.
- [ ] `grep -rn "THEMES\b\|allocate_quota\|no_bottleneck\|BottleneckRepo" src/` shows only `BottleneckRepo`'s definition and legacy read.
- [ ] Hand off for the user's first live run. Suggested sequence: `outreach run --role "…" --sector "…" --work-mode remote --dry-run`, then a real run. **The executor does not run it.**
