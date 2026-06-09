"""Pins #5: advisory dedup/CWE auto-suggestion (deterministic, non-AI).
Critical safety properties: never asserts is_duplicate, never sets final_status,
fills only blanks, and `references` round-trips as a real YAML list."""
from __future__ import annotations

import yaml

import pipeline as pl
from conftest import day3_hunt as d3


def test_suggest_cwe_known_and_unknown():
    assert d3.suggest_cwe("NPE")[0] == "CWE-476"          # case-insensitive
    assert d3.suggest_cwe("off-by-one")[0] == "CWE-193"
    assert d3.suggest_cwe("race")[0] == "CWE-362"
    assert d3.suggest_cwe("integer-overflow")[0] == "CWE-190"
    assert d3.suggest_cwe("empty-collection")[0] == "CWE-476"
    assert d3.suggest_cwe("polymorphic") == ("", "")      # no confident map
    assert d3.suggest_cwe(None) == ("", "")
    # mapped types carry an advisory note
    assert "Auto-suggested" in d3.suggest_cwe("npe")[1]


def test_suggest_dedup_with_and_without_matches():
    sc = {"dedup_auto": {"osv_matches": [{"id": "OSV-1"}],
                         "github_matches": [{"url": "https://github.com/x/5"}]}}
    flow, note = d3.suggest_dedup(sc)
    assert flow == '["OSV-1", "https://github.com/x/5"]'
    assert "1 OSV + 1 GitHub" in note
    flow2, note2 = d3.suggest_dedup({"dedup_auto": {}})
    assert flow2 is None and "likely not a duplicate" in note2


def test_set_dedup_suggestion_writes_a_real_list():
    raw = ("gates:\n  dedup:\n    is_duplicate: null    # true | false\n"
           "    references: []        # OSV ids\n    notes: \"\"\n  cwe:\n    cwe: \"\"\n")
    out = d3.set_dedup_suggestion(raw, '["OSV-1", "u"]', "2 candidates")
    doc = yaml.safe_load(out)
    assert doc["gates"]["dedup"]["references"] == ["OSV-1", "u"]   # a LIST, not a string
    assert doc["gates"]["dedup"]["notes"] == "2 candidates"
    assert doc["gates"]["dedup"]["is_duplicate"] is None           # never asserted
    assert "# true | false" in out                                # comment preserved


def _make_scaffold(d3mod, tmp, fid="ec-1", ftype="empty-collection", with_matches=True, cwe_prefilled=""):
    osv = '    - id: "OSV-2021-1"\n      summary: "x"\n      score: 3' if with_matches else "    []"
    gh = ('    - number: 5\n      state: "closed"\n      title: "t"\n'
          '      url: "https://github.com/x/5"') if with_matches else "    []"
    text = d3mod.VALIDATION_SCAFFOLD_TEMPLATE.format(
        finding_id=fid, angle="edge-case", summary_yaml='"S"', location_yaml='"L"',
        type_yaml=f'"{ftype}"', evidence_indented="  ev", reproducer_indented="  hint",
        osv_block=osv, github_block=gh)
    if cwe_prefilled:
        text = text.replace('cwe: ""', f'cwe: "{cwe_prefilled}"')
    vdir = tmp / "validation"; vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{fid}.yaml").write_text(text)
    return vdir


def test_apply_gate_suggestions_fills_and_preserves_safety(tmp_path, monkeypatch):
    vdir = _make_scaffold(d3, tmp_path)
    monkeypatch.setattr(d3, "VALIDATION_DIR", vdir)
    r = d3.apply_gate_suggestions()
    assert r["ok"] and r["updated"] == 1
    doc = yaml.safe_load((vdir / "ec-1.yaml").read_text())
    g = doc["gates"]
    assert g["cwe"]["cwe"] == "CWE-476" and g["cwe"]["cvss"] == "N/A"
    assert g["dedup"]["references"] == ["OSV-2021-1", "https://github.com/x/5"]
    # SAFETY: never auto-decides the gate-relevant fields
    assert g["dedup"]["is_duplicate"] is None
    assert doc["final_status"] == "pending"


def test_apply_gate_suggestions_fill_only_if_blank(tmp_path, monkeypatch):
    vdir = _make_scaffold(d3, tmp_path, cwe_prefilled="CWE-999")
    monkeypatch.setattr(d3, "VALIDATION_DIR", vdir)
    d3.apply_gate_suggestions()
    doc = yaml.safe_load((vdir / "ec-1.yaml").read_text())
    assert doc["gates"]["cwe"]["cwe"] == "CWE-999"   # human value not overwritten


def test_apply_gate_suggestions_no_scaffolds(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "VALIDATION_DIR", tmp_path / "nope")
    assert d3.apply_gate_suggestions()["ok"] is False


def test_pipeline_suggest_gates_delegates(monkeypatch):
    monkeypatch.setattr(pl, "_load_day3_hunt",
                        lambda: type("N", (), {"apply_gate_suggestions": staticmethod(lambda: {"ok": True, "updated": 3})}))
    assert pl.suggest_gates() == {"ok": True, "updated": 3}
