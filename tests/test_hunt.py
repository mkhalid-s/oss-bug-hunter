"""§12.5 last mile (#61) — the headless hunt step. Hermetic: the claude runner is
injected (canned VULN-FINDINGS), so no LLM/network. Asserts scan→ingest + the
EngineSteps wiring."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import hunt        # noqa: E402
import scheduler   # noqa: E402


def _runner(stdout, rc=0):
    return lambda prompt, **k: {"returncode": rc, "stdout": stdout, "stderr": ""}


_FINDINGS = ('```json\n{"findings": [{"id": "f1", "file": "x.py", "line": 3, '
             '"category": "sql-injection", "severity": "HIGH", "title": "SQLi", '
             '"description": "concats user input"}]}\n```')


def test_vuln_scan_ingests(tmp_path):
    r = hunt.vuln_scan("t/dir", language="python", target_name="demo", cell=tmp_path,
                       log=lambda *a: None, runner=_runner(_FINDINGS))
    assert r["ok"] and r["finding_ids"] == ["vs-f1"]
    rec = yaml.safe_load((tmp_path / "hunt" / "validation" / "vs-f1.yaml").read_text())
    assert rec["type"] == "sql-injection" and rec["source"] == "anthropic:vuln-scan"
    assert rec["location"] == "x.py:3" and rec["language"] == "python"


def test_vuln_scan_no_json(tmp_path):
    r = hunt.vuln_scan("t", language="go", target_name="d", cell=tmp_path,
                       log=lambda *a: None, runner=_runner("no findings here, sorry"))
    assert r["ok"] is False and r["finding_ids"] == []


def test_vuln_scan_claude_fails(tmp_path):
    r = hunt.vuln_scan("t", language="go", target_name="d", cell=tmp_path,
                       log=lambda *a: None, runner=_runner(_FINDINGS, rc=1))
    assert r["ok"] is False


def test_scan_prompt_and_extract():
    p = hunt.build_scan_prompt("/x/y", language="rust")
    assert "/x/y" in p and "VULN-FINDINGS" in p and "rust" in p and "findings" in p
    assert hunt._extract_json('```json\n{"findings": []}\n```') == {"findings": []}
    assert hunt._extract_json('prefix {"findings": [1]} suffix') == {"findings": [1]}
    assert hunt._extract_json("no json at all") is None


def test_engine_steps_hunt_wired(monkeypatch):
    # EngineSteps.hunt must call hunt.vuln_scan with the detected language (no LLM here)
    monkeypatch.setattr(hunt, "vuln_scan", lambda *a, **k: {"ok": True, "finding_ids": ["vs-z"]})
    import targets as _tg
    monkeypatch.setattr(_tg, "detect_language", lambda p: "python")
    assert scheduler.EngineSteps().hunt("some-target") == ["vs-z"]
