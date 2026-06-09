"""Tests for scripts/day2-build-dataset.py::classify()."""
from conftest import day2_build


def test_correctness_keyword_npe():
    flags = day2_build.classify("NullPointerException in foo deserializer", "")
    assert flags["correctness"] is True
    assert flags["feature"] is False
    assert flags["security"] is False


def test_correctness_keyword_regression():
    flags = day2_build.classify("Regression in 2.21: parser breaks on empty input", "")
    assert flags["correctness"] is True


def test_feature_request_keywords():
    flags = day2_build.classify("Add support for Records in BeanDeserializer", "")
    assert flags["feature"] is True
    # "Add support" + "Records" — should NOT also flag as correctness
    assert flags["correctness"] is False


def test_security_keyword_cve():
    flags = day2_build.classify("CVE-2024-1234 RCE via gadget chain", "")
    assert flags["security"] is True


def test_has_code_fence():
    flags = day2_build.classify("Title", "Body with ```java\nfoo()\n``` block")
    assert flags["has_code_fence"] is True


def test_no_keywords_all_false():
    flags = day2_build.classify("Polite documentation request", "")
    assert flags["correctness"] is False
    assert flags["feature"] is False
    assert flags["security"] is False
