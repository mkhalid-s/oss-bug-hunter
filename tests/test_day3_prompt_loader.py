"""P1-14 regression tests — runtime prompt loading from day3-novel-hunt-prompts.md."""
from conftest import day3_hunt


def test_both_angles_load():
    templates = day3_hunt._load_prompt_templates()
    assert set(templates.keys()) == {"code-quality", "edge-case"}


def test_code_quality_template_has_placeholder():
    t = day3_hunt.get_template("code-quality")
    assert "{SHORTLIST_FILES_BLOCK}" in t


def test_edge_case_template_has_placeholder():
    t = day3_hunt.get_template("edge-case")
    assert "{SHORTLIST_FILES_BLOCK}" in t


def test_code_quality_distinguishable_from_edge_case():
    cq = day3_hunt.get_template("code-quality")
    ec = day3_hunt.get_template("edge-case")
    assert cq != ec
    assert "code-quality angle" in cq
    assert "edge-case angle" in ec


def test_nested_yaml_fence_preserved():
    """The inner ```yaml example block must round-trip through the loader."""
    cq = day3_hunt.get_template("code-quality")
    # Example fence + key fields must be present
    assert "```yaml" in cq
    assert "findings:" in cq
    assert "reproducer_hint" in cq


def test_template_proxy_dict_interface():
    """TEMPLATES dict-style access works (backward compat)."""
    assert "code-quality" in day3_hunt.TEMPLATES
    assert "edge-case" in day3_hunt.TEMPLATES
    assert day3_hunt.TEMPLATES["code-quality"] == day3_hunt.get_template("code-quality")


def test_unknown_angle_raises():
    import pytest
    with pytest.raises(KeyError):
        day3_hunt.get_template("invalid-angle")


def test_placeholder_substitution_via_replace():
    """The caller uses .replace(), so we should test that flow too."""
    t = day3_hunt.get_template("code-quality")
    substituted = t.replace("{SHORTLIST_FILES_BLOCK}", "  - src/foo.java\n  - src/bar.java")
    assert "{SHORTLIST_FILES_BLOCK}" not in substituted
    assert "src/foo.java" in substituted
