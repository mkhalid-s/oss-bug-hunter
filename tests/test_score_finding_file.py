"""Tests for scripts/day2-backtest.py::_finding_file() — P0-1 helper."""
from conftest import day2_backtest


def test_strips_line_range():
    assert day2_backtest._finding_file({"location": "src/foo.java:12-45"}) == "src/foo.java"


def test_strips_single_line():
    assert day2_backtest._finding_file({"location": "src/foo.java:12"}) == "src/foo.java"


def test_no_line_part():
    assert day2_backtest._finding_file({"location": "src/foo.java"}) == "src/foo.java"


def test_missing_location_returns_empty():
    assert day2_backtest._finding_file({}) == ""
    assert day2_backtest._finding_file({"location": None}) == ""
    assert day2_backtest._finding_file({"location": ""}) == ""


def test_strips_whitespace():
    assert day2_backtest._finding_file({"location": "  src/foo.java:1-5  "}) == "src/foo.java"


def test_none_input_safe():
    # _finding_file gets called with finding dicts that may have unexpected types
    assert day2_backtest._finding_file(None) == ""
