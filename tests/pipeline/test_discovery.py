"""HN selection: prefilter, parse, LLM fallback, filter, pre-rank, cap -- before any paid call."""
from dataclasses import replace
from datetime import datetime

from outreach.config import HNConfig
from outreach.llm.fake import FakeLLM
from outreach.pipeline.discovery import select_hn_candidates
from outreach.pipeline.runner import RunSummary
from outreach.sources.hn import FakeHNSource
from outreach.types import ALL_WORK_MODES, ParsedPost
from tests.pipeline.factories import build_context, hn_thread

TERMS = ["backend engineer", "platform engineer"]


def _select(ctx, known=frozenset(), allow_llm=True, run_id=0):
    summary = RunSummary(run_id=run_id)
    selection = select_hn_candidates(ctx, run_id, TERMS, ALL_WORK_MODES, known, summary,
                                     allow_llm=allow_llm)
    return selection, summary


def _ctx(item_ids=None, extra=(), parsed_posts=None, **hn):
    ctx = build_context()
    ctx.hn = FakeHNSource(hn_thread(item_ids, list(extra)))
    ctx.llm = FakeLLM(parsed_posts=parsed_posts or {}, titles=TERMS)
    ctx.config = replace(ctx.config, hn=replace(HNConfig(), **hn))
    return ctx


def _domains(candidates):
    return [c.parsed.domain for c in candidates]


def test_prefilter_runs_before_any_llm_call():
    designer = ParsedPost("Paintbox", "paintbox.co", None, "designer", "", "remote", "unknown")
    ctx = _ctx(item_ids=[111], extra=[(201, 'Paintbox hiring a designer, remote <a href="https://paintbox.co">x</a>')],
               parsed_posts={"Paintbox": designer})
    selection, summary = _select(ctx)
    assert ctx.llm.parse_calls == 0
    assert (summary.hn_posts_read, summary.hn_role_matched) == (2, 0)
    assert selection.kept == []


def test_llm_fallback_is_capped():
    ctx = _ctx(item_ids=[106, 108, 112], llm_fallback_max=1)
    _select(ctx)
    assert ctx.llm.parse_calls == 1


def test_llm_output_that_fails_validation_is_dropped():
    invented = ParsedPost("Larkspur", "invented.io", None, "backend engineers", "", "remote", "unknown")
    ctx = _ctx(item_ids=[108], parsed_posts={"Larkspur": invented})
    selection, summary = _select(ctx)
    assert (summary.hn_llm_rejected, summary.hn_llm_parsed) == (1, 0)
    assert selection.kept == []


def test_valid_llm_output_is_kept_as_llm_parsed():
    ok = ParsedPost("Larkspur", "larkspur.ai", None, "backend engineers", "US", "remote", "unknown")
    ctx = _ctx(item_ids=[108], parsed_posts={"Larkspur": ok})
    selection, summary = _select(ctx)
    assert summary.hn_llm_parsed == 1
    assert [(c.parsed.domain, c.method) for c in selection.kept] == [("larkspur.ai", "llm")]


def test_non_us_posts_are_filtered():
    selection, summary = _select(_ctx(item_ids=[107]))
    assert summary.hn_filtered == 1 and selection.kept == []


def test_one_company_per_domain_first_post_wins():
    selection, _ = _select(_ctx(item_ids=[101, 110]))
    assert [(c.parsed.domain, c.post.item_id) for c in selection.kept] == [("acmerobotics.com", 101)]


def test_growth_hints_rank_first_and_cap_applies():
    # 101 says Series B (growth), 102 and 105 say nothing (unknown), 104 says
    # "team of 40" (seed): growth first, then unknown in thread order, seed last.
    selection, summary = _select(_ctx(item_ids=[101, 102, 104, 105], max_new_companies=2))
    assert _domains(selection.kept) == ["acmerobotics.com", "zetapay.com"]
    assert _domains(selection.skipped_cap) == ["oysterhr.com", "quillhealth.io"]
    assert (summary.hn_kept, summary.hn_skipped_cap) == (2, 2)
    assert selection.kept[0].stage_hint.stage.value == "growth"


def test_recently_researched_company_is_skipped():
    ctx = _ctx(item_ids=[101, 102])
    cid = ctx.companies.upsert("zetapay.com", "Zeta Pay", None, None)
    earlier = ctx.runs.create("r", "s", "US", (1, 2000), datetime(2026, 9, 10))
    ctx.runs.set_stage(earlier, cid, "evidence", "ok")
    selection, summary = _select(ctx)
    assert _domains(selection.skipped_recent) == ["zetapay.com"]
    assert _domains(selection.kept) == ["acmerobotics.com"]
    assert summary.hn_skipped_recent == 1


def test_known_domains_are_neither_ranked_nor_capped():
    # max_new_companies=0: known domain lands in selection.known, kept is empty
    selection, summary = _select(_ctx(item_ids=[102], max_new_companies=0),
                                 known=frozenset({"zetapay.com"}))
    assert _domains(selection.known) == ["zetapay.com"]
    assert selection.kept == [] and selection.skipped_cap == [] and summary.hn_kept == 0


def test_dry_mode_never_calls_the_llm():
    ctx = _ctx(item_ids=[106, 108])
    selection, _ = _select(ctx, allow_llm=False)
    assert ctx.llm.parse_calls == 0
    assert selection.needs_llm == 2


def test_no_thread_is_an_hn_error_not_a_crash():
    ctx = build_context()
    ctx.hn = FakeHNSource(None)
    selection, summary = _select(ctx)
    assert selection.kept == [] and selection.thread is None
    assert "hn: no Who is hiring? thread found" in summary.errors


def test_a_role_named_only_in_the_body_of_a_pipe_post_does_not_match():
    designer = (401, 'Paintbox | Product Designer | Remote (US) | REMOTE<p>You will work closely '
                     'with our backend engineers. <a href="https://paintbox.co">paintbox.co</a>')
    selection, summary = _select(_ctx(item_ids=[], extra=[designer]))
    assert summary.hn_role_matched == 0 and selection.kept == []


def test_generic_openings_in_the_first_line_fall_back_to_the_body():
    multi = (402, 'Quarry | Multiple engineering roles | Remote (US) | REMOTE<p>Hiring a Backend '
                  'Engineer and an SRE. <a href="https://quarry.dev">quarry.dev</a>')
    _, summary = _select(_ctx(item_ids=[], extra=[multi]))
    assert summary.hn_role_matched == 1


def test_free_text_posts_still_match_on_the_body():
    _, summary = _select(_ctx(item_ids=[108]))
    assert summary.hn_role_matched == 1
