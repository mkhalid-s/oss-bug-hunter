"""Pins the R1-R5 fixes from the 2026-06-04 deep review.

R1 gate-editor multi-word/escaped-value corruption; R2 run-*.sh no-summary abort;
R3 finding_id path traversal; R4 run-fix.sh patch containment; R5 orchestrator
inconclusive-vs-success."""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest
import yaml

import pipeline as pl
from conftest import day3_hunt as d3

ROOT = Path(__file__).resolve().parent.parent


# ---- R1: _set_gate_block no longer corrupts bare / multi-word / quoted values ----

def test_r1_bare_multiword_value_not_duplicated():
    # A post-safe_dump scaffold: notes is a BARE multi-word scalar (no quotes).
    raw = ("gates:\n  reproducer:\n    status: not-attempted\n"
           "    path: cell-1/hunt/repros/ec-1.java\n"
           "    notes: Docker error here today\n  dedup:\n    is_duplicate: null\n")
    out = d3.set_reproducer_gate(raw, "pass", "p.java", "new note")
    rg = yaml.safe_load(out)["gates"]["reproducer"]      # must stay valid YAML
    assert rg["status"] == "pass"
    assert rg["notes"] == "new note"                     # not "new note ... here today"
    assert "here today" not in out                       # old tail gone


def test_r1_idempotent_reapply_em_dash_note():
    raw = ('gates:\n  reproducer:\n    status: ""\n    path: ""\n    notes: ""\n'
           "  dedup:\n    is_duplicate: null\n")
    note = "Docker/mvn invocation error (run-repro.sh exit 2) — sandbox unavailable? [ERROR: x]"
    t = raw
    for _ in range(4):                                   # the ec-1 corruption was ~4 re-applies
        t = d3.set_reproducer_gate(t, "not-attempted", "p.java", note)
    assert yaml.safe_load(t)["gates"]["reproducer"]["notes"] == note   # exact, never doubled


def test_r1_preserves_trailing_comment():
    raw = ('gates:\n  cwe:\n    cwe: ""               # CWE-XXX\n'
           '    cvss: ""\n    notes: ""\n  dedup:\n    is_duplicate: null\n')
    out = d3.set_cwe_gate(raw, "CWE-476", "N/A", "n")
    assert "# CWE-XXX" in out                            # guidance comment kept
    assert yaml.safe_load(out)["gates"]["cwe"]["cwe"] == "CWE-476"


# ---- R2 + R8: shared Surefire parser is abort-safe and counts Skipped ----

def _surefire(output: str):
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'source "{ROOT}/scripts/_repro_decide.sh"; surefire_counts "$1"', "_", output],
        capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def test_r2_no_summary_does_not_abort():
    rc, out = _surefire("BUILD FAILURE\ncannot find symbol\n")   # no "Tests run:" line
    assert rc == 0                                                # R2: did NOT abort under set -e
    assert out == "0 0 0 0"


def test_r2_counts_and_aggregate_line():
    assert _surefire("Tests run: 3, Failures: 1, Errors: 2, Skipped: 0")[1] == "3 1 2 0"
    # picks the LAST (aggregate Results:) line, not a per-class line
    multi = ("Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"
             "Results:\nTests run: 5, Failures: 2, Errors: 0, Skipped: 1")
    assert _surefire(multi)[1] == "5 2 0 1"


def test_r8_skipped_reported():
    assert _surefire("Tests run: 1, Failures: 0, Errors: 0, Skipped: 1")[1] == "1 0 0 1"


# ---- R3: finding_id / issue id path traversal is rejected before any write ----

def test_r3_safe_id():
    assert pl._safe_id("ec-1") and pl._safe_id("5608")
    assert not pl._safe_id("../x") and not pl._safe_id("a/b") and not pl._safe_id("a.b")


def test_r3_repro_write_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    r = pl._write_repro_result("../../../../etc/x",
                               {"returncode": 0, "stdout": "```java\nclass X{}\n```"})
    assert r["ok"] is False and "invalid" in r["error"]
    assert not (tmp_path / "etc").exists()               # nothing written outside


def test_r3_fix_write_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    r = pl._write_fix_result("a/../../b",
                             {"returncode": 0, "stdout": "```diff\ndiff --git a/F b/F\n```"})
    assert r["ok"] is False and "invalid" in r["error"]


# ---- R4: run-fix.sh patch containment (runs the scan before the docker preflight) ----

