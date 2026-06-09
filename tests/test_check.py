"""Tests for scripts/_check.py::_stub_findings() and helpers.

P1-4 fix (2026-05-19): stub detection is now sentinel-based, not text-sniffing.
The explicit sentinel line is `STUB_SENTINEL` (defined in _check.py). The
human deletes it on paste; legacy "Paste the..." markers are still
recognized for backward compatibility with files predating the fix.
"""
import tempfile
import textwrap
from pathlib import Path

import _check


def _write(content: str) -> Path:
    f = Path(tempfile.NamedTemporaryFile(suffix=".yaml", delete=False).name)
    f.write_text(content)
    return f


# ---- new sentinel-based detection ----

def test_stub_findings_detects_sentinel():
    """P1-4: sentinel line present → stub."""
    p = _write(textwrap.dedent(f"""\
        {_check.STUB_SENTINEL}
        # Other boilerplate
        findings: []
    """))
    assert _check._stub_findings(p) is True


def test_sentinel_removed_with_empty_findings_is_NOT_stub():
    """P1-4 fix: this used to be a known false positive — the agent's
    legitimate `findings: []` answer was reported as 'stub'. After P1-4,
    removing the sentinel line signals 'this is the real answer'."""
    p = _write("# Other boilerplate stayed, sentinel deleted\n\nfindings: []\n")
    assert _check._stub_findings(p) is False


def test_sentinel_removed_with_real_findings_is_NOT_stub():
    p = _write(textwrap.dedent("""\
        # Auto-generated via claude_driver.

        findings:
          - summary: real bug
            location: src/foo.java:1
    """))
    assert _check._stub_findings(p) is False


def test_missing_file_treated_as_stub():
    assert _check._stub_findings(Path("/nonexistent/path/findings.yaml")) is True


# ---- backward compatibility with pre-P1-4 stub files ----

def test_backward_compat_legacy_paste_marker_with_empty_list_is_stub():
    """Files written by pre-P1-4 pipeline still detect as stubs."""
    p = _write(textwrap.dedent("""\
        # Paste the agent's `findings:` YAML block from its final message here, then save.

        findings: []
    """))
    assert _check._stub_findings(p) is True


def test_backward_compat_legacy_yaml_marker_is_stub():
    p = _write("# Paste the YAML block from the agent's final message here\n\nfindings: []\n")
    assert _check._stub_findings(p) is True


def test_legacy_marker_with_real_findings_is_NOT_stub():
    """A pre-P1-4 stub that received a populated `findings:` list (and the
    marker comment was kept for whatever reason) — `findings: []` is NOT
    there anymore, so the legacy branch returns False. Populated wins."""
    p = _write(textwrap.dedent("""\
        # Paste the agent's `findings:` YAML block here

        findings:
          - summary: real bug
            location: src/foo.java:1
    """))
    assert _check._stub_findings(p) is False
