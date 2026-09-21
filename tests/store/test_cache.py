from outreach.store.cache import DocumentCache


def test_store_returns_stable_hash_and_reads_back(tmp_path):
    cache = DocumentCache(tmp_path)
    h1 = cache.store(b"hello world")
    h2 = cache.store(b"hello world")
    assert h1 == h2
    assert cache.has(h1)
    assert cache.read(h1) == "hello world"


def test_different_content_gives_different_hash(tmp_path):
    cache = DocumentCache(tmp_path)
    assert cache.store(b"a") != cache.store(b"b")


def test_has_is_false_for_unknown_hash(tmp_path):
    assert DocumentCache(tmp_path).has("0" * 64) is False
