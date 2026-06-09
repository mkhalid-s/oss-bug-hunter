"""Pins the P1 cluster fixes (R6, R7, R9, R10, R11, R12, R14) from the
2026-06-04 deep review."""
from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import yaml

import pipeline as pl
from conftest import day3_hunt as d3

ROOT = Path(__file__).resolve().parent.parent
PYBIN = str(ROOT / ".venv" / "bin" / "python")


# ---- R6: REPRO_NETWORK allowlist (host refused unless explicit opt-in) ----

def _net(env_overrides: dict):
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'source "{ROOT}/scripts/_repro_decide.sh"; repro_network_validate; echo OK'],
        capture_output=True, text=True, env={**os.environ, **env_overrides})
    return r.returncode, r.stdout.strip(), r.stderr


def test_r6_none_and_bridge_ok():
    assert _net({"REPRO_NETWORK": "none"})[0] == 0
    assert _net({"REPRO_NETWORK": "bridge"})[0] == 0
    assert _net({})[0] == 0                                   # default none


def test_r6_host_refused_by_default():
    rc, out, err = _net({"REPRO_NETWORK": "host"})
    assert rc == 2 and "refused" in err and out != "OK"


def test_r6_host_allowed_with_explicit_optin_and_warns():
    rc, out, err = _net({"REPRO_NETWORK": "host", "REPRO_ALLOW_HOST_NET": "1"})
    assert rc == 0 and "WARNING" in err


def test_r6_garbage_rejected():
    assert _net({"REPRO_NETWORK": "host --privileged"})[0] == 2


# ---- R7 / R12: lock + pristine helpers ----

def test_r7_lock_acquires(tmp_path):
    lock = tmp_path / "l.lock"
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'source "{ROOT}/scripts/_repro_decide.sh"; repro_acquire_lock "$1"; echo LOCKED', "_", str(lock)],
        capture_output=True, text=True)
    assert r.returncode == 0 and "LOCKED" in r.stdout and lock.exists()


def test_r12_pristine_resets_tracked_and_cleans_untracked(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def g(*a): subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(repo), *a],
                              check=True, capture_output=True, env=genv)
    g("init", "-q")
    (repo / "a.txt").write_text("orig\n"); g("add", "."); g("commit", "-qm", "init")
    (repo / "a.txt").write_text("MODIFIED\n")            # tracked change
    (repo / "untracked.txt").write_text("x\n")           # untracked file
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'source "{ROOT}/scripts/_repro_decide.sh"; repro_pristine "$1"', "_", str(repo)],
        capture_output=True, text=True)
    assert r.returncode == 0
    assert (repo / "a.txt").read_text() == "orig\n"      # tracked reset
    assert not (repo / "untracked.txt").exists()         # untracked cleaned


# ---- R9: orchestrator distinguishes "no reproducer built" from "does not reproduce" ----

def test_r9_status_none_is_no_reproducer_built(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; (cell / "hunt" / "repros").mkdir(parents=True)
    (cell / "hunt" / "repros" / "ec-1.java").write_text("class X {}")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl, "_load_day3_hunt", lambda: types.SimpleNamespace(
        validate_one_repro=lambda f, w, n: {"status": None, "ok": False, "note": "no reproducer .java"},
        validate_one_fix=lambda f, w, n: {"status": "pass", "ok": True}))
    r = pl.orchestrate_finding("ec-1")
    assert r["outcome"] == "no-reproducer-built" and r["validated"] is False


# ---- R10: suggest-gates is idempotent ----

def _scaffold_text(d3mod):
    return d3mod.VALIDATION_SCAFFOLD_TEMPLATE.format(
        finding_id="ec-1", angle="edge-case", summary_yaml='"S"', location_yaml='"L"',
        type_yaml='"empty-collection"', evidence_indented="  ev", reproducer_indented="  hint",
        osv_block="    []", github_block="    []")


def test_r10_apply_gate_suggestions_idempotent(tmp_path, monkeypatch):
    vdir = tmp_path / "validation"; vdir.mkdir()
    (vdir / "ec-1.yaml").write_text(_scaffold_text(d3))
    monkeypatch.setattr(d3, "VALIDATION_DIR", vdir)
    d3.apply_gate_suggestions()
    first = (vdir / "ec-1.yaml").read_text()
    n1 = yaml.safe_load(first)["gates"]["dedup"]["notes"]
    d3.apply_gate_suggestions()                              # second run
    second = (vdir / "ec-1.yaml").read_text()
    assert yaml.safe_load(second)["gates"]["dedup"]["notes"] == n1   # unchanged
    assert second.count("No OSV/GitHub candidates") == 1             # not doubled


# ---- R11: feedback rendered + malformed scaffold skipped (not crash) ----

def test_r11_feedback_rendered_in_fix_prompt():
    sc = {"finding_id": "ec-1", "summary": "s", "location": "L", "type": "NPE",
          "evidence": "e", "reproducer_hint": "h"}
    p = d3.build_fix_prompt(sc, repro_src="class X {}", feedback="reproducer STILL FAILS: boom")
    assert "reproducer STILL FAILS: boom" in p
    assert "PREVIOUS PATCH DID NOT WORK" in p


def test_r11_malformed_scaffold_skipped_not_crash(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"; v = cell / "hunt" / "validation"; v.mkdir(parents=True)
    (v / "ec-1.yaml").write_text("finding_id: ec-1\nsummary: ok\ntype: NPE\n")
    (v / "bad.yaml").write_text("finding_id: bad\n: : not valid ::\n  x\n")   # malformed YAML
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude",
                        lambda prompt, **kw: {"returncode": 0, "stdout": "```java\nclass Y {}\n```"})
    res = pl.run_repro_batch(["ec-1", "bad"], max_parallel=1)
    by = {r["finding_id"]: r for r in res["results"]}
    assert by["bad"]["ok"] is False and "malformed" in by["bad"]["error"]   # reported, not crashed
    assert by["ec-1"]["ok"] is True                                          # good one still ran


# ---- R14: malformed passes rejected; CLI pass-token tolerant ----

def test_r14_cli_pass_token_tolerant():
    assert pl._parse_pass_token("code-quality:2") == ("code-quality", 2)
    assert pl._parse_pass_token("garbage") == ("", -1)              # no colon -> rejected downstream
    assert pl._parse_pass_token("edge-case:x") == ("edge-case", -1)  # bad num -> no crash


def test_r14_rest_malformed_passes_400():
    code = (
        "import sys; sys.path.insert(0,'tool'); import server;"
        "from fastapi.testclient import TestClient;"
        "c=TestClient(server.app); h={'Authorization':'Bearer '+server.AUTH_TOKEN};"
        "print(c.post('/api/subagent/hunt/batch',headers=h,json={'passes':['cq']}).status_code,"
        "c.post('/api/subagent/hunt/batch',headers=h,json={'passes':[['code-quality']]}).status_code)"
    )
    r = subprocess.run([PYBIN, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                       env={**os.environ, "OSS_BUG_HUNTER_HOST_ALLOWLIST": "testserver"})
    assert r.stdout.strip() == "400 400", (r.stdout, r.stderr)
