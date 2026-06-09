"""Pins the WS3 reproducer-builder logic in day3-hunt.py: id->class/fqcn,
the run-repro.sh exit-code inversion, java extraction, prompt token
substitution, and the surgical reproducer-gate text edit."""
from __future__ import annotations

import yaml

from conftest import day3_hunt as d3


def test_class_name_and_fqcn():
    assert d3.repro_class_name("cq-1") == "Repro_cq_1"
    assert d3.repro_class_name("ec-12") == "Repro_ec_12"
    assert d3.repro_fqcn("cq-1") == "com.fasterxml.jackson.databind.repro.Repro_cq_1"


def test_status_from_exit_inversion():
    # The crux: a reproducer "passes" when the JUnit test FAILS on buggy HEAD.
    assert d3.repro_status_from_exit(1)[0] == "pass"          # test failed -> reproduces
    assert d3.repro_status_from_exit(0)[0] == "fail"          # test passed -> no repro
    assert d3.repro_status_from_exit(2)[0] == "not-attempted"  # docker/mvn error
    assert d3.repro_status_from_exit(3)[0] == "not-attempted"  # bad args
    assert d3.repro_status_from_exit(99)[0] == "not-attempted"
    # every branch returns a non-empty note
    for code in (0, 1, 2, 3, 99):
        assert d3.repro_status_from_exit(code)[1]


def test_extract_java_block():
    txt = "prose\n```java\npackage com.x;\nclass Repro_cq_1 {}\n```\nafter"
    assert "class Repro_cq_1" in d3.extract_java_block(txt)
    # bare fence that looks like java still works
    assert d3.extract_java_block("```\nclass Foo {}\n```") is not None
    # no fence -> None (never guess at unfenced output)
    assert d3.extract_java_block("class Foo {}") is None
    # prefers the LAST java block
    two = "```java\nclass A {}\n```\n```java\nclass B {}\n```"
    assert "class B" in d3.extract_java_block(two)


def test_build_repro_prompt_substitutes_all_tokens():
    sc = {
        "finding_id": "cq-1",
        "summary": "NPE in foo()",
        "location": "A.java:10-12",
        "type": "NPE",
        "evidence": "if (x == null)\n  bar();",
        "reproducer_hint": "call foo(null)",
    }
    p = d3.build_repro_prompt(sc)
    assert "Repro_cq_1" in p
    assert "NPE in foo()" in p and "A.java:10-12" in p
    assert "call foo(null)" in p
    # evidence indented and present
    assert "if (x == null)" in p
    # no leftover placeholders
    assert "{{" not in p and "}}" not in p


def _scaffold_text(fid="cq-1"):
    return d3.VALIDATION_SCAFFOLD_TEMPLATE.format(
        finding_id=fid, angle="code-quality",
        summary_yaml='"S"', location_yaml='"L"', type_yaml='"NPE"',
        evidence_indented="  ev", reproducer_indented="  hint",
        osv_block="    []", github_block="    []",
    )


def test_set_reproducer_gate_sets_values_and_preserves_rest():
    raw = _scaffold_text()
    out = d3.set_reproducer_gate(raw, "pass", "cell-1/hunt/repros/cq-1.java",
                                 'JUnit FAILED [boom "quoted"]')
    doc = yaml.safe_load(out)  # still valid YAML
    rg = doc["gates"]["reproducer"]
    assert rg["status"] == "pass"
    assert rg["path"] == "cell-1/hunt/repros/cq-1.java"
    assert "JUnit FAILED" in rg["notes"]
    # other gates untouched
    assert doc["gates"]["dedup"]["is_duplicate"] is None
    assert doc["gates"]["cwe"]["cwe"] == ""
    assert doc["final_status"] == "pending"
    # comments preserved (the surgical edit didn't nuke the template guidance)
    assert "# === HUMAN FILLS BELOW ===" in out
    assert "pass | fail | not-attempted" in out


def test_set_reproducer_gate_raises_when_block_absent():
    import pytest
    with pytest.raises(ValueError):
        d3.set_reproducer_gate("gates:\n  dedup:\n    is_duplicate: null\n",
                               "pass", "x.java", "note")
