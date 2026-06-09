"""Pins the fix-builder (#4): build_fix_prompt, extract_diff_block,
fix_status_from_exit, set_fix_gate, and pipeline.run_fix_batch + CLI."""
from __future__ import annotations

import yaml

import pipeline as pl
from conftest import day3_hunt as d3


# ---- pure functions (day3-hunt) -------------------------------------------

def test_build_fix_prompt_embeds_repro_and_substitutes():
    sc = {"finding_id": "ec-1", "summary": "null into TreeSet",
          "location": "Foo.java:464", "type": "empty-collection",
          "evidence": "add(value)", "reproducer_hint": "readValue(...)"}
    p = d3.build_fix_prompt(sc, repro_src="class Repro_ec_1 { @Test void t(){} }")
    assert "null into TreeSet" in p and "Foo.java:464" in p
    assert "com.fasterxml.jackson.databind.repro.Repro_ec_1" in p   # FQCN token
    assert "class Repro_ec_1" in p                                  # repro embedded
    assert "{{" not in p and "}}" not in p


def test_build_fix_prompt_handles_missing_repro():
    p = d3.build_fix_prompt({"finding_id": "cq-1"}, repro_src=None)
    assert "no reproducer" in p.lower()


def test_extract_diff_block():
    assert "diff --git" in d3.extract_diff_block("x\n```diff\ndiff --git a/F b/F\n-a\n+b\n```")
    assert d3.extract_diff_block("```patch\n--- a/F\n+++ b/F\n```") is not None
    # bare fence that looks like a diff
    assert d3.extract_diff_block("```\ndiff --git a/F b/F\n```") is not None
    # prose, no fence -> None
    assert d3.extract_diff_block("just apply the obvious fix") is None


def test_fix_status_from_exit_inversion():
    # Fix PASSES when the reproducer now passes (exit 0) — inverse of reproducer.
    assert d3.fix_status_from_exit(0)[0] == "pass"
    assert d3.fix_status_from_exit(1)[0] == "fail"          # still red
    assert d3.fix_status_from_exit(3)[0] == "fail"          # patch didn't apply
    assert d3.fix_status_from_exit(4)[0] == "fail"          # compile error
    assert d3.fix_status_from_exit(2)[0] == "not-attempted"  # docker
    for c in (0, 1, 2, 3, 4, 9):
        assert d3.fix_status_from_exit(c)[1]


def _scaffold_text():
    return d3.VALIDATION_SCAFFOLD_TEMPLATE.format(
        finding_id="ec-1", angle="edge-case",
        summary_yaml='"S"', location_yaml='"L"', type_yaml='"x"',
        evidence_indented="  ev", reproducer_indented="  hint",
        osv_block="    []", github_block="    []",
    )


def test_set_fix_gate_sets_only_fix_block():
    raw = _scaffold_text()
    out = d3.set_fix_gate(raw, "pass", "cell-1/hunt/patches/ec-1.patch", "green after fix")
    doc = yaml.safe_load(out)
    fg = doc["gates"]["fix_passes_tests"]
    assert fg["status"] == "pass"
    assert fg["patch_path"] == "cell-1/hunt/patches/ec-1.patch"
    assert "green after fix" in fg["notes"]
    # reproducer + other gates untouched
    assert doc["gates"]["reproducer"]["status"] == ""
    assert doc["gates"]["cwe"]["cwe"] == ""
    assert doc["final_status"] == "pending"
    assert "# === HUMAN FILLS BELOW ===" in out  # comments preserved


def test_set_fix_gate_raises_when_block_absent():
    import pytest
    with pytest.raises(ValueError):
        d3.set_fix_gate("gates:\n  reproducer:\n    status: \"\"\n", "pass", "p", "n")


# ---- pipeline batch + CLI --------------------------------------------------

def _setup_finding(cell, fid):
    v = cell / "hunt" / "validation"; v.mkdir(parents=True, exist_ok=True)
    (v / f"{fid}.yaml").write_text(
        f"finding_id: {fid}\nsummary: S\nlocation: A.java:1\ntype: NPE\n"
        f"evidence: |\n  ev\nreproducer_hint: |\n  hint\n")
    r = cell / "hunt" / "repros"; r.mkdir(parents=True, exist_ok=True)
    (r / f"{fid}.java").write_text("class Repro {}")  # reproducer present


def test_run_fix_batch_writes_patches(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    _setup_finding(cell, "ec-1")
    _setup_finding(cell, "cq-1")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude",
                        lambda prompt, **kw: {"returncode": 0, "stderr": "", "elapsed_s": 0.1,
                                              "stdout": "```diff\ndiff --git a/A b/A\n-x\n+y\n```"})
    res = pl.run_fix_batch()  # None => all findings with repro but no patch
    assert res["ok"] and res["total"] == 2 and res["succeeded"] == 2
    assert (cell / "hunt" / "patches" / "ec-1.patch").exists()
    # once a patch exists it's no longer pending
    assert pl.list_fix_finding_ids() == []


def test_run_fix_batch_none_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    res = pl.run_fix_batch()
    assert res["ok"] is False and "awaiting a fix" in res["error"]


def test_cli_routes_to_run_fix_batch(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(pl, "run_fix_batch",
                        lambda ids, par: seen.update(ids=ids, par=par) or {"ok": True})
    assert pl._cli(["run-fix-batch", "--ids", "ec-1", "--parallel", "3"]) == 0
    assert seen == {"ids": ["ec-1"], "par": 3}
