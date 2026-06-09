"""Tests for scripts/day2-build-dataset.py::score()."""
from conftest import day2_build


def _candidate(**overrides) -> dict:
    base = {
        "issue_num": 1, "title": "t", "url": "u",
        "fix_commit": "abc", "fix_subject": "fix",
        "parent_commit": "def",
        "files_changed": 3, "lines_added": 30, "lines_removed": 10,
        "files_touched": ["src/main/java/x.java"],
        "correctness": False, "feature": False, "security": False,
        "has_code_fence": False,
    }
    base.update(overrides)
    return base


def test_correctness_keyword_adds_3():
    s = day2_build.score(_candidate(correctness=True))
    s_no = day2_build.score(_candidate(correctness=False))
    assert s - s_no == 3.0


def test_feature_keyword_subtracts_5():
    s = day2_build.score(_candidate(feature=True))
    s_no = day2_build.score(_candidate(feature=False))
    assert s - s_no == -5.0


def test_security_keyword_subtracts_2():
    s = day2_build.score(_candidate(security=True))
    s_no = day2_build.score(_candidate(security=False))
    assert s - s_no == -2.0


def test_files_in_sweet_spot_1_to_5_adds_2():
    s_3files = day2_build.score(_candidate(files_changed=3))
    s_25files = day2_build.score(_candidate(files_changed=25, lines_added=30, lines_removed=10))
    # 1-5 files: +2; >20 files: -5
    # also the 3-file case is in lines sweet-spot (5-100); the 25-file case still is
    assert s_3files - s_25files == 2.0 - (-5.0)


def test_huge_commit_penalized():
    s = day2_build.score(_candidate(files_changed=30, lines_added=600, lines_removed=400))
    # files>20: -5; lines>500: -3
    assert s <= -8.0


def test_jsontype_or_deser_bonus():
    s_in_deser = day2_build.score(_candidate(
        files_touched=["src/main/java/com/fasterxml/jackson/databind/deser/X.java"]))
    s_outside = day2_build.score(_candidate(
        files_touched=["src/main/java/com/fasterxml/jackson/databind/util/X.java"]))
    # +1.5 bonus when any file is under deser/ or jsontype/
    assert s_in_deser - s_outside == 1.5
