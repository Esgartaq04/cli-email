# Contact Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the startup-research pipeline into a Hunter-based contact finder with two run types (`outreach jobs`, `outreach companies`), editable people lists, an emailed list, credit-frugal buying and a slim contact report.

**Architecture:**
1. Delete the research half first (quotes, gate, stages, the old runner, report and CLI). What remains is the reusable core: job boards, HN discovery, the fetcher, Hunter plumbing and the store.
2. On that base, add:
   - pure filter and people rules,
   - store v4,
   - two Hunter adapters with fakes (people search; company search via Discover),
   - one shared contact-buying step that uses a credit ledger counted in tenths,
   - two pipelines that feed it (companies, jobs),
   - a new report and the CLI.
3. Docs last.

**Tech Stack:** Python 3.11+ (the venv runs 3.12), typer 0.27, httpx (with `MockTransport` in tests), jinja2, SQLite and pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-contact-finder-design.md`

## Global Constraints

**Safety**
- Never open `.env`.
- Never make a live Hunter, Anthropic, HN or job-board call. Tests use fakes, `httpx.MockTransport` and recorded fixtures.
- Never run `outreach jobs` or `outreach companies` unless `OUTREACH_FAKE_ADAPTERS=1` is set.
- Every CLI test runs under the autouse `isolated_from_the_users_setup` fixture in `tests/test_cli.py`. It patches `outreach.cli.load_dotenv` and changes into `tmp_path`. Keep it in place.
- Until Task 10, fake mode still builds a real `httpx.Client` fetcher. So no CLI test may make a fake-mode run fetch anything: no real-run test returns board postings or an HN thread. Pipeline tests cover those paths through `tests/pipeline/factories.py` and its `MockTransport`. From Task 10 on, the fake-mode fetcher can't reach the network at all.

**Running and committing**
- Run tests with `.venv/Scripts/python -m pytest -q`. The whole suite must pass at the end of every task.
- No new runtime dependencies. The only ones allowed are `httpx`, `jinja2`, `typer` and `python-dotenv`.
- Commit messages use a conventional prefix (`feat:`, `fix:`, `refactor:`, `docs:`) and end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Credits and Hunter**
- Inside the code, credits are integers counted in **tenths**: 1 credit = 10, an address check = 5. They are printed only through `outreach.credits.format_credits`.
- A Hunter failure surfaces only as `HunterAPIError` carrying `"Hunter <path> returned HTTP <status>"`. The API key travels in the query string and must never appear in error text.
- Hunter payload shapes: confirm them against <https://hunter.io/api-documentation/v2> (reading the public docs is fine) and save the documented example responses as fixtures. If the docs contradict an interface in this plan, stop and report instead of improvising.

**Report rules**
- An address is shown only when it is confirmed (`email_status == "verified"`). `ContactRepo.upsert` already stores `email` only for verified people; keep that behavior.
- HTML links are `http(s)` only (`is_http_url`).
- LinkedIn URLs come only from `contacts.hunter._safe_linkedin_url`.
- Jinja autoescape stays on.

**Behavior**
- No location given means US only.

## Review Focus

1. **A Hunter search fails partway through a company.** For example, the second title tier raises after the first one bought people. The people already paid for are kept and counted, the company ends `bought`, and the error is recorded. Covered in Task 7: `test_failing_second_tier_keeps_people_already_bought`.
2. **The user's current pre-pivot `config.toml` loads with one "ignored" warning, not an error.** That file still has `[gate]`, `[ranking]`, `region`, `headcount_max` and `cache`. Covered in Task 2: `test_the_pre_pivot_config_loads_with_ignored_settings`.
3. **An emailed address typed with different case or spaces still matches.** For example `" Dana@Kestrel.example "` must match the stored contact and skip its company. Covered in Task 4 (`test_mark_for_company_matches_case_insensitively`) and Task 10 (`test_emailed_add_list_remove_round_trip`).
4. **A city with the right name in the wrong state doesn't pass.** "Austin, MN" fails `--location "Austin, TX"`. Covered by a Task 3 row in `test_posting_in_location`.
5. **A run resumed after a crash mid-purchase doesn't re-buy.** A company already bought is skipped and still counts against the cap. Covered in Task 7: `test_resume_skips_bought_companies_and_counts_them_against_the_cap`.

## Decisions this plan makes inside the spec's room

The spec leaves these to the builder. They are recorded in its "As built" section in Task 13.

- **Hunter documents `job_titles` on Domain Search, so the spec's department/seniority fallback is not built.**
  - People lists are `tiers` (lists of titles, one Domain Search per tier) plus `exclude_titles`.
  - Tier searches also send `type=personal`.
- **The free Email Count pre-check uses the domain's total personal emails, not per-department counts.**
  - A department sum would drop companies whose partnerships people Hunter files under "sales".
  - An empty search is free anyway.
- **Sector/size checks for token and HN companies use Discover only, and no Company Enrichment is called.**
  - The first call is `organization.domain` alone (which domains Hunter knows).
  - Then one call per sector × size combination, with the filters.
  - `enrichment_cost` is not a setting.
- **Discover returns only domain and name.** Every search is therefore one request per sector × size combination, so each company carries the exact sector and band it matched.
  - The `companies` table is not extended; that data lives in a new per-run `run_company_info` table.
  - The report shows the requested location as "HQ in …" for Hunter-found companies.
- **Report file names include the run id** (`2026-10-11-run7-jobs-backend-engineer.html`), so two runs on the same day don't overwrite each other.

## File Structure (end state)

```
src/outreach/
  cli.py                       commands: jobs, companies, sectors, emailed {add,list,remove}, report
  config.py                    Config, PeopleList, DEFAULT_PEOPLE, legacy-key handling
  credits.py                   tenths(), format_credits()
  sectors.py                   load_sectors()  (bundled Hunter industry names)
  data/industries.json         copy of https://hunter.io/files/industries.json
  types.py                     shared dataclasses (Location, RunFilters, CompanyInfo, Spend, FoundPerson, ...)
  core/filters.py              sectors, size bands, location parsing/labels, posting_in_location, describe_filters
  core/people.py               title matching, exclusion, tier ordering
  core/addresses.py            normalize_address()
  core/{dedupe,workmode,hnparse}.py   kept
  contacts/base.py             PeopleFinder protocol
  contacts/hunter.py           hunter_get, hunter_post, HunterAPIError, _safe_linkedin_url, HunterProvider
  contacts/fake.py             FakePeopleFinder
  contacts/ledger.py           CreditLedger
  sources/companies.py         CompanyQuery, FoundCompany, CompanySearch, HunterCompanySearch, FakeCompanySearch
  sources/hn.py, sources/jobboards/*  kept (htmltext.py moves here)
  pipeline/context.py          RunContext
  pipeline/summary.py          RunSummary
  pipeline/discovery.py        HN selection: thread order, location filter
  pipeline/contacts.py         Candidate, buy_contacts, restore_candidates, preview_contacts, max_cost
  pipeline/companies.py        run_companies
  pipeline/jobs.py             JobProspect, find_job_prospects, run_jobs
  render/links.py              is_http_url, linkedin_search_url, slugify
  render/contact_report.py     view model, build_contact_view, render/write
  render/templates/contacts.html.j2
  store/migrations.py (v4), dimensions.py, runs.py, emailed.py
```

---

### Task 1: Strip the research half

**Files:**
- Delete (src):
  - `pipeline/runner.py`
  - `render/view.py`, `render/report.py`, `render/templates/report.html.j2`
  - `extraction/__init__.py`, `extraction/changelog.py`, `extraction/extract.py`, `extraction/pubdate.py`
  - `sources/surfaces/` (the whole directory)
  - `core/gate.py`, `core/clustering.py`, `core/themes.py`, `core/stage.py`, `core/ranking.py`
  - `contacts/facts.py`, `contacts/quota.py`, `contacts/base.py`, `contacts/fake.py`
  - `store/cache.py`

  All of these are under `src/outreach/`.
- Move: `src/outreach/extraction/htmltext.py` → `src/outreach/sources/jobboards/htmltext.py`.
- Create:
  - `src/outreach/pipeline/summary.py`
  - `tests/sources/test_htmltext.py`
- Modify (src): `cli.py`, `types.py`, `core/hnparse.py`, `pipeline/context.py`, `pipeline/discovery.py`, `llm/base.py`, `llm/fake.py`, `llm/anthropic_client.py`, `contacts/hunter.py`, `sources/base.py`, `sources/jobboards/greenhouse.py`, `store/dimensions.py`, `store/runs.py`
- Delete (tests):
  - `core/test_clustering.py`, `core/test_gate.py`, `core/test_stage.py`, `core/test_ranking.py`
  - `contacts/test_facts.py`, `contacts/test_quota.py`, `contacts/test_provider_contract.py`
  - `pipeline/test_enrich.py`, `pipeline/test_findings.py`, `pipeline/test_hn_runner.py`, `pipeline/test_reading_sources.py`, `pipeline/test_runner.py`
  - `render/test_report.py`
  - `sources/test_blog_links.py`, `sources/test_github.py`, `sources/test_surfaces.py`
  - `store/test_cache.py`
  - `test_changelog.py`, `test_extraction.py`, `test_pubdate.py`, `test_report_findings.py`, `test_end_to_end.py`, `test_html_extraction.py`
  - Fixtures: `careers_page.html`, `changelog.html`, `eng_blog_post.html`, `github_repos.json`, `homepage.html`, `hunter_company.json`

  All of these are under `tests/`; the fixtures are in `tests/fixtures/`.
- Modify (tests): `pipeline/factories.py` (rewrite), `pipeline/test_discovery.py`, `core/test_hnparse.py`, `store/test_runs.py`, `store/test_dimensions.py`, `test_types.py`, `llm/test_anthropic_client.py`, `contacts/test_hunter.py`, `test_cli.py`

**Interfaces:**
- Produces:
  - **`RunSummary`** (`outreach.pipeline.summary`): `@dataclass RunSummary(run_id: int, errors: list[str] = [], hn_thread_title: str | None = None, hn_posts_read=0, hn_role_matched=0, hn_parsed=0, hn_llm_parsed=0, hn_llm_rejected=0, hn_filtered=0, hn_skipped_cap=0, hn_kept=0)`. Use `field(default_factory=list)` for `errors`.
  - **`select_hn_candidates`** (`outreach.pipeline.discovery`):
    - Signature: `select_hn_candidates(ctx, terms, work_modes, known_domains, summary, *, allow_llm: bool = True, region: str = "US") -> HNSelection`.
    - It no longer takes a `run_id` argument.
    - It keeps the first `ctx.config.hn.max_new_companies` new companies **in thread order**; the rest go to `skipped_cap`.
  - **HN types:** `HNCandidate(post, parsed, method)` and `HNSelection(thread, kept=[], known=[], skipped_cap=[], needs_llm=0)`.
  - **`RunContext`:** `RunContext(config, today, companies, contacts, runs, postings, fetch_attempts, hn_posts, fetcher=None, llm=None, job_board=None, hn=None)`. The adapter fields are `Optional`.
  - **LLM:** `LLMClient` has only `expand_titles(role_title) -> list[str]` and `parse_job_post(text) -> ParsedPost | None`. The fake is `FakeLLM(titles=None, parsed_posts=None)`.
  - **Hunter:** `HunterProvider(api_key, client=None)` has only `verify(email) -> EmailStatus`. `hunter_get`, `HunterAPIError` and `_safe_linkedin_url` are unchanged.
  - **`Company`:** `types.Company(id, canonical_domain, name, headcount, headcount_source, headcount_band=None)`.
  - **CLI:** `cli.build_context(config_path=Path("config.toml")) -> RunContext` builds the Anthropic LLM, job boards, HN, fetcher and repositories, and requires only `ANTHROPIC_API_KEY`. `cli.app` has no commands yet.

- [ ] **Step 1: Write the failing test.** In `tests/pipeline/test_discovery.py`, change the import to `from outreach.pipeline.summary import RunSummary`. Make `_select` call `select_hn_candidates(ctx, TERMS, ALL_WORK_MODES, known, summary, allow_llm=allow_llm)` and drop its `run_id` parameter. Delete `test_growth_hints_rank_first_and_cap_applies` and `test_recently_researched_company_is_skipped`, and add:

```python
def test_cap_keeps_the_first_companies_in_thread_order():
    tiny = (601, 'Tinyco | Backend Engineer | Austin, TX | ONSITE<p>A team of 4. '
                 '<a href="https://tinyco.dev">tinyco.dev</a>')
    big = (602, 'Bigco | Backend Engineer | Austin, TX | ONSITE<p>We raised a $90M Series C. '
                '<a href="https://bigco.dev">bigco.dev</a>')
    selection, summary = _select(_ctx(item_ids=[], extra=[tiny, big], max_new_companies=1))
    assert _domains(selection.kept) == ["tinyco.dev"]
    assert _domains(selection.skipped_cap) == ["bigco.dev"]
    assert (summary.hn_kept, summary.hn_skipped_cap) == (1, 1)
```

  Also rename `test_known_domains_are_neither_ranked_nor_capped` to `test_known_domains_are_not_capped` and leave its assertions alone.

- [ ] **Step 2: Run it.** `.venv/Scripts/python -m pytest tests/pipeline/test_discovery.py -q`. Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.pipeline.summary'`.

- [ ] **Step 3: Delete the research modules, their tests and their fixtures.** Use `git rm -rq <every path in the Delete lists above>`.

- [ ] **Step 4: Move the HTML-to-text helper.**
  - Run `git mv src/outreach/extraction/htmltext.py src/outreach/sources/jobboards/htmltext.py`.
  - In `greenhouse.py`, import `html_to_text` from `outreach.sources.jobboards.htmltext`.
  - Create `tests/sources/test_htmltext.py` holding the four `html_to_text` tests from `tests/test_html_extraction.py`, verbatim. They are the ones in the "--- html_to_text itself ---" block: `test_script_and_style_content_is_dropped`, `test_inline_markup_mid_sentence_does_not_fuse_or_leak_tags`, `test_entities_decode_to_real_characters` and `test_block_boundaries_get_a_separating_space`.
  - Then run `git rm tests/test_html_extraction.py`.

- [ ] **Step 5: Create `src/outreach/pipeline/summary.py`** with the `RunSummary` described in Interfaces.

- [ ] **Step 6: Slim the kept modules** (every path is under `src/outreach/`):

  | Module | Change |
  |---|---|
  | `core/hnparse.py` | Delete `hint_signals`, `_SENTENCE` and `HN_PUBLISHER` (nothing else uses them once the runner is gone). Drop the imports of `core.stage`, `EvidenceItem`, `SourceClass` and `date`. |
  | `pipeline/discovery.py` | Use the new `select_hn_candidates` signature. Remove the stage pre-rank, the recheck window, `HNCandidate.stage_hint`/`headcount_hint` and `HNSelection.skipped_recent`. Filter with `matches_region(parsed.location, region)`. Import `RunSummary` (TYPE_CHECKING) from `pipeline.summary`. Rewrite the module docstring so it no longer mentions ranking. |
  | `pipeline/context.py` | Use the `RunContext` from Interfaces. |
  | `llm/base.py` | Remove `RawClaim`, `extract_claims` and `write_summary`. Docstring: "Exactly two jobs, both for job runs: expand a role title into search terms, and read an HN post the rules can't. Neither decides who or what gets bought." |
  | `llm/fake.py` | `FakeLLM(titles=None, parsed_posts=None)`. Keep `parse_calls`. |
  | `llm/anthropic_client.py` | Remove `_THEME_DESCRIPTIONS`, `_EXTRACT_SYSTEM`, `_SUMMARY_SYSTEM`, `extract_claims`, `write_summary` and the imports of clustering, themes and `RawClaim`. |
  | `contacts/hunter.py` | Remove `find`, `find_profile`, `remaining_credits` and the `PersonRef` import. The class docstring now says it checks addresses through Hunter's Email Verifier. |
  | `sources/base.py` | Remove `SurfaceTarget` and the `SourceClass` import. |
  | `store/dimensions.py` | Remove `DocumentRepo`, `CompanyRepo.set_facts`, `CompanyRepo.mark_facts_checked` and `CompanyRepo.set_github_org`. `CompanyRepo.get` builds the slim `Company`. |
  | `store/runs.py` | Remove `EvidenceRepo`, `BottleneckRepo` and `FindingRepo`. From `RunRepo`, remove `window`, `set_company_stage`, `company_stage`, `researched_since` and `pending_companies`. |
  | `types.py` | Remove `Stage`, `FundingRound`, `CompanyFacts`, `StageResult`, `EvidenceItem`, `Bottleneck`, `Finding` and `SourceDocument`. Slim `Company`. Keep `SourceClass`: `FetchAttempt` still uses it for the homepage check. Comment that its other values only describe old rows. |
  | `cli.py` | Keep `app = typer.Typer(help="Find the right people to email at the right companies.")` and `build_context` as described in Interfaces. Delete `run`, `report`, `_dry_run_hn` and `_summary_from_storage`. |

- [ ] **Step 7: Rewrite `tests/pipeline/factories.py`.** Keep `TODAY = date(2026, 9, 21)`, `company_id` and `hn_thread` unchanged. Build `CONFIG` from the current (not yet slimmed) config dataclasses: `GateConfig(1, True, 180, frozenset())`, `RankingConfig(3, ())`, `DiscoveryConfig(1, 2000, "US", ())`, `PathsConfig(Path("db"), Path("cache"), Path("reports"))`. Then add:

```python
def build_context(*, postings: Sequence[PostingRef] = (),
                  routes: Mapping[str, str] | None = None) -> RunContext: ...
```

  - The database goes in a fresh temp dir via `connect(...)`.
  - Construct every repo `RunContext` needs.
  - Use `FakeLLM(titles=["backend engineer"])`, `FakeJobBoardSource(list(postings))` and `FakeHNSource()`.
  - Use a `Fetcher(httpx.Client(transport=...), sleep=lambda s: None)`. Its transport answers 404 for `robots.txt`, 200 with the routed text for any URL starting with a key in `routes`, and 404 for everything else.

- [ ] **Step 8: Trim the kept tests.**
  - `tests/core/test_hnparse.py`: delete `test_hint_signals_find_round_and_headcount` and fix the imports.
  - `tests/store/test_runs.py`: delete `test_pending_companies_excludes_completed_ones`, `test_stage_round_trips_and_upserts`, `test_stage_reasons_survive_commas_and_empty`, `test_stage_is_scoped_per_run`, `test_findings_round_trip_and_clear`, `test_run_window_is_open_until_the_run_finishes` and `test_researched_since_ignores_this_run_and_old_runs`. Remove the `EvidenceRepo` half of `test_clear_for_company_is_scoped_to_one_run_and_one_company`.
  - `tests/store/test_dimensions.py`: delete the document, facts and github-org tests (everything from `test_document_insert_dedupes_and_returns_original_id` on).
  - `tests/test_types.py`: delete `test_evidence_item_carries_quote_and_provenance`.
  - `tests/llm/test_anthropic_client.py`: delete these theme, extraction and summary tests:
    - `test_the_extraction_prompt_lists_every_allowed_theme`
    - `test_a_theme_outside_the_list_is_dropped`
    - `test_theme_spelling_is_normalised_onto_the_list`
    - `test_every_allowed_theme_is_already_in_normal_form`
    - `test_a_legacy_bottleneck_theme_is_now_dropped`
    - `test_the_summary_prompt_is_about_what_they_are_building`
  - `tests/contacts/test_hunter.py`: delete every `find`, `find_profile`, `remaining_credits` and `domain_search` test. In `test_http_errors_never_carry_the_api_key`, keep only the `verify` case.
  - `tests/test_cli.py`: keep `_TEST_CONFIG`, the autouse fixture and `test_real_adapters_build_one_source_per_configured_board`. Delete everything else.
  - `tests/store/test_db.py`: in `test_migration_upgrades_a_v1_database_and_keeps_contacted_history`, drop the assertion on `funding_rounds`, `tags` and `founded_year`, because `Company` no longer has those fields. Keep the rest of that test.

- [ ] **Step 9: Run the suite and check for dangling references.** Run `.venv/Scripts/python -m pytest -q`. Expected: PASS. Then run:

```bash
grep -rnE "core\.(stage|gate|clustering|themes|ranking)|outreach\.extraction|sources\.surfaces|pipeline\.runner|render\.(view|report)|contacts\.(facts|quota|fake|base)|store\.cache|RawClaim|EvidenceItem|SourceDocument|CompanyFacts|StageResult|hint_signals" src tests
```

  Expected: no output.

- [ ] **Step 10: Commit.** `git add -A && git commit` with the message `refactor: remove the research half ahead of the contact finder`.

---

### Task 2: Config for the contact finder

**Files:**
- Create: `src/outreach/credits.py`, `tests/test_credits.py`
- Modify: `src/outreach/config.py`, `config.toml`, `tests/test_config.py` (rewrite), `tests/pipeline/factories.py` (`CONFIG`), `tests/test_cli.py` (`_TEST_CONFIG`)

**Interfaces:**
- Produces (`outreach.credits`):
  - `tenths(value: object) -> int`. It raises `ValueError` unless `value` is a non-bool int or float that is ≥ 0 and a whole number of tenths. Compute it as `Decimal(str(value)) * 10`.
  - `format_credits(amount: int) -> str`. Examples: `45` → `"4.5"`, `30` → `"3"`, `0` → `"0"`.
- Produces (`outreach.config`):

```python
@dataclass(frozen=True)
class PathsConfig: db: Path; reports: Path
@dataclass(frozen=True)
class DiscoveryConfig: greenhouse_tokens: tuple[str, ...] = (); ashby_tokens: tuple[str, ...] = (); lever_tokens: tuple[str, ...] = ()
@dataclass(frozen=True)
class HNConfig: enabled: bool = True; max_new_companies: int = 15; llm_fallback_max: int = 20
@dataclass(frozen=True)
class HunterConfig: email_cost: int = 10; verify_cost: int = 5; verified_max_age_days: int = 90   # costs in tenths
@dataclass(frozen=True)
class RunConfig: max_companies: int = 10; contacts_per_company: int = 3
@dataclass(frozen=True)
class PeopleList: name: str; tiers: tuple[tuple[str, ...], ...]; exclude_titles: tuple[str, ...] = ()
DEFAULT_PEOPLE: dict[str, PeopleList]
@dataclass(frozen=True)
class Config:
    paths: PathsConfig
    discovery: DiscoveryConfig = DiscoveryConfig()
    hn: HNConfig = HNConfig()
    hunter: HunterConfig = HunterConfig()
    run: RunConfig = RunConfig()
    people: dict[str, PeopleList] = field(default_factory=lambda: dict(DEFAULT_PEOPLE))
    jobs_people: str = "hiring"
    companies_people: str = "sponsors"
    ignored: tuple[str, ...] = ()
def load_config(path: Path) -> Config
def ignored_warning(config: Config) -> str | None
def require_env(name: str) -> str   # unchanged
```

**Values pinned by the spec:**

`DEFAULT_PEOPLE`:

```python
"hiring": PeopleList("hiring", (
    ("founder", "co-founder", "cto", "chief technology officer"),
    ("vp engineering", "vp of engineering", "head of engineering", "director of engineering"),
    ("engineering manager", "tech lead"),
), ("recruit", "talent", "sourcer")),
"sponsors": PeopleList("sponsors", (
    ("partnerships", "sponsorships", "university relations", "university recruiting",
     "campus recruiting", "developer relations", "community"),
    ("events", "marketing manager", "head of marketing", "brand"),
    ("founder", "co-founder", "cto", "engineering manager"),
)),
```

**Recognized settings:**
- `paths.db` and `paths.reports` are required.
- `discovery.{greenhouse,ashby,lever}_tokens`
- `hn.{enabled, max_new_companies, llm_fallback_max}`
- `hunter.{email_cost, verify_cost, verified_max_age_days}`. The costs are written in credits and stored in tenths.
- `run.{max_companies, contacts_per_company}`
- `jobs.people` and `companies.people`
- `people.<name>.{tiers, exclude_titles}`. A people list in the file replaces the default list of the same name; other defaults stay.

**Legacy settings** are recorded in `Config.ignored`, in file order, rather than raising:
- whole sections: `[gate]`, `[ranking]`, `[stage]`
- `discovery.`: `headcount_min`, `headcount_max`, `region`, `max_blog_posts`, `max_job_postings`, `max_changelog_entries`, `max_press_posts`, `max_github_repos`
- `hunter.`: `enrichment_cost`, `finder_cost`, `linkedin_lookup`, `facts_ttl_days`
- `hn.recheck_days`
- `paths.cache`

Any other unknown section or key raises `ConfigError(f"unknown config setting: {name}")`.

**Validation errors** each raise `ConfigError` and name the dotted key:
- `run.contacts_per_company` outside 1–10. The message ends with "(Hunter's free plan returns at most 10 people per search)".
- `run.max_companies` < 1.
- A bad cost.
- Tiers that are not a non-empty list of non-empty lists of non-empty strings.
- `jobs.people` or `companies.people` naming a list that doesn't exist.

**Warning text:** `ignored_warning` returns `"Warning: config.toml settings this version no longer uses were ignored: " + ", ".join(config.ignored)`, or `None` when nothing was ignored.

- [ ] **Step 1: Write the failing tests.** In `tests/test_credits.py`:

```python
@pytest.mark.parametrize("value,expected", [(1, 10), (0.5, 5), (0, 0), (2, 20), (0.2, 2)])
def test_tenths(value, expected): assert tenths(value) == expected
@pytest.mark.parametrize("value", [0.25, -1, True, "1", None])
def test_tenths_rejects(value):
    with pytest.raises(ValueError): tenths(value)
@pytest.mark.parametrize("amount,text", [(45, "4.5"), (30, "3"), (0, "0"), (5, "0.5"), (135, "13.5")])
def test_format_credits(amount, text): assert format_credits(amount) == text
```

  Rewrite `tests/test_config.py`. The helper is `write(tmp_path, text) -> Path`, and every test below ends by asserting what its name says:

```python
def test_minimal_config_needs_only_paths(tmp_path):
    cfg = load_config(write(tmp_path, '[paths]\ndb = "d.db"\nreports = "r"\n'))
    assert cfg.paths == PathsConfig(Path("d.db"), Path("r"))
    assert (cfg.run.max_companies, cfg.run.contacts_per_company) == (10, 3)
    assert (cfg.hunter.email_cost, cfg.hunter.verify_cost, cfg.hunter.verified_max_age_days) == (10, 5, 90)
    assert (cfg.hn.enabled, cfg.hn.max_new_companies, cfg.hn.llm_fallback_max) == (True, 15, 20)
    assert cfg.people == DEFAULT_PEOPLE and (cfg.jobs_people, cfg.companies_people) == ("hiring", "sponsors")
    assert cfg.ignored == () and ignored_warning(cfg) is None

def test_paths_are_required(tmp_path)                       # ConfigError match "paths.db"
def test_costs_are_read_in_tenths(tmp_path)                  # email_cost = 2, verify_cost = 0.5 -> (20, 5)
def test_a_cost_finer_than_a_tenth_is_an_error(tmp_path)    # verify_cost = 0.25 -> match "hunter.verify_cost"
def test_a_people_list_in_the_file_replaces_the_default_of_that_name(tmp_path)
    # [people.hiring] tiers = [["cto"]] -> cfg.people["hiring"] == PeopleList("hiring", (("cto",),), ())
    #                                      and cfg.people["sponsors"] == DEFAULT_PEOPLE["sponsors"]
def test_a_new_people_list_can_be_a_commands_default(tmp_path)   # [people.mentors] + [companies] people = "mentors"
def test_the_default_people_list_must_exist(tmp_path)       # [jobs] people = "nobody" -> match "jobs.people"
def test_tiers_must_be_lists_of_titles(tmp_path)            # tiers = ["cto"] -> match "people.hiring.tiers"
def test_contacts_per_company_is_between_1_and_10(tmp_path) # 11 -> match "run.contacts_per_company"
def test_an_unknown_setting_is_an_error(tmp_path)           # [run] max_companys = 5 -> match "run.max_companys"
def test_the_pre_pivot_config_loads_with_ignored_settings(tmp_path):
    # PRE_PIVOT is the exact text of `git show 224f9e0:config.toml`, pasted into the test file.
    cfg = load_config(write(tmp_path, PRE_PIVOT))
    assert cfg.ignored == ("[gate]", "[ranking]", "discovery.headcount_min", "discovery.headcount_max",
                           "discovery.region", "discovery.max_blog_posts", "discovery.max_job_postings",
                           "paths.cache")
    assert cfg.discovery.greenhouse_tokens == ("stripe", "plaid", "brex")
    assert ignored_warning(cfg).startswith(
        "Warning: config.toml settings this version no longer uses were ignored: [gate], [ranking]")
def test_the_shipped_config_loads_cleanly()   # repo-root config.toml: ignored == (), tokens == ("stripe", "plaid", "brex")
def test_require_env_raises_when_missing(monkeypatch)       # kept as is
```

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/test_credits.py tests/test_config.py -q`. Expected: FAIL (import errors).

- [ ] **Step 3: Implement `outreach/credits.py` and the new `outreach/config.py`.** Then rewrite `config.toml` in the new format:
  - Write every section with its default values, and put a one-line comment beside each setting saying what it does.
  - Keep `greenhouse_tokens = ["stripe", "plaid", "brex"]`.
  - Write both people lists exactly as `DEFAULT_PEOPLE`.
  - Above `[people.*]`, add a comment: "each tier is one Hunter search by job title, most important first; a run stops once it has enough people".

- [ ] **Step 4: Point the tests at the new config.**
  - In `tests/pipeline/factories.py`, set `CONFIG = Config(paths=PathsConfig(Path("db"), Path("reports")))`.
  - In `tests/test_cli.py`, set `_TEST_CONFIG = '[paths]\ndb = "data/pipeline.db"\nreports = "reports"\n\n[discovery]\ngreenhouse_tokens = []\n'`.
  - The `pipeline/discovery.py` code reads only `config.hn.max_new_companies` and `llm_fallback_max`, so it needs no change.

- [ ] **Step 5: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit.** `feat: config for the contact finder -- people lists, run caps, per-email costs`.

---

### Task 3: Filters and the bundled sector list

**Files:**
- Create:
  - `src/outreach/data/industries.json`
  - `src/outreach/sectors.py`
  - `src/outreach/core/filters.py`
  - `tests/core/test_filters.py`
  - `tests/test_sectors.py`
- Modify: `src/outreach/types.py` (add `Location`), `pyproject.toml` (package data)

**Interfaces:**
- Produces:

```python
# types.py
@dataclass(frozen=True)
class Location:
    country: str = "US"            # ISO 3166 alpha-2
    state: str | None = None       # US state code; only with country "US"
    city: str | None = None
# sectors.py
def load_sectors() -> tuple[str, ...]          # functools.cache; importlib.resources.files("outreach").joinpath("data/industries.json")
# core/filters.py  (pure: no file or network access)
SIZE_BANDS: tuple[str, ...] = ("1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10001+")
class FilterError(ValueError): ...
def parse_sectors(values: Sequence[str], known: Sequence[str]) -> tuple[str, ...]
def suggest_sectors(value: str, known: Sequence[str], limit: int = 3) -> list[str]
def search_sectors(word: str | None, known: Sequence[str]) -> list[str]
def parse_sizes(values: Sequence[str]) -> tuple[str, ...]
def parse_location(text: str | None) -> Location
def location_label(location: Location) -> str
def hunter_location(location: Location) -> dict[str, str]     # {"country":..., "state":..., "city":...} without None values
def posting_in_location(location_text: str, work_mode: WorkMode, where: Location) -> bool
```

**Rules pinned by the spec:**

`parse_sectors`
- A name matches case-insensitively and comes back in Hunter's spelling. Duplicates are dropped and the order is kept.
- An unknown name raises `FilterError` with this message: `Unknown sector "{v}". Did you mean: {a}; {b}? Run "outreach sectors" to see them all.`
- When there are no suggestions, the message is instead `Unknown sector "{v}". Run "outreach sectors <word>" to search the list.`

`suggest_sectors`
- `difflib.get_close_matches` on lowercased names comes first, then substring matches.
- Results are de-duplicated and capped at `limit`.

`search_sectors`
- A case-insensitive substring filter. `None` returns every name. List order is kept.

`parse_sizes`
- Each value must be an exact band. Otherwise it raises `Unknown size band "{v}". Use one of: 1-10, 11-50, ...`.

`parse_location`
- Blank or `None` gives `Location()`.
- A trailing `, US`, `, USA` or `, United States` is dropped first.
- With one part:
  - Two letters means a US state code, checked against the 50 states plus DC (`CA` is California).
  - A full US state name is accepted.
  - A country name or alias from `COUNTRIES` gives `Location(iso)`. `US` and `United States` give `Location()`.
- With two parts `City, X`, `X` must be a state code, a state name or a country.
- Anything else raises `Can't read location "{text}". Use a US state ("TX"), a city and state ("Austin, TX"), or a country ("Canada", "Toronto, Canada").`

Lookup tables:
- `US_STATES` maps names to codes.
- `COUNTRIES` maps about 40 lowercased names and aliases to ISO codes. It must include `united states`, `usa`, `us`, `canada`, `united kingdom`, `uk`, `germany`, `france`, `ireland`, `netherlands`, `spain`, `india`, `mexico`, `brazil`, `australia` and `singapore`.

`location_label`
- `Location()` → `"United States"`
- `Location("US", "TX")` → `"Texas"`
- `Location("US", "TX", "Austin")` → `"Austin, TX"`
- `Location("CA")` → `"Canada"`
- `Location("CA", None, "Toronto")` → `"Toronto, Canada"`

`posting_in_location`
- `where == Location()` is always `True`; the board or HN already applied the US filter.
- For a US `where`:
  - A `remote` posting passes.
  - With a city: the city must appear as a whole word. If the text names a US state (a trailing `, XX` code or a full state name), it must be the requested one.
  - With only a state: a trailing `, ST`, the full state name, or a hub city of that state passes. Hub cities come from `_HUB_STATES`, which maps every city marker in `region._US_MARKERS` to its state.
- For a non-US `where`:
  - If a city was asked, the city must appear as a whole word.
  - Otherwise, the country name or an alias must appear. This applies to remote postings too.

- [ ] **Step 1: Bundle the sector list.** Download Hunter's public industry list into a new, empty scratch directory. Validate it, then write it into the package:

```bash
mkdir -p "$SCRATCH/industries" && curl -sSf https://hunter.io/files/industries.json -o "$SCRATCH/industries/raw.json"
.venv/Scripts/python -I -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); assert isinstance(d,list) and d and all(isinstance(x,str) and x.strip() for x in d) and len(set(d))==len(d); json.dump(d,open(sys.argv[2],'w',encoding='utf-8'),indent=1,ensure_ascii=False)" "$SCRATCH/industries/raw.json" src/outreach/data/industries.json
```

  `$SCRATCH` is the session scratchpad directory. Add `"data/*.json"` to `[tool.setuptools.package-data] outreach`.

- [ ] **Step 2: Write the failing tests.** In `tests/test_sectors.py`:

```python
def test_bundled_sectors_are_hunters_list():
    sectors = load_sectors()
    assert 400 <= len(sectors) <= 600 and len(set(sectors)) == len(sectors)
    assert "Software Development" in sectors and "Health, Wellness and Fitness" in sectors
```

  `tests/core/test_filters.py` uses a small `KNOWN = ("Software Development", "Banking", "Health, Wellness and Fitness", "Higher Education")`:

```python
def test_sectors_match_case_insensitively_in_hunters_spelling():
    assert parse_sectors(["software development", "Banking", "banking"], KNOWN) == ("Software Development", "Banking")
def test_a_sector_with_a_comma_is_one_sector():
    assert parse_sectors(["Health, Wellness and Fitness"], KNOWN) == ("Health, Wellness and Fitness",)
def test_unknown_sector_suggests_close_names():
    with pytest.raises(FilterError, match='Unknown sector "software developement". Did you mean: Software Development'):
        parse_sectors(["software developement"], KNOWN)
def test_search_sectors_is_a_substring_filter():
    assert search_sectors("ed", KNOWN) == ["Higher Education"]
    assert search_sectors(None, KNOWN) == list(KNOWN)
def test_sizes_must_be_hunters_bands():
    assert parse_sizes(["51-200", "11-50"]) == ("51-200", "11-50")
    with pytest.raises(FilterError, match="Unknown size band"): parse_sizes(["50-200"])

@pytest.mark.parametrize("text,expected", [
    (None, Location()), ("  ", Location()), ("US", Location()), ("United States", Location()),
    ("TX", Location("US", "TX")), ("tx", Location("US", "TX")), ("CA", Location("US", "CA")),
    ("Texas", Location("US", "TX")), ("Austin, TX", Location("US", "TX", "Austin")),
    ("Austin, TX, US", Location("US", "TX", "Austin")), ("Canada", Location("CA")),
    ("Toronto, Canada", Location("CA", None, "Toronto")),
])
def test_parse_location(text, expected): assert parse_location(text) == expected
@pytest.mark.parametrize("text", ["Austin", "Atlantis", "ZZ", "Austin, ZZ", "a, b, c"])
def test_unreadable_location(text):
    with pytest.raises(FilterError, match="Can't read location"): parse_location(text)
def test_location_label_and_hunter_filter():
    assert [location_label(l) for l in (Location(), Location("US", "TX"), Location("US", "TX", "Austin"),
            Location("CA"), Location("CA", None, "Toronto"))] == [
        "United States", "Texas", "Austin, TX", "Canada", "Toronto, Canada"]
    assert hunter_location(Location("US", "TX", "Austin")) == {"country": "US", "state": "TX", "city": "Austin"}
    assert hunter_location(Location()) == {"country": "US"}

TX, AUSTIN, CANADA = Location("US", "TX"), Location("US", "TX", "Austin"), Location("CA")
@pytest.mark.parametrize("text,mode,where,expected", [
    ("Chicago, IL", "onsite", Location(), True), ("", "unknown", Location(), True),
    ("Austin, TX", "onsite", TX, True), ("Dallas, Texas", "hybrid", TX, True), ("Austin", "onsite", TX, True),
    ("Chicago, IL", "onsite", TX, False), ("", "onsite", TX, False),
    ("Remote - US", "remote", AUSTIN, True), ("Austin, TX", "onsite", AUSTIN, True),
    ("Austin, MN", "onsite", AUSTIN, False), ("Austin", "onsite", AUSTIN, True),
    ("Toronto, Canada", "onsite", CANADA, True), ("Remote (Canada)", "remote", CANADA, True),
    ("Remote", "remote", CANADA, False), ("Toronto, ON", "onsite", CANADA, False),
    ("Toronto, ON", "onsite", Location("CA", None, "Toronto"), True),
])
def test_posting_in_location(text, mode, where, expected):
    assert posting_in_location(text, mode, where) is expected
```

- [ ] **Step 3: Run them.** `.venv/Scripts/python -m pytest tests/core/test_filters.py tests/test_sectors.py -q`. Expected: FAIL (import errors).

- [ ] **Step 4: Implement** `Location`, `sectors.py` and `core/filters.py` to the signatures and rules above.

- [ ] **Step 5: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit.** `feat: sector, size and location filters with Hunter's industry list`.

---

### Task 4: Store v4 — runs, per-run company info, run contacts, emailed list

**Files:**
- Create: `src/outreach/store/emailed.py`, `src/outreach/core/addresses.py`, `tests/store/test_emailed.py`, `tests/core/test_addresses.py`
- Modify: `src/outreach/store/migrations.py`, `src/outreach/store/runs.py`, `src/outreach/store/dimensions.py`, `src/outreach/types.py`, `src/outreach/pipeline/context.py`, `src/outreach/cli.py` (`build_context` wires the new repos), `tests/store/test_db.py`, `tests/store/test_runs.py`, `tests/store/test_dimensions.py`, `tests/pipeline/factories.py`, `tests/test_types.py`

**Interfaces:**
- Consumes: `Location` (Task 3).
- Produces:

```python
# types.py
@dataclass(frozen=True)
class RunFilters: sectors: tuple[str, ...] = (); sizes: tuple[str, ...] = (); location: Location = Location(); work_modes: tuple[str, ...] = ()
@dataclass(frozen=True)
class CompanyInfo: position: int; source: str; sector: str | None = None; size: str | None = None; fit: str = "fits"; guessed_domain: bool = False
    # source: "board" | "hn" | "hunter" | "hunter_hiring"; fit: "fits" | "unknown" | "no"
@dataclass(frozen=True)
class Spend: balance_before: int | None = None; emails: int = 0; verifications: int = 0; spent: int = 0   # tenths
@dataclass(frozen=True)
class RunInfo: run_id: int; kind: str | None; role: str; people: str | None; filters: RunFilters | None; started_at: datetime; schema_version: int; spend: Spend | None
@dataclass(frozen=True)
class EmailedEntry: address: str; emailed_on: date; full_name: str | None = None; title: str | None = None; company: str | None = None
# Contact loses `contacted_at` (fields: id, company_id, full_name, title, profile_url, email, email_status, provider, looked_up_at)

# store/runs.py -- RunRepo.create is removed
class RunRepo:
    def start(self, kind: str, role: str, filters: RunFilters, people: str, started_at: datetime) -> int
    def info(self, run_id: int) -> RunInfo | None
    def record_spend(self, run_id: int, spend: Spend) -> None
    def bought_before(self, company_id: int, people: str, before_run_id: int | None) -> tuple[int, datetime] | None
    def came_back_empty_before(self, company_id: int, people: str, before_run_id: int | None) -> bool
    # kept: schema_version, finish, set_stage, stage_status, companies_for_run, stage_error
class CompanyInfoRepo:
    def insert(self, run_id: int, company_id: int, info: CompanyInfo) -> None      # INSERT OR IGNORE
    def for_run(self, run_id: int) -> list[tuple[int, CompanyInfo]]                # by position
    def get(self, run_id: int, company_id: int) -> CompanyInfo | None
class RunContactRepo:
    def add(self, run_id: int, company_id: int, contact_id: int, position: int) -> None   # INSERT OR IGNORE
    def for_company(self, run_id: int, company_id: int) -> list[Contact]                    # by position

# store/emailed.py  (addresses passed in are already normalized)
class EmailedRepo:
    def add(self, addresses: Sequence[str], on: date, now: datetime) -> None   # re-adding updates emailed_on
    def remove(self, addresses: Sequence[str]) -> list[str]                   # the ones that were on the list
    def get(self, address: str) -> date | None
    def all(self) -> list[EmailedEntry]                                       # newest emailed_on first, then address
    def mark_for_company(self, company_id: int | None, domain: str) -> EmailedEntry | None

# core/addresses.py
def normalize_address(text: str) -> str   # trimmed, lowercased; ValueError('"{text}" is not an email address')

# pipeline/context.py: RunContext gains required fields after hn_posts:
#   company_info: CompanyInfoRepo, run_contacts: RunContactRepo, emailed: EmailedRepo
```

**Values pinned by the spec:**

*Migration v4 SQL.* Append to `MIGRATIONS`. As before, it is one script with its own `BEGIN`/`COMMIT`, and it sets `user_version` last:

```sql
ALTER TABLE runs ADD COLUMN kind TEXT;
ALTER TABLE runs ADD COLUMN people TEXT;
ALTER TABLE runs ADD COLUMN filters TEXT;
ALTER TABLE runs ADD COLUMN spend TEXT;
CREATE TABLE run_company_info (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id), position INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('board','hn','hunter','hunter_hiring')),
  sector TEXT, size TEXT, fit TEXT NOT NULL CHECK (fit IN ('fits','unknown','no')),
  guessed_domain INTEGER NOT NULL DEFAULT 0, UNIQUE (run_id, company_id));
CREATE TABLE run_contacts (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  contact_id INTEGER NOT NULL REFERENCES contacts(id), position INTEGER NOT NULL,
  UNIQUE (run_id, contact_id));
CREATE TABLE emailed (address TEXT PRIMARY KEY, emailed_on TEXT NOT NULL, added_at TEXT NOT NULL);
INSERT OR IGNORE INTO emailed (address, emailed_on, added_at)
  SELECT lower(email), substr(contacted_at, 1, 10), contacted_at FROM contacts
  WHERE contacted_at IS NOT NULL AND email IS NOT NULL;
PRAGMA user_version = 4;
```

*Run rows.*
- `start` writes these columns: `role_title=role`, `sector="; ".join(filters.sectors)`, `region=filters.location.country`, `headcount_min=0`, `headcount_max=0`, `status='running'`, `schema_version=4`, `kind`, `people`, and `filters`.
- The `filters` column holds this JSON: `{"sectors": [...], "sizes": [...], "location": {"country", "state", "city"}, "work_modes": [...]}`.
- `record_spend` adds the counts to the stored ones and keeps the first non-null `balance_before`. The `spend` column holds the JSON form of `Spend`.

*Earlier purchases.*
- `bought_before` returns the latest `(run_id, started_at)`, restricted to runs with `id < before_run_id` (or any run when that is `None`), that has a `contacts` row with status `bought` and `runs.people = people`.
- When `people == "hiring"`, a run with `schema_version < 4` and a `contacts` row with status `ok` also counts.
- `came_back_empty_before` uses the same window and people list, and matches status `empty` or `no_matching_people`.

*Emailed lookups.*
- `mark_for_company` returns the newest entry whose address domain (the text after `@`) equals `canonical_domain(domain)`, or whose address is a stored contact email of `company_id`. The comparison is case-insensitive.

- [ ] **Step 1: Write the failing tests.**
  - In `tests/store/test_db.py`, every `user_version == 3` becomes `== 4`.
  - Update two existing upgrade tests: `test_migration_upgrades_a_v1_database_and_keeps_contacted_history` and `test_migration_to_v3_from_v2_keeps_contact_history`.
    - Give each inserted contact `email='ann@a.example'`.
    - Replace their `contacted_at` assertion with `EmailedRepo(new).get("ann@a.example") == date(2026, 9, 1)`.
  - Then add:

```python
def test_v4_migration_moves_contacted_marks_into_the_emailed_list(tmp_path):
    conn = _v1_database(tmp_path / "v3.db")
    for script in migrations.MIGRATIONS[:2]: conn.executescript(script)
    conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A')")
    conn.execute("INSERT INTO contacts (company_id, full_name, title, email, email_status, contacted_at) "
                 "VALUES (1,'Ann','CTO','Ann@A.example','verified','2026-09-01T08:00:00')")
    conn.commit(); conn.close()
    new = connect(tmp_path / "v3.db")
    assert new.execute("PRAGMA user_version").fetchone()[0] == 4
    assert EmailedRepo(new).get("ann@a.example") == date(2026, 9, 1)
```

  Then in `tests/store/test_runs.py`, every `create(...)` call becomes `start(...)`. Delete `test_new_runs_are_schema_version_2`, and add these (helpers: `runs = RunRepo(conn)` and `cid = CompanyRepo(conn).upsert("a.example", "A", None, None)`):

```python
def test_start_records_kind_people_and_filters(tmp_path):
    filters = RunFilters(("Software Development",), ("51-200",), Location("US", "TX"), ("remote",))
    run_id = runs.start("jobs", "Backend Engineer", filters, "hiring", datetime(2026, 10, 11, 9))
    info = runs.info(run_id)
    assert (info.kind, info.role, info.people, info.filters, info.schema_version, info.spend) == (
        "jobs", "Backend Engineer", "hiring", filters, 4, None)
def test_record_spend_adds_up_and_keeps_the_first_balance(tmp_path):
    runs.record_spend(r, Spend(500, 3, 1, 35)); runs.record_spend(r, Spend(465, 2, 0, 20))
    assert runs.info(r).spend == Spend(500, 5, 1, 55)
def test_bought_before_is_per_people_list_and_only_looks_back(tmp_path):
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "bought")
    second = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 2))
    assert runs.bought_before(cid, "hiring", second) == (first, datetime(2026, 9, 1))
    assert runs.bought_before(cid, "sponsors", second) is None
    assert runs.bought_before(cid, "hiring", first) is None
    assert runs.bought_before(cid, "hiring", None) == (first, datetime(2026, 9, 1))
def test_an_empty_search_is_not_a_purchase(tmp_path)      # status "empty": bought_before None, came_back_empty_before True
def test_pre_v4_contacts_count_as_an_earlier_hiring_list(tmp_path):
    cur = conn.execute("INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max, started_at,"
                       " status, schema_version) VALUES ('r','s','US',1,2000,'2026-09-25T10:00:00','done',3)")
    runs.set_stage(cur.lastrowid, cid, "contacts", "ok")
    assert runs.bought_before(cid, "hiring", None) == (cur.lastrowid, datetime(2026, 9, 25, 10))
    assert runs.bought_before(cid, "sponsors", None) is None
def test_company_info_round_trips_in_position_order(tmp_path)
def test_run_contacts_keep_their_order(tmp_path)
```

  Write `tests/store/test_emailed.py` with these tests:
  - `test_add_get_and_remove`: `remove` returns only the addresses that were present.
  - `test_adding_again_updates_the_date`
  - `test_all_lists_newest_first_with_what_is_known`: a stored verified contact supplies name, title and company.
  - `test_mark_for_company_matches_the_address_domain`: `www.` is ignored.
  - `test_mark_for_company_matches_a_contact_at_another_domain`
  - `test_mark_for_company_matches_case_insensitively`: the contact email is stored as `Dana@Kestrel.example`, and the mark is added as `dana@kestrel.example`.

  Write `tests/core/test_addresses.py`. `normalize_address(" Dana@Kestrel.Example ") == "dana@kestrel.example"`, and each of `"dana"`, `"dana@"`, `"@x.com"`, `"a@b"`, `"a b@c.com"`, `"a@@b.com"` raises `ValueError`.

  Finally, delete `test_mark_contacted_is_visible_on_next_read` from `tests/store/test_dimensions.py`.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/store tests/core/test_addresses.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** the migration, the types, the repositories and `normalize_address`.
  - Remove `ContactRepo.mark_contacted`, and stop reading `contacted_at` in `_row_to_contact`.
  - Add the three new repos to `RunContext`, to `cli.build_context` and to `tests/pipeline/factories.build_context`.
  - In factories, add `start_run(ctx, kind="companies", people="hiring", filters=RunFilters()) -> int`, which calls `ctx.runs.start(..., datetime(2026, 9, 21, 9))`.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: store v4 -- run kind and filters, per-run company info and contacts, emailed list`.

---

### Task 5: People finder — title rules, Hunter people search, fake

**Files:**
- Create: `src/outreach/core/people.py`, `src/outreach/contacts/base.py`, `src/outreach/contacts/fake.py`, `tests/core/test_people.py`, `tests/contacts/test_people_contract.py`, `tests/fixtures/hunter_domain_search.json`, `tests/fixtures/hunter_email_count.json`, `tests/fixtures/hunter_account.json`
- Modify: `src/outreach/types.py` (`FoundPerson`), `src/outreach/contacts/hunter.py`, `src/outreach/pipeline/context.py`, `src/outreach/cli.py` (`build_context`), `tests/contacts/test_hunter.py`

**Interfaces:**
- Consumes: `PeopleList` (Task 2), `tenths` (Task 2).
- Produces:

```python
# types.py
@dataclass(frozen=True)
class FoundPerson:
    full_name: str; title: str; email: str
    profile_url: str | None = None
    hunter_status: str | None = None        # Domain Search's own verification status, e.g. "valid"
    hunter_checked_on: date | None = None
# core/people.py
def title_matches(title: str, wanted: str) -> bool         # every word of `wanted` is a whole word of `title`, any order, case-insensitive
def tier_of(title: str, people: PeopleList) -> tuple[int, int] | None   # (tier index, title index) of the first match
def is_excluded(title: str, people: PeopleList) -> bool    # any exclude pattern as a case-insensitive substring
def order_people(found: Sequence[FoundPerson], people: PeopleList) -> list[FoundPerson]   # drop excluded, stable sort by tier_of (None last)
# contacts/base.py
class PeopleFinder(Protocol):
    def count_people(self, domain: str) -> int: ...                                            # free
    def search_people(self, domain: str, titles: Sequence[str], limit: int) -> list[FoundPerson]: ...   # 1 credit per email returned
    def verify(self, email: str) -> EmailStatus: ...                                            # 0.5 credit
    def balance_tenths(self) -> int: ...
# contacts/hunter.py: HunterProvider gains count_people, search_people, balance_tenths (verify kept)
# contacts/fake.py
class FakePeopleFinder:
    def __init__(self, people: Mapping[str, Sequence[FoundPerson]] | None = None, *, balance: int = 500,
                 counts: Mapping[str, int] | None = None, statuses: Mapping[str, EmailStatus] | None = None,
                 failing_titles: frozenset[str] = frozenset(), balance_error: Exception | None = None) -> None
    count_calls: list[str]; search_calls: list[tuple[str, tuple[str, ...], int]]; verify_calls: list[str]
    # count_people -> counts.get(domain, len(people.get(domain, ())))
    # search_people -> records the call; raises RuntimeError(f"search failed for {domain}") if any title is in
    #   failing_titles; else the people whose title title_matches any requested title, in list order, [:limit]
    # verify -> statuses.get(email, "verified"); balance_tenths -> raise balance_error or return balance
# pipeline/context.py: RunContext gains `people: PeopleFinder | None = None`
```

**What each Hunter method sends and reads.** These follow Hunter's v2 docs; confirm the field names there.

`count_people`
- Sends `GET email-count?domain=`.
- Returns `data.personal_emails` when it is an int, otherwise `0`.

`search_people`
- Sends `GET domain-search` with `domain`, `type=personal`, `job_titles=",".join(titles)` and `limit=str(limit)`.
- Reads each entry of `data.emails[]`:
  - `value` becomes the email. Entries without a value are skipped.
  - `first_name` plus `last_name` becomes the name, falling back to `value`.
  - `position` becomes the title.
  - `linkedin`, or failing that `linkedin_url`, goes through `_safe_linkedin_url`.
  - `verification.status`, and `verification.date[:10]` parsed as a date, become `hunter_status` and `hunter_checked_on`.
- Never returns more than `limit` people.

`balance_tenths`
- Sends `GET account`.
- Returns `max(0, tenths(available) - tenths(used))`, read from Hunter's documented credit counter, which is expected at `data.requests.credits.{available, used}`.
- If the counter is missing or isn't a number, it raises `HunterAPIError("Hunter account response has no credit balance")`. That makes the run buy nothing.

`build_context` changes
- Real mode: add `people=HunterProvider(require_env("HUNTER_API_KEY"))`.
- Fake mode: `people=FakePeopleFinder(balance=0)`.

- [ ] **Step 1: Write the failing tests.**

  In `tests/core/test_people.py`, with `HIRING = DEFAULT_PEOPLE["hiring"]` and a `_p(title)` helper:

```python
@pytest.mark.parametrize("title,wanted,expected", [
    ("Co-founder & CTO", "cto", True), ("Co-Founder", "founder", True), ("VP of Engineering", "vp engineering", True),
    ("Senior Engineering Manager", "engineering manager", True), ("Factory Manager", "cto", False),
    ("Senior Software Engineer", "engineering manager", False), ("Director", "cto", False)])
def test_title_matches(title, wanted, expected): assert title_matches(title, wanted) is expected
def test_tier_of():
    assert [tier_of(t, HIRING) for t in ("CTO", "Head of Engineering", "Engineering Manager", "Designer")] == [
        (0, 2), (1, 2), (2, 0), None]
def test_is_excluded():
    assert is_excluded("Technical Recruiter", HIRING) and is_excluded("Talent Partner", HIRING)
    assert not is_excluded("CTO", HIRING) and not is_excluded("Campus Recruiter", DEFAULT_PEOPLE["sponsors"])
def test_order_people_drops_excluded_and_sorts_by_tier():
    found = [_p("Engineering Manager"), _p("Technical Recruiter"), _p("CTO"), _p("Designer")]
    assert [p.title for p in order_people(found, HIRING)] == ["CTO", "Engineering Manager", "Designer"]
```

  In `tests/contacts/test_hunter.py`, add these. The handlers serve the new fixtures through `MockTransport`.
  - `test_search_people_sends_titles_type_and_limit`: the request params are `domain=acme.example`, `job_titles=founder,cto`, `type=personal`, `limit=2`, `api_key=key`.
  - `test_search_people_reads_hunters_own_verification`: `hunter_status == "valid"` and `hunter_checked_on == date(...)` from the fixture.
  - `test_search_people_never_returns_more_than_limit`: the response holds 3 entries and `limit` is 2, so 2 come back.
  - `test_search_people_drops_an_unsafe_linkedin`
  - `test_count_people_reads_personal_emails`
  - `test_balance_reads_fractional_credits`: `available=49.5` and `used=0` give `495`.
  - `test_balance_without_a_credit_counter_is_an_error`
  - Extend the `test_http_errors_never_carry_the_api_key` parametrize list with `count_people`, `search_people` and `balance_tenths`.

  In `tests/contacts/test_people_contract.py`, parametrize over a fake and a Hunter provider that serves the fixtures. Assert:
  - `search_people` returns `FoundPerson`s, no more than `limit` of them, each with a non-empty email;
  - an unknown domain gives `[]` and a count of `0`;
  - `count_people` returns an int ≥ 0;
  - `verify` returns `"verified"`, `"unverified"` or `"not_found"`;
  - `balance_tenths` returns an int ≥ 0.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/core/test_people.py tests/contacts -q`. Expected: FAIL.

- [ ] **Step 3: Write the fixtures.** Write the three JSON fixtures from the documented example responses on Hunter's API page, trimmed to two or three emails. Then implement `FoundPerson`, `core/people.py`, `PeopleFinder`, the new `HunterProvider` methods, `FakePeopleFinder`, the `RunContext.people` field and the `build_context` wiring.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: Hunter people search by title tier, email count and credit balance`.

---

### Task 6: Company search — Hunter Discover and fake

**Files:**
- Create: `src/outreach/sources/companies.py`, `tests/sources/test_companies.py`, `tests/fixtures/hunter_discover.json`
- Modify: `src/outreach/contacts/hunter.py` (`hunter_post`), `src/outreach/pipeline/context.py`, `src/outreach/cli.py` (`build_context`)

**Interfaces:**
- Consumes: `Location`, `hunter_location` (Task 3), `canonical_domain`.
- Produces:

```python
# contacts/hunter.py
def hunter_post(client: httpx.Client, path: str, api_key: str, body: dict) -> dict
    # POST {_BASE_URL}/{path}?api_key=...; non-2xx -> HunterAPIError(f"Hunter {path} returned HTTP {status}")
# sources/companies.py
@dataclass(frozen=True)
class CompanyQuery: sectors: tuple[str, ...] = (); sizes: tuple[str, ...] = (); location: Location | None = None; hiring_titles: tuple[str, ...] = ()
@dataclass(frozen=True)
class FoundCompany: domain: str; name: str; sector: str | None = None; size: str | None = None
class CompanySearch(Protocol):
    def search(self, query: CompanyQuery) -> list[FoundCompany]: ...
    def check(self, domains: Sequence[str], query: CompanyQuery) -> dict[str, FoundCompany | None]: ...
        # domains Hunter knows: FoundCompany (fits; carries the matched sector/size) or None (known, doesn't fit).
        # Domains Hunter doesn't know are absent. Only query.sectors/sizes are applied.
class HunterCompanySearch: def __init__(self, api_key: str, client: httpx.Client | None = None) -> None
class FakeCompanySearch:
    def __init__(self, companies: Sequence[FoundCompany] = (), *, hiring: Sequence[FoundCompany] = (),
                 known: Mapping[str, FoundCompany] | None = None, error: Exception | None = None) -> None
    queries: list[CompanyQuery]; checks: list[tuple[tuple[str, ...], CompanyQuery]]
    # search: raise error if set; pool = hiring if query.hiring_titles else companies; keep those whose
    #   sector is in query.sectors (when given) and size in query.sizes (when given)
    # check: {d: (k if it passes the same sector/size test else None) for d in domains if (k := known.get(d))}
# pipeline/context.py: RunContext gains `company_search: CompanySearch | None = None`
```

**The Discover request** (`POST discover`, body shapes per Hunter's docs):

`search`
- Makes one request per combination of `(sector or None) × (size or None)`, in the given order.
- The body has these filter keys, each included only when it applies:
  - `"industry": {"include": [sector]}`
  - `"headcount": [size]`
  - `"headquarters_location": {"include": [hunter_location(location)]}`
  - `"job_openings": {"job_title": {"include": list(hiring_titles)}}`
- If there is no filter at all, it raises `ValueError("a company search needs at least one filter")`.
- From each response it reads `data[]` items `{domain, organization}`. Domains go through `canonical_domain`; unparseable ones are skipped.
- Results are de-duplicated across combinations, with the first one winning. Each `FoundCompany` carries the sector and size of the combination that found it.

`check`
- Domains are canonicalized and sent in chunks of 100.
- First, one request per chunk with only `{"organization": {"domain": chunk}}`, which tells us which domains Hunter knows.
- Then, per combination, `organization` plus `industry`/`headcount`. Location and hiring filters are never sent.

`build_context` changes
- Real mode: `company_search=HunterCompanySearch(require_env("HUNTER_API_KEY"))`.
- Fake mode: `FakeCompanySearch()`.

If Hunter's Discover docs say `organization` can't be combined with other filters, stop and report. Don't substitute Company Enrichment.

- [ ] **Step 1: Write the failing tests** in `tests/sources/test_companies.py`. They use a `MockTransport` that records each JSON body and answers from a dict keyed by `json.dumps(body, sort_keys=True)`:
  - `test_search_sends_one_request_per_sector_and_size`: `CompanyQuery(("Banking", "Software Development"), ("51-200",), Location())` gives exactly 2 bodies, `{"industry": {"include": ["Banking"]}, "headcount": ["51-200"], "headquarters_location": {"include": [{"country": "US"}]}}` and the same for Software Development.
  - `test_search_dedupes_across_combinations_and_keeps_the_first_match`
  - `test_search_carries_the_matched_sector_and_size`
  - `test_search_sends_the_headquarters_location`: `Location("US", "TX", "Austin")` gives `{"country": "US", "state": "TX", "city": "Austin"}`.
  - `test_hiring_search_sends_job_titles`: `{"job_openings": {"job_title": {"include": ["backend engineer"]}}}`.
  - `test_check_separates_fits_misfits_and_unknown_domains`: the known-domains call returns a and b, and the filtered call returns a, so the result is `{"a.example": FoundCompany(..., "Banking", None), "b.example": None}` and c is absent.
  - `test_check_never_sends_location_or_hiring_filters`
  - `test_a_search_without_any_filter_is_refused`: `pytest.raises(ValueError)` on `CompanyQuery()`.
  - `test_discover_errors_name_only_path_and_status`: a 403 response gives `HunterAPIError` with text `"Hunter discover returned HTTP 403"`, and neither the key nor `"?"` appears in it.
  - `test_fake_search_and_check_follow_the_same_rules`

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/sources/test_companies.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `hunter_post`, `sources/companies.py`, the context field and the `build_context` wiring. Write `tests/fixtures/hunter_discover.json` from Hunter's documented Discover example.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: Hunter Discover company search with per-combination sector and size`.

---

### Task 7: Buying contacts — ledger and the shared tail

**Files:**
- Create: `src/outreach/contacts/ledger.py`, `src/outreach/pipeline/contacts.py`, `tests/contacts/test_ledger.py`, `tests/pipeline/test_contacts.py`
- Modify: `src/outreach/pipeline/summary.py`, `src/outreach/types.py` (`StageStatus`), `tests/pipeline/factories.py`

**Interfaces:**
- Consumes:
  - `PeopleFinder` and `FakePeopleFinder` (Task 5)
  - `order_people` and `is_excluded` (Task 5)
  - the repos and `Spend` (Task 4)
  - `RunConfig` and `HunterConfig` (Task 2)
- Produces:

```python
# contacts/ledger.py
@dataclass
class CreditLedger:
    remaining: int            # tenths
    spent: int = 0
    def try_reserve(self, amount: int) -> bool          # deducts when amount <= remaining
    def settle(self, reserved: int, used: int) -> None  # remaining += reserved - used; spent += used; ValueError if used > reserved
# pipeline/summary.py: RunSummary gains balance_before: int | None = None, emails: int = 0,
#   verifications: int = 0, spent: int = 0, bought: int = 0, out_of_credits: int = 0
# pipeline/contacts.py
DONE_STATUSES = frozenset({"bought", "empty", "no_matching_people", "skipped_earlier_list",
                           "skipped_emailed", "domain_unconfirmed"})
@dataclass(frozen=True)
class Candidate: company_id: int; domain: str; name: str; guessed_domain: bool = False
def buy_contacts(ctx: RunContext, run_id: int, people: PeopleList, candidates: Sequence[Candidate], summary: RunSummary) -> None
def restore_candidates(ctx: RunContext, run_id: int) -> list[Candidate]   # company_info rows whose discover status is "ok", by position
@dataclass
class Preview: to_buy: int = 0; earlier_list: int = 0; emailed: int = 0; no_one: int = 0; over_cap: int = 0
def preview_contacts(ctx: RunContext, people: PeopleList, domains: Sequence[str]) -> Preview   # reads only; writes nothing
def max_cost(ctx: RunContext, companies: int) -> int   # companies * contacts_per_company * (email_cost + verify_cost)
# types.StageStatus documents: discover -> ok | excluded_no_matching_posting | left_out_filters | skipped_cap;
#   contacts -> bought | empty | no_matching_people | skipped_earlier_list | skipped_emailed |
#   domain_unconfirmed | over_cap | out_of_credits | failed
```

**How `buy_contacts` works.** This is the order the spec fixes. Every status is written with `ctx.runs.set_stage(run_id, cid, "contacts", status, error=...)`.

1. **Read the balance once.** Call `ctx.people.balance_tenths()`. On an exception, append `f"credits: {exc}"` to `summary.errors` and remember the error. The ledger starts at `CreditLedger(balance or 0)`.
2. **Order the candidates.** Fresh ones keep their given order. Those for which `came_back_empty_before(cid, people.name, run_id)` is true move to the end.
3. **Set the cap.** `cap = ctx.config.run.max_companies`. Set `bought` to the number of candidates already `bought` in this run, which happens on a resume.
4. **Walk each candidate in order.** Skip any already in `DONE_STATUSES` for this run. Otherwise, inside a per-company `try` (an exception means `failed` plus `summary.errors.append(f"contacts {domain}: {exc}")`):
   1. If `ctx.runs.bought_before(cid, people.name, run_id)` → `skipped_earlier_list`.
   2. If `ctx.emailed.mark_for_company(cid, domain)` → `skipped_emailed`.
   3. If `bought >= cap` → `over_cap`.
   4. If `guessed_domain` and the homepage doesn't answer → `domain_unconfirmed`. Fetch `https://{domain}/` through `ctx.fetcher`, first clearing this run's HOMEPAGE attempts for the company, and record a `FetchAttempt(cid, SourceClass.HOMEPAGE, url, outcome, status, 0)`.
   5. If the balance was unreadable → `failed`, with the credit error text.
   6. If `ctx.people.count_people(domain) == 0` → `no_matching_people`.
   7. Reserve `cpc × (email_cost + verify_cost)`. If that fails → `out_of_credits`, and add 1 to `summary.out_of_credits`.
   8. **Buy by tier.** `charged = 0`, `kept = []`, `seen = set()`. For each tier, while `len(kept) < cpc`:
      - set `limit = cpc - charged`; stop if it is ≤ 0;
      - `found = ctx.people.search_people(domain, tier, limit)`;
      - `charged += len(found)`;
      - keep each person whose lowercased email isn't in `seen` and who isn't `is_excluded`.

      If a search raises, record the error in `summary.errors` and on the stage row, stop searching, and keep what was already bought.
   9. **Order and confirm each kept person.** Take `order_people(kept, people)[:cpc]` in that order, and confirm each one:
      - A prior verified contact (`already_resolved`) with the same address (case-insensitive) is confirmed free.
      - `hunter_status == "valid"` with `(ctx.today - hunter_checked_on).days <= verified_max_age_days` is confirmed free.
      - Otherwise call `ctx.people.verify(email)` (one verification). If verify raises, the person stays `"unverified"` and the error is recorded.
   10. **Store each person.** `ctx.contacts.upsert(cid, PersonRef(name, title, profile_url, email if verified else None, status), "hunter", datetime.now())`, then `ctx.run_contacts.add(run_id, cid, contact_id, position)`.
   11. **Settle.** In a `finally`, call `settle(reserved, charged × email_cost + verifications × verify_cost)`, and add the counts to `summary.emails` and `summary.verifications`.
   12. **Set the status.**
       - `bought` (and `bought += 1`) if anyone was kept.
       - Otherwise `failed` if a search raised. A company we couldn't search mustn't be remembered as one that came back empty.
       - Otherwise `empty`.
5. **Record the spend.** Afterwards, set `summary.balance_before`, `summary.spent += ledger.spent` and `summary.bought = bought`. Then call `ctx.runs.record_spend(run_id, Spend(balance, this call's emails, this call's verifications, ledger.spent))`.

`preview_contacts` applies steps 2 (using `ctx.companies.find(domain)` for the id, with `None` meaning fresh) and 4.1–4.3 and 4.6 in the same order, with the earlier-list check run against any run, and counts the outcomes. It never fetches, searches, verifies or writes.

- [ ] **Step 1: Add factory helpers.** In `tests/pipeline/factories.py`:
  - `build_context` gains `people: PeopleFinder | None = None`, `company_search: CompanySearch | None = None` and `config: Config = CONFIG`. The defaults are `FakePeopleFinder()` and `FakeCompanySearch()`.
  - Add `person(name, title, domain="a.example", status="valid", checked_on=TODAY - timedelta(days=10)) -> FoundPerson`, whose email is `f"{name.split()[0].lower()}@{domain}"`.

- [ ] **Step 2: Write the failing tests.**

  In `tests/contacts/test_ledger.py`:

```python
def test_reserve_settle_and_refuse():
    ledger = CreditLedger(50)
    assert ledger.try_reserve(45) and ledger.remaining == 5
    ledger.settle(45, 30)
    assert (ledger.remaining, ledger.spent) == (20, 30)
    assert not ledger.try_reserve(45) and ledger.remaining == 20
def test_settling_more_than_was_reserved_is_a_bug():
    with pytest.raises(ValueError): CreditLedger(50).settle(10, 15)
```

  `tests/pipeline/test_contacts.py` uses these helpers: `HIRING = DEFAULT_PEOPLE["hiring"]`, `_candidate(ctx, domain, guessed=False)`, which upserts the company and returns a `Candidate`, `_buy(ctx, run_id, *domains, people=HIRING) -> RunSummary`, and `_status(ctx, run_id, domain)`.

```python
def test_buys_people_tier_by_tier_until_the_company_is_full():
    finder = FakePeopleFinder({"a.example": [person("Ann Lee", "Engineering Manager"), person("Bo Diaz", "CTO"),
                                             person("Cy Park", "VP Engineering"), person("Di Moe", "Tech Lead")]})
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert finder.search_calls == [("a.example", HIRING.tiers[0], 3), ("a.example", HIRING.tiers[1], 2),
                                   ("a.example", HIRING.tiers[2], 1)]
    assert [c.full_name for c in ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))] == [
        "Bo Diaz", "Cy Park", "Ann Lee"]
    assert (_status(ctx, run_id, "a.example"), summary.emails, summary.verifications, summary.spent,
            summary.bought) == ("bought", 3, 0, 30, 1)

def test_a_fresh_valid_is_trusted_and_a_stale_one_is_checked():
    fresh = person("Bo Diaz", "CTO", checked_on=TODAY - timedelta(days=90))
    stale = person("Cy Park", "VP Engineering", checked_on=TODAY - timedelta(days=91))
    finder = FakePeopleFinder({"a.example": [fresh, stale]})
    ctx = build_context(people=finder); summary = _buy(ctx, start_run(ctx), "a.example")
    assert finder.verify_calls == [stale.email] and (summary.verifications, summary.spent) == (1, 25)

def test_an_unconfirmed_address_is_never_stored():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO", status="accept_all")]},
                              statuses={"bo@a.example": "unverified"})
    ctx = build_context(people=finder); run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    [contact] = ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))
    assert (contact.email, contact.email_status) == (None, "unverified")

def test_failing_second_tier_keeps_people_already_bought():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO"), person("Cy Park", "VP Engineering")]},
                              failing_titles=frozenset({"vp engineering"}))
    ctx = build_context(people=finder); run_id = start_run(ctx)
    summary = _buy(ctx, run_id, "a.example")
    assert _status(ctx, run_id, "a.example") == "bought" and summary.emails == 1
    assert [c.full_name for c in ctx.run_contacts.for_company(run_id, company_id(ctx, "a.example"))] == ["Bo Diaz"]
    assert any("a.example" in e for e in summary.errors)

def test_resume_skips_bought_companies_and_counts_them_against_the_cap():
    finder = FakePeopleFinder({"a.example": [person("Bo Diaz", "CTO")],
                               "b.example": [person("Cy Park", "CTO", domain="b.example")]})
    ctx = build_context(people=finder, config=replace(CONFIG, run=RunConfig(max_companies=1)))
    run_id = start_run(ctx); _buy(ctx, run_id, "a.example")
    calls = list(finder.search_calls)
    summary = _buy(ctx, run_id, "a.example", "b.example")
    assert finder.search_calls == calls
    assert (_status(ctx, run_id, "a.example"), _status(ctx, run_id, "b.example"), summary.bought) == (
        "bought", "over_cap", 1)
```

  Also, each one line:
  - `test_excluded_people_are_dropped_even_after_being_paid_for`: `PeopleList("eng", (("engineering",),), ("recruit",))` with "Engineering Recruiter" and "Engineering Lead" keeps only the Lead, and `emails == 2`.
  - `test_a_company_bought_on_an_earlier_list_is_skipped_for_free`: a second run gives `skipped_earlier_list` with one search call in total. A run with `sponsors` still buys.
  - `test_pre_v4_contacts_count_as_an_earlier_hiring_list`: an old run is inserted with SQL, `schema_version=3`, contacts `ok`.
  - `test_a_company_where_someone_was_emailed_is_skipped_before_any_call`: `count_calls == search_calls == []`.
  - `test_no_one_matching_costs_nothing`: `counts={"a.example": 0}` gives `no_matching_people`, no search, `spent == 0`.
  - `test_an_empty_search_does_not_use_a_cap_slot`: cap 1, a has only a "Designer", so a is `empty` and b is `bought`.
  - `test_companies_that_came_back_empty_before_go_after_fresh_ones`: cap 1, a was `empty` in an earlier hiring run, so b is `bought` and a is `over_cap`.
  - `test_companies_over_the_cap_are_never_counted_or_searched`: `count_calls == ["a.example"]`.
  - `test_running_out_of_credits_stops_buying`: `balance=50`, a has 3 people and b has 1, so a is `bought` and b is `out_of_credits`, with `spent == 30` and `summary.out_of_credits == 1`.
  - `test_an_unreadable_balance_buys_nothing`: `balance_error=RuntimeError("account endpoint down")` gives `failed`, no search, and `"credits: account endpoint down"` in errors.
  - `test_a_guessed_domain_is_bought_only_after_its_homepage_answers`: routes only `https://b.example/`, so a is `domain_unconfirmed`, b is `bought`, and `count_calls == ["b.example"]`.
  - `test_spend_is_recorded_on_the_run`: `ctx.runs.info(run_id).spend == Spend(500, 3, 0, 30)`.
  - `test_a_search_that_fails_before_anyone_is_bought_is_failed_not_empty`: `failing_titles` holds a first-tier title, so the status is `failed` and `came_back_empty_before` stays False for a later run.
  - `test_preview_counts_without_buying_or_writing`: a has people, b has count 0, c has an emailed address, giving `Preview(to_buy=1, emailed=1, no_one=1)`. There are no search or verify calls, and `ctx.companies.find("a.example") is None`.
  - `test_restore_candidates_follows_the_recorded_order`
  - `test_max_cost`: `max_cost(ctx, 2) == 90`.

- [ ] **Step 3: Run them.** `.venv/Scripts/python -m pytest tests/contacts/test_ledger.py tests/pipeline/test_contacts.py -q`. Expected: FAIL.

- [ ] **Step 4: Implement** `contacts/ledger.py`, `pipeline/contacts.py`, the summary fields and `StageStatus`.

- [ ] **Step 5: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit.** `feat: buy contacts by title tier with a tenths credit ledger and skip rules`.

---

### Task 8: Company runs

**Files:**
- Create: `src/outreach/pipeline/companies.py`, `tests/pipeline/test_companies.py`
- Modify: `src/outreach/pipeline/summary.py`

**Interfaces:**
- Consumes:
  - `CompanyQuery` and `FakeCompanySearch` (Task 6)
  - `buy_contacts` and `restore_candidates` (Task 7)
  - `RunRepo.start`/`info`/`finish` and `CompanyInfoRepo` (Task 4)
- Produces:
  - `RunSummary` gains `found: dict[str, int] = field(default_factory=dict)`.
  - `run_companies(ctx: RunContext, filters: RunFilters, people: PeopleList, resume_run_id: int | None = None) -> RunSummary`

**What a company run does:**
- **New run:**
  1. `found = ctx.company_search.search(CompanyQuery(filters.sectors, filters.sizes, filters.location))`. Errors propagate, and they do so *before* any run row exists.
  2. Then `run_id = ctx.runs.start("companies", "", filters, people.name, datetime.now())`.
  3. For each position `i`:
     - upsert the company;
     - `company_info.insert(run_id, cid, CompanyInfo(i, "hunter", f.sector, f.size))`;
     - `set_stage(run_id, cid, "discover", "ok")`.
  4. Set `summary.found = {"hunter": len(found)}`.
- **Resume:** `info = ctx.runs.info(resume_run_id)` must be `kind == "companies"`; otherwise raise `ValueError(f"run {id} is not a company run")`. Candidates come from `restore_candidates`, and no search is made.
- **Both:** call `buy_contacts`, then `ctx.runs.finish(run_id, datetime.now())`, and return the summary.

- [ ] **Step 1: Write the failing tests** in `tests/pipeline/test_companies.py`:
  - `test_company_run_buys_contacts_at_hunters_companies`: two `FoundCompany` with sector and size give `summary.found == {"hunter": 2}` and `summary.bought == 2`, and the `company_info` sector and size round-trip.
  - `test_company_run_records_kind_people_and_filters`
  - `test_a_failed_search_creates_no_run`: `FakeCompanySearch(error=HunterAPIError("Hunter discover returned HTTP 500"))` raises, and `SELECT count(*) FROM runs` is 0.
  - `test_resume_does_not_search_again`: `len(company_search.queries)` doesn't change.
  - `test_a_second_run_moves_on_to_new_companies`: cap 2; run 1 buys a and b; run 2 has a and b `skipped_earlier_list` and buys c.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/pipeline/test_companies.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `pipeline/companies.py` and the `found` field.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: company runs from Hunter's company search`.

---

### Task 9: The contact report

**Files:**
- Create: `src/outreach/render/links.py`, `src/outreach/render/contact_report.py`, `src/outreach/render/templates/contacts.html.j2`, `tests/render/test_links.py`, `tests/render/test_contact_report.py`
- Modify: `src/outreach/core/filters.py` (`describe_filters`)

**Interfaces:**
- Consumes:
  - the repos (Task 4)
  - the contact statuses (Task 7)
  - `location_label` (Task 3)
  - `format_credits` (Task 2)
  - `RunSummary`
- Produces:

```python
# render/links.py  (port from `git show 224f9e0:src/outreach/render/report.py`)
def is_http_url(url: str | None) -> bool
def linkedin_search_url(full_name: str, company_name: str) -> str
def slugify(text: str) -> str
# core/filters.py
def describe_filters(filters: RunFilters) -> str
    # " · ".join(["; ".join(sectors) or "any sector", ", ".join(sizes) or "any size", location_label(location)]
    #             + [", ".join(work_modes)] when given and not all three)
    # e.g. RunFilters(("Software Development",), ("51-200",), Location("US", "TX")) -> "Software Development · 51-200 · Texas"
# render/contact_report.py
@dataclass
class PersonLine: full_name: str; title: str; email: str | None; profile_url: str | None; linkedin_search: str | None; emailed_on: date | None
@dataclass
class PostingLine: title: str; url: str; work_mode: str; via: str
@dataclass
class CompanyCard: name: str; domain: str; sector: str | None; size: str | None; hq: str | None; fit_unknown: bool
                   postings: list[PostingLine]; hiring_per_hunter: str | None; people: list[PersonLine]
@dataclass
class ListedCompany: name: str; domain: str; reason: str
@dataclass
class ContactReportView: run_id: int; kind: str; heading: str; filters_line: str; people: str; run_date: date
                         credits_line: str; with_contacts: list[CompanyCard]; no_one_found: list[ListedCompany]
                         earlier: list[ListedCompany]; left_out: list[ListedCompany]; details: dict[str, str]; errors: list[str]
def build_contact_view(ctx: RunContext, run_id: int, summary: RunSummary | None = None) -> ContactReportView
def render_contact_report(view: ContactReportView) -> str
def write_contact_report(view: ContactReportView, directory: Path) -> Path
```

**What the report shows** (the copy is pinned by the spec):

*Header.*
- The heading is `f"{role} · job run"` for jobs and `"Company run"` for companies.
- `run_date` is the run's `started_at.date()`.
- `credits_line` reads `f"{format_credits(spent)} credits used · {format_credits(before - spent)} left"`. When the balance is unknown it is `f"{format_credits(spent)} credits used"`, and with no spend recorded it is `"not recorded"`.

*Sections.* Cards and list entries follow the `company_info` position order. Each company is placed by its `contacts` status, falling back to its `discover` status:

| Status | Section | Reason |
|---|---|---|
| `bought` | `with_contacts` | (card) |
| `empty`, `no_matching_people` | `no_one_found` | `Hunter has no one matching "{people}" — cost nothing` |
| `skipped_earlier_list` | `earlier` | `on run {id}, {date:%d %b %Y} — not bought again` (from `bought_before(cid, people, run_id)`) |
| `skipped_emailed` | `earlier` | `you emailed {address} on {date:%d %b %Y}` (from `mark_for_company`) |
| `left_out_filters` | `left_out` | `doesn't match your sector or size` |
| `excluded_no_matching_posting` | `left_out` | `no full-time posting in the requested work mode or location` |
| `skipped_cap` | `left_out` | `over this run's HN cap of {max_new_companies}` |
| `over_cap` | `left_out` | `over this run's cap of {max_companies} companies` |
| `out_of_credits` | `left_out` | `not bought — out of credits` |
| `domain_unconfirmed` | `left_out` | `its website didn't answer, so its domain couldn't be confirmed` |
| `failed` | `left_out` | `failed — {stage error}` |
| discover `ok` with no contacts row | `left_out` | `not reached` |

*Card.*
- `sector` and `size` come from `company_info`.
- `hq` is `location_label(filters.location)` for the `hunter` and `hunter_hiring` sources, and `None` otherwise.
- `fit_unknown` is `fit == "unknown"`.
- Postings come from `ctx.postings.for_company`. Their `via` is `f"via HN Who is hiring? ({posted_at:%B %Y})"` when the source is `hn` (taking `posted_at` from `ctx.hn_posts.for_company`), and `"via job board"` otherwise.
- `hiring_per_hunter = role` when the source is `hunter_hiring`.
- People come from `ctx.run_contacts.for_company`:
  - The email is shown only if verified.
  - `linkedin_search` is set only when there is no profile URL.
  - `emailed_on` is `ctx.emailed.get(email)`.

*Details.*
- `"Companies found"`: counted from `company_info` rows whose discover status is `ok`, labelled `job boards`, `HN`, `Hunter` and `Hunter hiring search`, joined with `" · "`.
- `"Emails revealed"` and `"Addresses checked"` come from the stored spend.
- `"Credits"` is the credits line.
- For jobs runs, also `"HN thread"`, `"HN posts read / matching role"`, `"HN parsed / LLM-parsed / LLM-rejected"`, `"HN filtered out"` and `"HN kept / over the cap"`. They come from `summary` when it is given and read `"not recorded"` otherwise.
- `errors = summary.errors if summary else ["not recorded"]`.

*Template.*
- A self-contained HTML page with inline CSS. Reuse the look from `git show 224f9e0:src/outreach/render/templates/report.html.j2`.
- Section ids: `with-contacts`, `no-one-found`, `earlier`, `left-out`, `run-details`.
- A confirmed email renders as `<a href="mailto:{email}">{email}</a> <button class="copy" data-copy="{email}">Copy</button>`, with one small inline `<script>` that uses `navigator.clipboard.writeText`.
- An unconfirmed person shows `address not confirmed`.
- LinkedIn renders as `<a href=...>LinkedIn</a>` only when `is http_url`; otherwise `Search LinkedIn`.
- An emailed person shows `emailed {date:%d %b}`.
- Hunter's hiring finds show `Hiring {role}, per Hunter · no posting link`.
- A card with an unknown fit says `sector unknown`.
- Empty sections say `None.`

*File name.* `write_contact_report` names the file `f"{run_date}-run{run_id}-{kind}-{slug}.html"`, where the slug is `slugify(role)` for jobs and `slugify(first sector or "any sector")` for companies.

- [ ] **Step 1: Write the failing tests.**

  In `tests/render/test_links.py`, test `is_http_url` (`"https://x"` is true; `"javascript:alert(1)"`, `""` and `None` are false), `linkedin_search_url("Dana Reyes", "Kestrel Labs")` (ends with `keywords=Dana+Reyes+Kestrel+Labs`), and `slugify("Backend / Platform Eng") == "backend-platform-eng"`.

  `tests/render/test_contact_report.py` builds rows directly through the repos, using a `_bought(ctx, run_id, domain, people, source=..., sector=..., size=...)` helper:
  - `test_sections_follow_the_contacts_status`: bought, empty, skipped_earlier_list and over_cap land in the right lists, and the over_cap reason is `"over this run's cap of 10 companies"`.
  - `test_only_confirmed_addresses_are_shown`: the HTML contains `href="mailto:bo@a.example"` and `data-copy="bo@a.example"`, and for the unverified person, `address not confirmed` and no `mailto`.
  - `test_emailed_people_carry_the_date`: `emailed 12 Sep`.
  - `test_a_hunter_hiring_company_says_so_without_a_link`: `Hiring Backend Engineer, per Hunter · no posting link`.
  - `test_an_hn_company_links_its_post`: `via HN Who is hiring? (September 2026)`.
  - `test_non_http_links_render_as_text`: a posting URL of `javascript:alert(1)` never appears in an `href`.
  - `test_a_rerendered_view_says_not_recorded_for_the_hn_funnel_but_keeps_credits`: `summary=None`; the credits line reads `"3 credits used · 47 left"` and `details["HN thread"] == "not recorded"`.
  - `test_describe_filters`: `"Software Development · 51-200 · Texas"`, and with `work_modes=("remote",)` a trailing `" · remote"`.
  - `test_report_file_name_has_date_run_kind_and_slug`: `2026-09-21-run1-jobs-backend-engineer.html`.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/render -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `links.py`, `describe_filters`, `contact_report.py` and the template.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: contact report -- a card per company, confirmed emails, skip and left-out lists`.

---

### Task 10: CLI — `companies`, `sectors`, `emailed`, `report`

**Files:**
- Modify: `src/outreach/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `run_companies` (Task 8)
  - `preview_contacts` and `max_cost` (Task 7)
  - `build_contact_view` and `write_contact_report` (Task 9)
  - `EmailedRepo` and `normalize_address` (Task 4)
  - the filters (Task 3)
  - `load_sectors` (Task 3)
  - `ignored_warning` (Task 2)
- Produces:

```python
app = typer.Typer(help="Find the right people to email at the right companies.")
emailed_app = typer.Typer(help="Keep track of who you've emailed."); app.add_typer(emailed_app, name="emailed")
def build_context(config: Config, *, need_llm: bool, need_hunter: bool = True) -> RunContext
    # load_dotenv(); keys only for the adapters asked for; OUTREACH_FAKE_ADAPTERS=1 -> FakeLLM(),
    # FakeJobBoardSource([]), FakeHNSource(), FakePeopleFinder(balance=0), FakeCompanySearch(), no key checks,
    # and a Fetcher whose transport is httpx.MockTransport(lambda request: httpx.Response(404)) with
    # sleep=lambda s: None -- so nothing in fake mode can reach the network, whatever a test patches
@app.command() def companies(sector: list[str] | None = Option(None, "--sector"), size: list[str] | None = Option(None, "--size"),
                             location: str | None = Option(None, "--location"), people: str | None = Option(None, "--people"),
                             dry_run: bool = Option(False, "--dry-run"), resume: int | None = Option(None, "--resume"))
@app.command() def sectors(word: str | None = Argument(None))
@emailed_app.command("add") def emailed_add(addresses: list[str] = Argument(...), on: str | None = Option(None, "--on"))
@emailed_app.command("list") def emailed_list()
@emailed_app.command("remove") def emailed_remove(addresses: list[str] = Argument(...))
@app.command() def report(run_id: int = Argument(...))
```

**Order of checks in `companies`:**
1. `--sector` is required unless `--resume` is given: exit 2 with `--sector is required`.
2. Parse the sectors, sizes and location. A `FilterError` exits 2 with its message.
3. Load the config. A `ConfigError` exits 2. Print `ignored_warning` to stderr when it isn't `None`.
4. Resolve the people list from `--people` or `config.companies_people`. An unknown name exits 2 with `Unknown people list "x". Known lists: hiring, sponsors.` (names sorted).
5. `build_context(config, need_llm=False)`. A `ConfigError` exits 2.

On `--resume`, the run's stored people list and filters are used, and a run that isn't a company run exits 1 with `Run {id} is not a company run.`

**Output** (the copy is pinned):

*`companies` dry run*
1. `Filters: {describe_filters(filters)}`
2. `Hunter found {n} companies.`
3. `Skipped: {earlier_list} on an earlier list, {emailed} where you've emailed someone, {no_one} with no one matching.`
4. `Dry run: up to {to_buy} companies would be bought, at most {format_credits(max_cost)} credits; {format_credits(balance)} left.`

When the balance read fails, the last line becomes `... at most {X} credits, but the credit balance could not be checked: {exc}` on stderr, and the command exits 1.

*`companies` real run*
- `Run {id}: contacts bought at {summary.bought} companies, {format_credits(summary.spent)} credits used. Report: {path}`
- When `summary.out_of_credits` is non-zero, also print `{k} companies were not bought because credits ran out.`
- When Hunter found nothing, in either mode, print `No company matched these filters. Try another sector ("outreach sectors <word>") or a wider size or location.`
- A `HunterAPIError` from the search exits 1 with `Hunter's company search failed: {exc}`.

*`sectors`*
- Prints the matching names, one per line, then `Size bands: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+`.
- With no match, prints `No sector contains "{word}".` and exits 1.

*`emailed add`*
- Validates every address before writing anything. A bad one exits 2 with `"{text}" is not an email address`.
- `--on` must be `YYYY-MM-DD`; otherwise exit 2 with `--on must be a date like 2026-10-11, not "{on}"`.
- On success prints `Marked {n} address(es) as emailed on {date}.`, with `address` when n is 1 and `addresses` otherwise.

*`emailed list`*
- One line per entry: `{date}  {address}`, followed by `  {name} · {title} · {company}` when those are known.
- When the list is empty: `No one is marked as emailed yet.`

*`emailed remove`*
- Prints `Removed {n}.`, and `Not on the list: {address}` on stderr for each address that wasn't there.
- Exits 1 when nothing was removed.

*`report`*
- An unknown id prints `No run with id {id}` and exits 1.
- A run older than schema 4 prints `Run {id} was made by an older version of this tool; see its saved HTML report.` and exits 1.
- Otherwise it calls `build_context(config, need_llm=False, need_hunter=False)`, writes the view with `summary=None`, and prints `Report: {path}`.

The `emailed` and `report` commands need only the config's paths, and no keys.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`. Keep the autouse fixture and use the `_TEST_CONFIG` from Task 2. Each test sets env vars with `monkeypatch`, and fake data comes from `monkeypatch.setattr` on `FakeCompanySearch.search` and on the `FakePeopleFinder` methods:
  - `test_companies_needs_only_the_hunter_key`: `ANTHROPIC_API_KEY` is unset, `HUNTER_API_KEY` is set and fakes are on, so `--dry-run` exits 0.
  - `test_missing_hunter_key_aborts_before_any_network_call`: no keys and no fakes; exits 2 with `HUNTER_API_KEY` in the output.
  - `test_unknown_sector_exits_2_before_anything_runs`: `outreach.cli.build_context` is patched to raise, and `--sector "software developement"` gives exit 2 with `Did you mean: Software Development`.
  - `test_bad_size_and_location_exit_2`
  - `test_unknown_people_list_exits_2`: `Unknown people list "nobody". Known lists: hiring, sponsors.`
  - `test_a_sector_with_a_comma_is_one_sector`: the output contains `Filters: Health, Wellness and Fitness · any size · United States`.
  - `test_companies_dry_run_reports_the_maximum_cost`: 3 companies with counts of 3 and a balance of 500 give `Dry run: up to 3 companies would be bought, at most 13.5 credits; 50 left.`
  - `test_companies_dry_run_with_an_unreadable_balance_exits_1`
  - `test_companies_with_no_match_says_why`
  - `test_companies_run_writes_a_report`: the output contains `Report:` and the file exists under `reports/`.
  - `test_sectors_lists_matches_and_size_bands`: `sectors software` prints `Software Development` and the `Size bands:` line.
  - `test_sectors_with_no_match_exits_1`
  - `test_emailed_add_list_remove_round_trip`: adding `" Dana@Kestrel.example "` means `list` shows `dana@kestrel.example`, and then `remove dana@kestrel.example` prints `Removed 1.`
  - `test_emailed_add_rejects_a_non_address_and_writes_nothing`
  - `test_emailed_remove_of_an_unknown_address_exits_1`
  - `test_report_rerenders_a_v4_run`
  - `test_report_refuses_an_older_run`
  - `test_report_rejects_an_unknown_run`
  - `test_ignored_config_settings_warn`
  - `test_real_adapters_build_one_source_per_configured_board`: adapt it to `build_context(load_config(Path("config.toml")), need_llm=True)`.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/test_cli.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** the commands and `build_context`.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: outreach companies, sectors, emailed and report commands`.

---

### Task 11: Job runs pipeline

**Files:**
- Create: `src/outreach/pipeline/jobs.py`, `tests/pipeline/test_jobs.py`
- Modify:
  - `src/outreach/pipeline/discovery.py` (the `location` parameter)
  - `src/outreach/pipeline/summary.py`
  - `src/outreach/sources/jobboards/fake.py` (add a `calls` counter)
  - `tests/pipeline/test_discovery.py`

**Interfaces:**
- Consumes:
  - `posting_in_location` (Task 3)
  - `CompanyQuery` and `FakeCompanySearch` (Task 6)
  - `buy_contacts` and `restore_candidates` (Task 7)
  - the repos (Task 4)
- Produces:

```python
# discovery.py: select_hn_candidates(ctx, terms, work_modes, known_domains, summary, *, allow_llm=True,
#   location: Location = Location())  -- replaces `region`; keeps a post only if
#   matches_region(parsed.location, location.country) and posting_in_location(parsed.location, parsed.work_mode, location)
#   and posting_matches(...); sets summary.hn_needs_llm = selection.needs_llm
# summary.py: RunSummary gains left_out: int = 0, hn_needs_llm: int = 0
# jobs.py
@dataclass(frozen=True)
class JobProspect:
    domain: str; name: str; source: str                 # "board" | "hn" | "hunter_hiring"
    postings: tuple[PostingRef, ...] = (); hn: HNCandidate | None = None; hn_status: str | None = None
    sector: str | None = None; size: str | None = None; fit: str = "fits"
    left_out: str | None = None                         # "excluded_no_matching_posting" | "left_out_filters" | "skipped_cap"
    guessed_domain: bool = False
def find_job_prospects(ctx: RunContext, terms: Sequence[str], filters: RunFilters, work_modes: frozenset[WorkMode],
                       summary: RunSummary, *, use_hn: bool = True, dry_run: bool = False) -> list[JobProspect]
    # dry_run: no LLM fallback (select_hn_candidates(allow_llm=False)) and no searches of the boards HN posts link;
    # every kept HN company then uses its post as the posting, so the company count is the same either way
def run_jobs(ctx: RunContext, role: str, filters: RunFilters, work_modes: frozenset[WorkMode], people: PeopleList,
             *, use_hn: bool = True, resume_run_id: int | None = None) -> RunSummary
# FakeJobBoardSource gains `calls: int` (incremented per search)
```

**How `find_job_prospects` finds companies.** It reads only; it never writes.

1. **Boards.**
   - Call `ctx.job_board.search(terms, filters.location.country)`. Any new entries in `ctx.job_board.errors` (when it has that attribute) are appended as `f"discover: {e}"`.
   - Group the postings by `canonical_domain`. A posting whose domain can't be canonicalized becomes an error and is skipped.
   - A posting matches when `posting_matches(p, work_modes)` and `posting_in_location(p.location, p.work_mode, filters.location)`.
   - Each group becomes `JobProspect(domain, group[0].company_name, "board", matching, guessed_domain=True, left_out=None if matching else "excluded_no_matching_posting")`.
2. **HN** (when `use_hn and ctx.hn is not None and ctx.config.hn.enabled`), inside `try`/`except` with `summary.errors.append(f"hn: {exc}")`:
   - Run `select_hn_candidates` with `known_domains` = the board domains, `location=filters.location` and `allow_llm=not dry_run`.
   - Unless `dry_run`, run `hn_board_source(ctx.fetcher, with_board).search(terms, filters.location.country)` for the kept candidates that link a board. Postings from it take the post's domain and company name.
   - A candidate counts as covered when one of its postings passes both filters. An uncovered candidate gets `candidate_posting`.
   - Kept candidates become `JobProspect(..., "hn", postings, hn=c, hn_status="kept")`. Skipped ones get `hn_status="skipped_cap"` and `left_out="skipped_cap"`. Known (board) domains are ignored.
3. **Hunter's hiring search** (when `ctx.company_search` is present), inside `try`/`except` with `f"hunter: {exc}"`:
   - Search with `CompanyQuery(filters.sectors, filters.sizes, filters.location, hiring_titles=tuple(terms))`.
   - Domains not already seen become `JobProspect(f.domain, f.name, "hunter_hiring", sector=f.sector, size=f.size)`.
4. **Check against sector and size** (only when `filters.sectors or filters.sizes`):
   - Call `ctx.company_search.check` on the board and HN prospects that are not left out, inside `try`/`except` with `f"hunter check: {exc}"`. On error the result is `{}`, so every company counts as unknown.
   - A domain present with a `FoundCompany` takes its sector and size.
   - A domain present with `None` gets `fit="no"` and `left_out="left_out_filters"`.
   - An absent domain gets `fit="unknown"`.
5. **Order.** Kept `fits` prospects come first (board, then HN, then Hunter hiring, each in its own order), then kept `unknown` ones, then the left-out ones.
6. **Count.** `summary.found` counts the kept prospects by source, and `summary.left_out` counts those left out by `left_out_filters` or `excluded_no_matching_posting`.

**How `run_jobs` runs.**
- **New run:**
  1. `terms = ctx.llm.expand_titles(role)`, then `find_job_prospects`.
  2. `run_id = ctx.runs.start("jobs", role, filters, people.name, datetime.now())`.
  3. Record each prospect in order:
     - upsert the company;
     - `company_info.insert(CompanyInfo(i, source, sector, size, fit, guessed_domain))`;
     - insert its postings;
     - for an `hn` prospect, `ctx.hn_posts.insert(run_id, cid, HNPostRecord(post.item_id, summary.hn_thread_title or "", post.posted_at.date(), method, hn_status, parsed, post.html))`;
     - `set_stage("discover", left_out or "ok")`.
  4. The candidates are the kept prospects, in order.
- **Resume:** the stored `kind` must be `"jobs"`; otherwise raise `ValueError`. There is no LLM call and no discovery; candidates come from `restore_candidates`.
- **Both:** call `buy_contacts`, then `finish`.

- [ ] **Step 1: Write the failing tests.**

  In `tests/pipeline/test_discovery.py`, add `test_location_filters_hn_posts`: with `Location("US", "TX")`, the Austin post is kept and the Chicago post is dropped (`hn_filtered` counts it).

  `tests/pipeline/test_jobs.py` reuses `ACME_POST`, `DESIGN_POST`, `SOLO_POST` and the `ROUTES` Ashby payloads from `git show 224f9e0:tests/pipeline/test_hn_runner.py`, with the people finder's data keyed by domain:
  - `test_a_board_company_is_a_guessed_domain_that_needs_its_homepage`: Good Co's posting at `good.example` gives `domain_unconfirmed` without a route, and `bought` with `"https://good.example/"` routed.
  - `test_hn_company_with_an_ats_board_uses_the_posts_domain`: the posting URL is `https://jobs.ashbyhq.com/x/Back` and `ctx.companies.find("acmehq.com") is None`.
  - `test_an_ats_board_without_the_role_falls_back_to_the_post`: the URL is `https://news.ycombinator.com/item?id=302`.
  - `test_hn_companies_over_the_cap_spend_nothing`: with `max_new_companies=1`, sololabs gets discover status `skipped_cap`, is absent from `people.count_calls`, and `hn_posts` records it as `skipped_cap`.
  - `test_an_hn_outage_keeps_board_companies`: `"hn: algolia down"` is in the errors and the board company is still bought.
  - `test_hunter_hiring_companies_come_after_posting_companies`: verified by `company_info` positions and sources.
  - `test_a_company_found_twice_keeps_its_posting`: an HN domain also returned by Hunter's hiring search keeps the source `hn`.
  - `test_the_sector_check_leaves_out_known_misfits_and_keeps_unknowns`: `known={"good.example": fits, "thin.example": misfit}`, so ghost is unknown and is ordered after good, and thin gets `left_out_filters`.
  - `test_no_sector_or_size_means_no_check`: `company_search.checks == []`.
  - `test_location_filters_board_postings`: with `Location("US", "TX")`, the Austin posting is kept and Chicago gets `excluded_no_matching_posting`.
  - `test_resume_does_not_rediscover`: `job_board.calls`, `hn.calls` and the LLM's titles are unchanged.
  - `test_runs_record_kind_jobs_and_the_role`
  - `test_a_dry_run_never_calls_the_llm_or_hn_linked_boards`: with `dry_run=True`, `llm.parse_calls == 0`, no Ashby URL is fetched, and ACME_POST's company still counts as one HN prospect with the HN item as its posting.

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/pipeline/test_jobs.py tests/pipeline/test_discovery.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** the discovery change, `jobs.py`, the summary fields and the fake board counter.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: job runs from boards, HN and Hunter's hiring search`.

---

### Task 12: CLI — `outreach jobs`

**Files:**
- Modify: `src/outreach/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `run_jobs` and `find_job_prospects` (Task 11)
  - `parse_work_modes` (kept)
  - the Task 10 helpers
- Produces:

```python
@app.command()
def jobs(role: str | None = Option(None, "--role"), sector: list[str] | None = Option(None, "--sector"),
         size: list[str] | None = Option(None, "--size"), location: str | None = Option(None, "--location"),
         work_mode: str | None = Option(None, "--work-mode"), people: str | None = Option(None, "--people"),
         no_hn: bool = Option(False, "--no-hn"), dry_run: bool = Option(False, "--dry-run"),
         resume: int | None = Option(None, "--resume"))
```

**Order of checks in `jobs`:**
1. `--role` is required unless `--resume` is given (exit 2, `--role is required`).
2. Parse the work modes (exit 2).
3. Parse the filters (exit 2). `RunFilters.work_modes = tuple(sorted(modes))`.
4. Load the config (exit 2, then print the ignored warning).
5. Resolve the people list (default `config.jobs_people`; exit 2 on an unknown name).
6. `build_context(config, need_llm=True)`.

**Warnings** (stderr):
- When `OUTREACH_CONTACT_EMAIL` is blank: the existing warning text.
- When there are no board tokens and HN is off for this run: `Warning: no board tokens in config.toml and HN discovery is off -- companies can only come from Hunter's hiring search.`

**Dry-run output**, in this order:
1. `Filters: {describe_filters(filters)}`
2. The HN line, only when HN ran: `HN: {title} — {read} posts, {matched} match the role, {parsed} parsed, {needs_llm} would need the LLM fallback, {kept} kept after the cap.`
3. `Found: {board} from job boards, {hn} from HN, {hiring} from Hunter's hiring search; {left_out} left out by your filters.`
4. The `Skipped:` line and the `Dry run:` cost line, exactly as for companies.

The dry run calls `find_job_prospects(..., dry_run=True)` and then `preview_contacts` over the kept prospects' domains, in order.

**Real-run output** is the same as for companies. When nothing was found, print `No company matched. Try another role, fewer filters, or add board tokens in config.toml.`

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`:
  - `test_jobs_needs_the_anthropic_key`
  - `test_bad_work_mode_exits_before_anything_runs`: `build_context` is patched to raise; exit 2, and the output names `sometimes`.
  - `test_jobs_dry_run_reports_the_hn_funnel_without_llm_calls`: `FakeHNSource.latest_thread` returns `hn_thread()` and `FakeLLM.parse_job_post` raises. Assert the exact line `HN: Ask HN: Who is hiring? (September 2026) — 11 posts, 8 match the role, 5 parsed, 3 would need the LLM fallback, 4 kept after the cap.` These are the same counts the old dry-run test pinned. With no location given, the filter is the same US check, and the cap of 15 doesn't bind.
  - `test_no_hn_flag_disables_discovery`
  - `test_jobs_dry_run_counts_each_source`
  - `test_jobs_run_writes_a_report`
  - `test_warns_when_there_are_no_tokens_and_hn_is_off`
  - `test_the_contact_email_warning_is_for_jobs_only`

- [ ] **Step 2: Run them.** `.venv/Scripts/python -m pytest tests/test_cli.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `jobs`.

- [ ] **Step 4: Run the suite.** `.venv/Scripts/python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `feat: outreach jobs command with dry run`.

---

### Task 13: Docs

**Files:**
- Modify:
  - `README.md` (rewrite)
  - `.env.example`
  - `docs/superpowers/specs/2026-10-11-contact-finder-design.md` (an "As built" section)
  - the three older specs in `docs/superpowers/specs/` (a status line each)
- Create: `docs/getting-started.md`

**Content pinned by the spec.** Every command, flag and output line must match what `--help` and the code actually produce. Copy them from real (fake-adapter) runs.

**`README.md`**, in this order:
1. **What it is.** A contact finder for cold email with two uses:
   - job outreach to hiring managers;
   - asking companies to sponsor a student org or to give talks.

   It never writes or sends email.
2. **What a run costs.** This goes up front:
   - Hunter charges 1 credit per email revealed and 0.5 per address check.
   - Company search and email counts are free.
   - The free plan is 50 credits a month, which is about 11 companies at 3 people each.
   - Always `--dry-run` first.
3. **Install.**
4. **Keys.** `HUNTER_API_KEY` is needed for both commands; `ANTHROPIC_API_KEY` and `OUTREACH_CONTACT_EMAIL` only for `jobs`.
5. **Quick start for a company run.** `sectors` → `companies --dry-run` → `companies` → open the report → `emailed add`.
6. **Quick start for a job run.**
7. **Commands reference:** `jobs`, `companies`, `sectors`, `emailed add`/`list`/`remove`, `report`.
8. **Filters.**
   - Sector uses exact Hunter names.
   - Size uses the bands.
   - Location rules:
     - two letters always means a US state;
     - it is HQ for company runs and Hunter's finds, and the job's location for postings;
     - remote US jobs pass;
     - non-US matching is by country name only.
9. **People lists.** Tiers, exclusions, the shipped `hiring` and `sponsors` lists, and how to add your own.
10. **How buying works.**
    - companies on an earlier list are skipped;
    - companies where someone was emailed are skipped;
    - the free email count comes first;
    - the cap, which empty searches don't use up;
    - title tiers;
    - addresses Hunter confirmed within 90 days aren't checked again;
    - running out of credits.
11. **Reading the report.**
12. **`config.toml` reference.**
13. **AI calls.** Exactly two jobs, both in job runs: `expand_titles` and `parse_job_post`. Neither decides who or what gets bought.
14. **Tests.**
15. **Older runs.**

**`docs/getting-started.md`.** Written for a first-time user (the friend):
- install, with Windows and macOS commands;
- creating a Hunter account and copying the API key;
- `.env` with only `HUNTER_API_KEY` (and never committing it);
- `outreach sectors marketing`;
- a dry run, then a real run with a small cap (`[run] max_companies = 3`);
- opening the report;
- `outreach emailed add`;
- what 50 credits buy.

**`.env.example`.** Add a comment above each key saying which command needs it.

**Older specs.** In each of the V1, A and B specs, add a line under **Status**: `**Superseded:** by 2026-10-11-contact-finder-design.md, which removes the research half described here.`

**"As built" in the new spec.** Record:
- the plan decisions listed under "Decisions this plan makes inside the spec's room";
- any departures made during implementation;
- the confirmed Hunter field names (Account credit counter, Domain Search verification field);
- these known gaps:
  - non-US locations match by country name only;
  - Discover's free plan returns the same top 100 companies per filter combination;
  - the Email Count pre-check is per domain, not per title.

- [ ] **Step 1: Write the docs.** Run each documented command against fake adapters (`OUTREACH_FAKE_ADAPTERS=1` with dummy keys, from a scratch directory that holds its own `config.toml`) and paste the real output.

- [ ] **Step 2: Check them.** Run `.venv/Scripts/python -m pytest -q`; expected PASS. Run `grep -n "outreach run\|--sector fintech\|Growth & Establishment\|evidence gate" README.md docs/getting-started.md`; expected no output.

- [ ] **Step 3: Commit.** `docs: README and getting-started for the contact finder; spec as built`.
