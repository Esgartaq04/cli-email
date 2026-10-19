import json

import httpx

from outreach.llm.anthropic_client import AnthropicLLM


def _llm(reply_text: str, seen: dict | None = None) -> AnthropicLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": reply_text}]})

    return AnthropicLLM("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_requests_name_a_current_model():
    seen: dict = {}
    _llm('["Backend Engineer"]', seen).expand_titles("Backend Engineer")
    assert seen["body"]["model"] == "claude-sonnet-5"


def test_expand_titles_keeps_clean_titles_in_order():
    titles = _llm('["Backend Engineer", "Software Engineer, Backend"]').expand_titles("Backend")
    assert titles == ["Backend Engineer", "Software Engineer, Backend"]


def test_expand_titles_drops_blank_whitespace_and_non_string_items():
    # A blank would match every posting (`"" in title.lower()`), and a JSON null
    # must not turn into the title "None".
    reply = '["Backend Engineer", "", "   ", null, 7, ["Nested"], {"a": 1}, "  Data Engineer  "]'
    assert _llm(reply).expand_titles("Backend") == ["Backend Engineer", "Data Engineer"]


def test_expand_titles_with_nothing_usable_is_empty():
    assert _llm('["", "  ", null]').expand_titles("Backend") == []
    assert _llm("[]").expand_titles("Backend") == []


def _post_reply(**overrides) -> str:
    reply = {"company": "Acme", "domain": "acme.io", "ats_kind": "lever", "ats_token": "acme",
             "role": "Backend Engineer", "location": "Austin, TX", "work_mode": "remote",
             "employment_type": "full_time"}
    reply.update(overrides)
    return json.dumps(reply)


def test_parse_job_post_maps_json():
    seen: dict = {}
    from outreach.types import ParsedPost
    post = _llm(_post_reply(), seen).parse_job_post("Acme is hiring...")
    assert post == ParsedPost("Acme", "acme.io", ("lever", "acme"), "Backend Engineer",
                              "Austin, TX", "remote", "full_time")
    assert "Who is hiring?" in seen["body"]["system"]
    assert "never infer" in seen["body"]["system"]


def test_parse_job_post_null_is_none():
    assert _llm("null").parse_job_post("not a job post") is None
    assert _llm("[]").parse_job_post("not a job post") is None


def test_parse_job_post_unknown_mode_is_unknown():
    post = _llm(_post_reply(work_mode="sometimes", employment_type="gig",
                            ats_kind=None, ats_token=None)).parse_job_post("x")
    assert (post.work_mode, post.employment_type, post.ats) == ("unknown", "unknown", None)


def test_parse_job_post_without_company_or_domain_is_none():
    assert _llm(_post_reply(company=None)).parse_job_post("x") is None
    assert _llm(_post_reply(domain="")).parse_job_post("x") is None
