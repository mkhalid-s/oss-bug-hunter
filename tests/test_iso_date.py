"""P1-6 regression tests — find_fix_commit's ISO date comparison.

We can't exercise the full find_fix_commit (needs git+target dir), but we
verify the date-parsing pattern it uses handles `Z` vs `+00:00` equivalence.
"""
import datetime as dt


def _parse(s: str) -> dt.datetime:
    """Mirror the parse used inside find_fix_commit."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_z_and_explicit_offset_are_equal():
    a = _parse("2024-05-01T12:00:00Z")
    b = _parse("2024-05-01T12:00:00+00:00")
    assert a == b
    assert not (a < b)
    assert not (b < a)


def test_lexical_compare_would_be_wrong():
    """Demonstrates why lexical compare on raw ISO strings is wrong:
    `+00:00` sorts before `Z` even though they denote the same instant."""
    a_str = "2024-05-01T12:00:00Z"
    b_str = "2024-05-01T12:00:00+00:00"
    # Lexical (broken) — `+` (0x2B) < `Z` (0x5A)
    assert b_str < a_str
    # Datetime-parsed (correct)
    assert _parse(b_str) == _parse(a_str)


def test_strictly_earlier_commit_drops():
    issue_created = _parse("2024-05-01T12:00:00Z")
    commit_at = _parse("2024-05-01T11:59:59+00:00")
    assert commit_at < issue_created


def test_strictly_later_commit_kept():
    issue_created = _parse("2024-05-01T12:00:00Z")
    commit_at = _parse("2024-05-01T12:00:01+00:00")
    assert not (commit_at < issue_created)
