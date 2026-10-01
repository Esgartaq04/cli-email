import json

import httpx

from outreach.core.themes import ALL_THEMES
from outreach.llm.anthropic_client import AnthropicLLM


def _llm(reply_text: str, seen: dict | None = None) -> AnthropicLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": reply_text}]})

    return AnthropicLLM("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _claims(*themes: str) -> str:
    return json.dumps([{"claim": f"c{i}", "quote": f"q{i}", "theme": t}
                       for i, t in enumerate(themes)])


def test_the_extraction_prompt_lists_every_allowed_theme():
    seen: dict = {}
    _llm("[]", seen).extract_claims("page text")
    for theme in ALL_THEMES:
        assert theme in seen["body"]["system"]


def test_a_theme_outside_the_list_is_dropped():
    """Free-form themes never collide across sources, so they cannot corroborate."""
    claims = _llm(_claims("ai-ml", "vibes", "")).extract_claims("text")
    assert [c.theme for c in claims] == ["ai-ml"]


def test_theme_spelling_is_normalised_onto_the_list():
    claims = _llm(_claims("Product Launch", "PUBLIC_API_SDK")).extract_claims("text")
    assert [c.theme for c in claims] == ["product-launch", "public-api-sdk"]


def test_every_allowed_theme_is_already_in_normal_form():
    from outreach.core.clustering import normalize_theme
    assert all(normalize_theme(t) == t for t in ALL_THEMES)
    assert len(set(ALL_THEMES)) == len(ALL_THEMES)


def test_requests_name_a_current_model():
    seen: dict = {}
    _llm("[]", seen).extract_claims("text")
    assert seen["body"]["model"] == "claude-sonnet-5"


def test_a_legacy_bottleneck_theme_is_now_dropped():
    """Old bottleneck slugs are no longer in the closed list, so they cannot cluster."""
    assert _llm(_claims("reliability")).extract_claims("text") == []


def test_the_summary_prompt_is_about_what_they_are_building():
    seen: dict = {}
    _llm('{"summary": "They are building a thing."}', seen).write_summary(
        "claim", ["quote"])
    assert "what a company is building" in seen["body"]["system"]
