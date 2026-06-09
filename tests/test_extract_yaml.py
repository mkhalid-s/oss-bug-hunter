"""Tests for tool/claude_driver.py::extract_yaml_block()."""
from claude_driver import extract_yaml_block, is_findings_empty


def test_yaml_fenced_block():
    text = "Some prose\n\n```yaml\nfindings:\n  - summary: bug1\n```\nMore prose."
    result = extract_yaml_block(text)
    assert result is not None
    assert "findings:" in result
    assert "bug1" in result


def test_yaml_block_with_two_fenced_blocks_picks_last():
    text = (
        "```yaml\nfindings:\n  - summary: first\n```\n"
        "stuff\n"
        "```yaml\nfindings:\n  - summary: second\n```\n"
    )
    result = extract_yaml_block(text)
    assert "second" in result
    assert "first" not in result


def test_generic_fenced_block_without_yaml_lang():
    text = "Text\n```\nfindings:\n  - summary: from-bare-fence\n```\nEnd"
    result = extract_yaml_block(text)
    assert result is not None
    assert "from-bare-fence" in result


def test_no_yaml_block_returns_none():
    text = "This message has no findings block at all."
    assert extract_yaml_block(text) is None


def test_empty_findings_detected():
    yaml = "findings: []"
    assert is_findings_empty(yaml) is True


def test_non_empty_findings_not_detected_as_empty():
    yaml = "findings:\n  - summary: real bug"
    assert is_findings_empty(yaml) is False


def test_alternate_top_key_via_yaml_fence():
    """Non-default top_key still routes through Patterns 1 and 2 only."""
    text = "```yaml\nmatched_rank: 1\nlabels:\n  - index: 0\n    label: matches_known\n```"
    result = extract_yaml_block(text, top_key="matched_rank")
    assert result is not None
    assert "matched_rank: 1" in result


def test_alternate_top_key():
    text = "```yaml\nmatched_rank: 1\nlabels: []\n```"
    result = extract_yaml_block(text, top_key="matched_rank")
    assert result is not None
    assert "matched_rank: 1" in result


# ---- P1-3 regression tests (Pattern-3 removed) ----

def test_unfenced_yaml_no_longer_extracted():
    """P1-3 fix: bare-prefix fallback removed — agent MUST fence its output."""
    text = "Here are the findings:\n\nfindings:\n  - summary: foo\n    location: x.java:1\n"
    assert extract_yaml_block(text) is None


def test_yaml_with_markdown_comment_in_evidence_not_truncated():
    """P1-3 regression test: previously Pattern-3 cut at `\\n# ` (heading)
    even when the # was a YAML comment inside an evidence body."""
    text = (
        "Here's what I found:\n\n"
        "```yaml\n"
        "findings:\n"
        "  - summary: \"NPE in deserializer\"\n"
        "    location: \"src/Foo.java:42\"\n"
        "    evidence: |\n"
        "      # NOTE: this comment used to break Pattern-3 extraction\n"
        "      foo.bar();  # also this inline comment\n"
        "      baz();\n"
        "    reproducer_hint: \"ObjectMapper.readValue(...)\"\n"
        "```\n"
    )
    result = extract_yaml_block(text)
    assert result is not None
    # Full evidence body must round-trip; the comment must NOT have truncated the block
    assert "this comment used to break" in result
    assert "reproducer_hint" in result


def test_yaml_with_bold_markdown_in_evidence_not_truncated():
    """P1-3 regression test: previously Pattern-3 cut at `\\n**A` (bold)
    even when ** appeared in a YAML string."""
    text = (
        "```yaml\n"
        "findings:\n"
        "  - summary: \"bug\"\n"
        "    evidence: |\n"
        "      The **Builder** path is buggy.\n"
        "      **Specifically**, the wrapped update branch.\n"
        "    location: \"x.java:1\"\n"
        "```\n"
    )
    result = extract_yaml_block(text)
    assert result is not None
    assert "**Builder**" in result
    assert "location" in result
