"""Tests for scripts/day4-finalize.py::find_match() — self-consistency matcher.

This is the gate-critical Day 4 boundary: same-file + ≥2 keyword overlap.
"""
from conftest import day4_finalize as d4


def _finding(location: str, summary: str) -> dict:
    return {"location": location, "summary": summary}


def test_same_file_two_keyword_overlap_matches():
    target = _finding("src/foo/Bar.java:10-20", "NPE in BeanDeserializer.deserialize when input is null")
    candidates = [_finding("src/foo/Bar.java:50-60", "NPE in BeanDeserializer when input value is missing")]
    match = d4.find_match(target, candidates)
    assert match is not None
    assert match["location"].startswith("src/foo/Bar.java")


def test_same_file_one_keyword_overlap_does_not_match():
    # Threshold is >=2 overlap (best_score=1 initial means we need >1)
    target = _finding("src/foo/Bar.java:10", "NPE on null")
    candidates = [_finding("src/foo/Bar.java:50", "DST timezone parsing")]
    # No words above stopword threshold should overlap → no match
    assert d4.find_match(target, candidates) is None


def test_different_file_never_matches():
    target = _finding("src/foo/Bar.java:10", "NPE in BeanDeserializer deserialize input null")
    candidates = [_finding("src/foo/Baz.java:10", "NPE in BeanDeserializer deserialize input null")]
    # Different file path — should never match even if every keyword matches
    assert d4.find_match(target, candidates) is None


def test_empty_target_returns_none():
    assert d4.find_match({"location": "", "summary": ""}, [{"location": "x.java", "summary": "y"}]) is None


def test_empty_candidates_returns_none():
    assert d4.find_match({"location": "x.java:1", "summary": "foo bar baz"}, []) is None


def test_picks_highest_overlap():
    target = _finding("src/foo/Bar.java:10", "NullPointerException BeanDeserializer deserialize input value missing")
    candidates = [
        _finding("src/foo/Bar.java:50", "NullPointerException BeanDeserializer input"),  # 3 overlap
        _finding("src/foo/Bar.java:80", "NullPointerException BeanDeserializer deserialize input"),  # 4 overlap
    ]
    match = d4.find_match(target, candidates)
    assert match is candidates[1]


def test_extract_file_strips_line_range():
    assert d4.extract_file("src/foo.java:123-145") == "src/foo.java"
    assert d4.extract_file("src/foo.java:123") == "src/foo.java"
    assert d4.extract_file("src/foo.java") == "src/foo.java"
    assert d4.extract_file("") == ""