TARGET = ROOT / "targets" / "jackson-databind"
needs_target = pytest.mark.skipif(not (TARGET / ".git").exists(),
                                  reason="jackson-databind clone absent")


def _run_fix(patch_text: str, tmp_path: Path):
    p = tmp_path / "p.patch"
    p.write_text(patch_text)
    return subprocess.run(["bash", str(ROOT / "scripts" / "run-fix.sh"),
                           str(TARGET), str(p), "com.x.Y"],
                          capture_output=True, text=True)


@needs_target
def test_r4_rejects_path_outside_src(tmp_path):
    patch = ("diff --git a/evil.txt b/evil.txt\nnew file mode 100644\n"
             "--- /dev/null\n+++ b/evil.txt\n@@ -0,0 +1 @@\n+pwned\n")
    assert _run_fix(patch, tmp_path).returncode == 3


@needs_target
def test_r4_rejects_symlink_hunk(tmp_path):
    patch = ("diff --git a/src/main/java/x b/src/main/java/x\nnew file mode 120000\n"
             "--- /dev/null\n+++ b/src/main/java/x\n@@ -0,0 +1 @@\n+/etc/passwd\n")
    assert _run_fix(patch, tmp_path).returncode == 3


@needs_target
def test_r4_rejects_dotgit_path(tmp_path):
    patch = ("diff --git a/.git/hooks/post-checkout b/.git/hooks/post-checkout\n"
             "new file mode 100755\n--- /dev/null\n+++ b/.git/hooks/post-checkout\n"
             "@@ -0,0 +1 @@\n+#!/bin/sh\n")
    assert _run_fix(patch, tmp_path).returncode == 3


@needs_target
def test_r4_valid_src_patch_passes_containment(tmp_path):
    # New file under src/main/java passes the scan, then hits the docker preflight
    # (absent here) -> exit 2. The point: it is NOT rejected as exit-3 containment.
    patch = ("diff --git a/src/main/java/Zzz_r4.java b/src/main/java/Zzz_r4.java\n"
             "new file mode 100644\n--- /dev/null\n+++ b/src/main/java/Zzz_r4.java\n"
             "@@ -0,0 +1 @@\n+class Zzz_r4 {}\n")
    rc = _run_fix(patch, tmp_path).returncode
    assert rc != 3                                       # containment let it through


# ---- R5: orchestrator distinguishes a real verdict from an inconclusive run ----

def _repro_present(cell):
    d = cell / "hunt" / "repros"; d.mkdir(parents=True, exist_ok=True)
    (d / "ec-1.java").write_text("class X {}")


def _orch_setup(cell, fid="ec-1"):
    v = cell / "hunt" / "validation"; v.mkdir(parents=True, exist_ok=True)
    (v / f"{fid}.yaml").write_text(f"finding_id: {fid}\nlanguage: java\ntarget: t\n")
    for sub, name in (("repros", f"{fid}.java"), ("patches", f"{fid}.patch")):
        d = cell / "hunt" / sub; d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("x")


def test_r5_not_attempted_is_not_validated(tmp_path, monkeypatch):
    # R5: an inconclusive verdict (e.g. sandbox/build failure) is NOT "validated".
    cell = tmp_path / "cell-1"; _orch_setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    import run_harness as rh
    monkeypatch.setattr(rh, "orchestrate",
                        lambda *a, **k: types.SimpleNamespace(status="inconclusive", attempts=0, detail="d"))
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "inconclusive" and r["validated"] is False


def test_r5_fixed_is_validated(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; _orch_setup(cell)
    monkeypatch.setattr(pl, "CELL", cell)
    import run_harness as rh
    monkeypatch.setattr(rh, "orchestrate",
                        lambda *a, **k: types.SimpleNamespace(status="validated", attempts=1, detail="d"))
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "fixed" and r["validated"] is True


def test_r5_orchestrate_all_validated_and_cli_exit(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; v = cell / "hunt" / "validation"; v.mkdir(parents=True)
    (v / "ec-1.yaml").write_text("finding_id: ec-1\n")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl, "orchestrate_finding",
                        lambda fid, *a, **k: {"finding_id": fid, "outcome": "repro-not-attempted",
                                              "validated": False})
    res = pl.orchestrate()
    assert res["ok"] is True and res["all_validated"] is False and res["inconclusive"] == 1
    assert pl._cli(["orchestrate", "--ids", "ec-1"]) == 2   # inconclusive -> non-zero exit
