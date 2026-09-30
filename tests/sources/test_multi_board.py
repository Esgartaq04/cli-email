from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.sources.jobboards.multi import MultiBoardSource
from outreach.types import PostingRef


class Exploding:
    def search(self, role_terms, region):
        raise RuntimeError("boom")


def _posting(title="Backend Engineer"):
    return PostingRef("Acme", "acme.com", title, "https://x/1", "Remote - US")


def test_multi_board_keeps_other_sources_when_one_raises():
    posting = _posting()
    multi = MultiBoardSource([Exploding(), FakeJobBoardSource([posting])])
    assert multi.search(["x"], "US") == [posting]
    assert multi.errors == ["Exploding: boom"]


def test_multi_board_concatenates_in_source_order():
    a, b = _posting("A"), _posting("B")
    multi = MultiBoardSource([FakeJobBoardSource([a]), FakeJobBoardSource([b])])
    assert multi.search(["x"], "US") == [a, b]
    assert multi.errors == []
