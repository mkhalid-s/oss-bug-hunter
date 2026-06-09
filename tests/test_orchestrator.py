"""Pins the CONVERGED self-correcting orchestrator (pipeline.orchestrate_finding /
orchestrate). The reproduce→fix→retry loop is the single engine
run_harness.orchestrate (mocked here); the Java builders + engine + Docker are
mocked — this tests the control flow: scaffold lookup, build-if-missing (Java
only), language-aware delegation, and the outcome mapping."""
from __future__ import annotations

import types

import pipeline as pl
import run_harness as rh
import llm_repro_provider as lrp
import llm_fix_builder as lfb


def _setup(cell, fid="ec-1", lang="java", repro=True, patch=True):
    v = cell / "hunt" / "validation"
    v.mkdir(parents=True, exist_ok=True)
    (v / f"{fid}.yaml").write_text(f"finding_id: {fid}\nlanguage: {lang}\ntarget: t\n")
    ext = {"java": ".java", "python": ".py", "go": ".go"}[lang]
    rd = cell / "hunt" / "repros"; rd.mkdir(parents=True, exist_ok=True)
    pd = cell / "hunt" / "patches"; pd.mkdir(parents=True, exist_ok=True)
    if repro:
        (rd / f"{fid}{ext}").write_text("x")
    if patch:
        (pd / f"{fid}.patch").write_text("x")


def _mock_engine(monkeypatch, status, attempts=1, capture=None):
    def fake(*a, **k):
        if capture is not None:
            capture["kw"] = k
        return types.SimpleNamespace(status=status, attempts=attempts, detail="d")
    monkeypatch.setattr(rh, "orchestrate", fake)


