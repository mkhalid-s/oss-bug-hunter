"""Tests for scripts/_lib.py — shared keyword utils."""
from _lib import extract_keywords, extract_keyword_set_ci, STOPWORDS


def test_preserves_camelcase():
    kws = extract_keywords("BeanDeserializer fails on TextNode input")
    assert "BeanDeserializer" in kws
    assert "TextNode" in kws


def test_keeps_short_all_caps():
    kws = extract_keywords("NPE on DST boundary in JSON parsing")
    assert "NPE" in kws
    assert "DST" in kws
    assert "JSON" in kws  # not really an ALL-CAPS short — "JSON" is 4 chars (>=4)


def test_drops_short_lowercase():
    kws = extract_keywords("NPE on the foo bar baz")
    assert "on" not in kws
    assert "the" not in kws


def test_drops_stopwords():
    kws_lower = [k.lower() for k in extract_keywords("the quick brown fox jumps over")]
    for sw in STOPWORDS:
        assert sw not in kws_lower


def test_case_insensitive_dedup():
    kws = extract_keywords("BeanDeserializer beandeserializer BeanDeserializer")
    # Dedup by lowercase; preserved case is the first occurrence
    assert sum(1 for k in kws if k.lower() == "beandeserializer") == 1


def test_limit_caps_output():
    kws = extract_keywords(" ".join(f"Word{i}" for i in range(50)), limit=5)
    assert len(kws) == 5


def test_set_ci_is_lowercase():
    s = extract_keyword_set_ci("BeanDeserializer NPE TextNode")
    assert "beandeserializer" in s
    assert "BeanDeserializer" not in s  # all lowercase


def test_empty_input():
    assert extract_keywords("") == []
    assert extract_keyword_set_ci("") == set()
