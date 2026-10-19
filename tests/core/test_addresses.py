import pytest

from outreach.core.addresses import normalize_address


def test_an_address_is_trimmed_and_lowercased():
    assert normalize_address(" Dana@Kestrel.Example ") == "dana@kestrel.example"


def test_an_already_normal_address_is_unchanged():
    assert normalize_address("dana@kestrel.example") == "dana@kestrel.example"


@pytest.mark.parametrize("text", ["dana", "dana@", "@x.com", "a@b", "a b@c.com", "a@@b.com"])
def test_text_that_is_not_an_address_is_refused(text):
    with pytest.raises(ValueError, match=f'^"{text}" is not an email address$'):
        normalize_address(text)


@pytest.mark.parametrize("text", ["", "   ", "dana@kestrel.", "dana@.example", "dana@kestrel..example"])
def test_an_address_with_an_empty_part_is_refused(text):
    with pytest.raises(ValueError, match="is not an email address"):
        normalize_address(text)