def test_fixed_maps_from_validated(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    cap = {}
    _mock_engine(monkeypatch, "validated", attempts=1, capture=cap)
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "fixed" and r["attempts"] == 1 and r["validated"] is True
    assert cap["kw"]["lang"] == "java"                    # language-aware delegation


def test_does_not_reproduce(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    _mock_engine(monkeypatch, "not-reproduced")
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "does-not-reproduce" and r["validated"] is True


def test_fix_failed_after_retries(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    _mock_engine(monkeypatch, "fix-failed", attempts=3)
    r = pl.orchestrate_finding("ec-1", max_fix_attempts=2)
    assert r["outcome"] == "fix-failed-after-retries" and r["attempts"] == 3
    # fix-failed IS a conclusive verdict (reproduced + patch didn't work), so it
    # counts as validated — distinct from an inconclusive environment failure.
    assert r["validated"] is True


def test_inconclusive_not_validated(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    _mock_engine(monkeypatch, "inconclusive")
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "inconclusive" and r["validated"] is False


def test_builds_reproducer_when_missing_java(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, repro=False)   # java, no .java yet
    monkeypatch.setattr(pl, "CELL", cell)
    built = []

    def fake_repro(fid, **kw):
        built.append(fid)
        (cell / "hunt" / "repros" / f"{fid}.java").write_text("x")   # produce it
        return {"ok": True, "finding_id": fid}
    monkeypatch.setattr(pl, "run_repro_subagent", fake_repro)
    _mock_engine(monkeypatch, "validated")
    r = pl.orchestrate_finding("ec-1")
    assert built == ["ec-1"]
    assert r["steps"][0]["step"] == "build-reproducer" and r["outcome"] == "fixed"


def test_no_reproducer_built_stops(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, repro=False)
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl, "run_repro_subagent", lambda fid, **kw: {"ok": False, "error": "no java"})
    assert pl.orchestrate_finding("ec-1")["outcome"] == "no-reproducer-built"


def test_non_java_requires_existing_reproducer(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="python", repro=False)
    monkeypatch.setattr(pl, "CELL", cell)
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "no-reproducer-built" and "python" in (r.get("note") or "")


def test_python_finding_delegates_with_lang(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="python")
    monkeypatch.setattr(pl, "CELL", cell)
    cap = {}
    _mock_engine(monkeypatch, "validated", capture=cap)
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "fixed" and r["lang"] == "python"
    assert cap["kw"]["lang"] == "python" and cap["kw"]["fix_provider"] is not None  # #55: python now has a fix-builder retry provider


def test_builders_use_opus_high(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, repro=False, patch=False)  # java, build both
    monkeypatch.setattr(pl, "CELL", cell)
    seen = {}

    def fake_repro(fid, **kw):
        (cell / "hunt" / "repros" / f"{fid}.java").write_text("x"); seen["repro"] = kw
        return {"ok": True}

    def fake_fix(fid, feedback=None, **kw):
        (cell / "hunt" / "patches" / f"{fid}.patch").write_text("x"); seen["fix"] = kw
        return {"ok": True}
    monkeypatch.setattr(pl, "run_repro_subagent", fake_repro)
    monkeypatch.setattr(pl, "run_fix_subagent", fake_fix)
    _mock_engine(monkeypatch, "validated")
    pl.orchestrate_finding("ec-1")
    assert seen["repro"]["model"] == "opus" and seen["repro"]["effort"] == "high"
    assert seen["fix"]["model"] == "opus" and seen["fix"]["effort"] == "high"


def test_model_override(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, repro=False, patch=False)
    monkeypatch.setattr(pl, "CELL", cell)
    seen = {}

    def fake_repro(fid, **kw):
        (cell / "hunt" / "repros" / f"{fid}.java").write_text("x"); seen["repro"] = kw
        return {"ok": True}

    def fake_fix(fid, feedback=None, **kw):
        (cell / "hunt" / "patches" / f"{fid}.patch").write_text("x"); seen["fix"] = kw
        return {"ok": True}
    monkeypatch.setattr(pl, "run_repro_subagent", fake_repro)
    monkeypatch.setattr(pl, "run_fix_subagent", fake_fix)
    _mock_engine(monkeypatch, "validated")
    pl.orchestrate_finding("ec-1", model="sonnet", effort="medium")
    assert seen["repro"]["model"] == "sonnet" and seen["repro"]["effort"] == "medium"
    assert seen["fix"]["model"] == "sonnet" and seen["fix"]["effort"] == "medium"


def test_orchestrate_tally(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; v = cell / "hunt" / "validation"; v.mkdir(parents=True)
    (v / "ec-1.yaml").write_text("finding_id: ec-1\n")
    (v / "cq-1.yaml").write_text("finding_id: cq-1\n")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl, "orchestrate_finding",
                        lambda fid, *a, **k: {"finding_id": fid,
                                              "outcome": "fixed" if fid == "ec-1" else "does-not-reproduce"})
    res = pl.orchestrate()
    assert res["ok"] and res["total"] == 2 and res["fixed"] == 1
    assert res["outcomes"] == {"fixed": 1, "does-not-reproduce": 1}


def test_orchestrate_no_scaffolds(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    assert pl.orchestrate()["ok"] is False


# ---- #54: verify_finding (the Verify stage — AI proposes a reproducer, engine disposes) ----
def _verdict(name):
    return rh.TestVerdict(getattr(rh.Outcome, name), 1, 1 if name == "FAILED" else 0,
                          0, 0, f"oc={name}")


def _gate_status(cell, fid="ec-1"):
    import yaml
    s = yaml.safe_load((cell / "hunt" / "validation" / f"{fid}.yaml").read_text())
    return s["gates"]["reproducer"]["status"]


def test_verify_reproduced(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="python")          # repro exists
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _verdict("FAILED"))
    r = pl.verify_finding("ec-1")
    assert r["outcome"] == "reproduced" and _gate_status(cell) == "pass"


def test_verify_does_not_reproduce(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="python")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _verdict("PASSED"))
    r = pl.verify_finding("ec-1")
    assert r["outcome"] == "does-not-reproduce" and _gate_status(cell) == "fail"


def test_verify_builds_reproducer_for_nonjava(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="go", repro=False)  # no PoC yet
    monkeypatch.setattr(pl, "CELL", cell)
    built = []

    def fake_build(scaffold, repro_path, **kw):
        built.append(scaffold["finding_id"])
        from pathlib import Path
        Path(repro_path).write_text("package x\nfunc TestRepro(t *testing.T){}\n")
        return repro_path
    monkeypatch.setattr(lrp, "build_repro", fake_build)
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _verdict("FAILED"))
    r = pl.verify_finding("ec-1")
    assert built == ["ec-1"] and r["outcome"] == "reproduced"
    assert r["steps"][0]["step"] == "build-reproducer"


def test_verify_no_reproducer_built(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="python", repro=False)
    monkeypatch.setattr(pl, "CELL", cell)
    r = pl.verify_finding("ec-1", build=False)                        # don't build
    assert r["outcome"] == "no-reproducer-built" and _gate_status(cell) == "not-attempted"


# ---- #55: orchestrate builds a non-Java fix when the patch is missing ----
def test_nonjava_builds_fix_when_missing(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _setup(cell, lang="go", patch=False)  # repro exists, no patch
    monkeypatch.setattr(pl, "CELL", cell)
    built = []

    def fake_fix(scaffold, repro_src, patch_path, **kw):
        built.append(scaffold["finding_id"])
        from pathlib import Path
        Path(patch_path).write_text("diff --git a/x.go b/x.go\n")
        return patch_path
    monkeypatch.setattr(lfb, "build_fix", fake_fix)
    _mock_engine(monkeypatch, "validated")                            # non-AI engine mocked
    r = pl.orchestrate_finding("ec-1")
    assert built == ["ec-1"] and r["outcome"] == "fixed"
    assert any(s.get("step") == "build-fix#1" for s in r["steps"])
