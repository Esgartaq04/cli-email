from outreach.llm.fake import FakeLLM
from outreach.types import ParsedPost


def test_fake_parse_job_post_matches_by_substring_and_counts():
    acme = ParsedPost("Acme", "acme.io", None, "Backend Engineer", "", "remote", "unknown")
    llm = FakeLLM(parsed_posts={"Acme is hiring": acme})
    assert llm.parse_job_post("<p>Acme is hiring a Backend Engineer, remote") == acme
    assert llm.parse_job_post("Globex is hiring") is None
    assert llm.parse_calls == 2
