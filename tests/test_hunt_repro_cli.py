"""Pins run_hunt_batch, run_repro_batch, and the pipeline.py CLI dispatch.
All claude dispatch is mocked — nothing spawns `claude -p`."""
from __future__ import annotations

import pipeline as pl


def _ok_findings(prompt, **kw):
    return {"returncode": 0, "stderr": "", "stdout": "```yaml\nfindings: []\n```", "elapsed_s": 0.1}


# ---- run_hunt_batch --------------------------------------------------------

def test_run_hunt_batch_default_four_passes(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    for angle in ("code-quality", "edge-case"):
        d = cell / "hunt" / angle
        d.mkdir(parents=True)
        (d / "prompt.md").write_text(f"{angle} prompt")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude", _ok_findings)
    res = pl.run_hunt_batch()  # default = DAY4_PASSES
    assert res["ok"] and res["total"] == 4 and res["succeeded"] == 4
    # all four pass files written
    assert (cell / "hunt" / "code-quality" / "findings-pass2.yaml").exists()
    assert (cell / "hunt" / "edge-case" / "findings-pass3.yaml").exists()
    # keyed correctly back to (angle, pass)
    keys = {(r["angle"], r["pass"]) for r in res["results"]}
    assert keys == set(pl.DAY4_PASSES)


def test_run_hunt_batch_custom_and_missing_prompt(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    d = cell / "hunt" / "code-quality"
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("cq prompt")
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude", _ok_findings)
    res = pl.run_hunt_batch([("code-quality", 1), ("edge-case", 1)], max_parallel=2)
    by = {(r["angle"], r["pass"]): r for r in res["results"]}
    assert by[("code-quality", 1)]["ok"] is True
    assert by[("edge-case", 1)]["ok"] is False  # no prompt.md


def test_run_hunt_batch_rejects_invalid_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    monkeypatch.setattr(pl._cd, "run_claude", _ok_findings)
    res = pl.run_hunt_batch([("bogus", 9)], max_parallel=1)
    assert res["results"][0]["ok"] is False and "invalid" in res["results"][0]["error"]


# ---- run_repro_batch -------------------------------------------------------

def _make_scaffold(cell, fid):
    v = cell / "hunt" / "validation"
    v.mkdir(parents=True, exist_ok=True)
    (v / f"{fid}.yaml").write_text(
        f"finding_id: {fid}\nsummary: S\nlocation: A.java:1\ntype: NPE\n"
        f"evidence: |\n  ev\nreproducer_hint: |\n  hint\n"
    )


def test_run_repro_batch_default_pending(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    _make_scaffold(cell, "cq-1")
    _make_scaffold(cell, "ec-1")
    monkeypatch.setattr(pl, "CELL", cell)

    def fake(prompt, **kw):
        # echo a java block naming the class the batch expects for whichever id
        cls = "Repro_cq_1" if "cq-1" in prompt else "Repro_ec_1"
        return {"returncode": 0, "stderr": "", "elapsed_s": 0.1,
                "stdout": f"```java\npackage com.fasterxml.jackson.databind.repro;\nclass {cls} {{}}\n```"}
    monkeypatch.setattr(pl._cd, "run_claude", fake)

    res = pl.run_repro_batch()  # None => all scaffolds missing a .java
    assert res["ok"] and res["total"] == 2 and res["succeeded"] == 2
    assert (cell / "hunt" / "repros" / "cq-1.java").exists()
    assert (cell / "hunt" / "repros" / "ec-1.java").exists()
    # once a .java exists, it's no longer "pending"
    assert pl.list_repro_finding_ids() == []


def test_run_repro_batch_no_scaffolds(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    res = pl.run_repro_batch()
    assert res["ok"] is False and "no validation scaffolds" in res["error"]


# ---- CLI -------------------------------------------------------------------

def test_parse_pass_token():
    assert pl._parse_pass_token("code-quality:2") == ("code-quality", 2)
    assert pl._parse_pass_token("edge-case:3") == ("edge-case", 3)


def test_cli_routes_to_hunt_batch(monkeypatch, capsys):
    captured = {}

    def fake_batch(passes, parallel):
        captured["passes"] = passes
        captured["parallel"] = parallel
        return {"ok": True, "total": 0, "results": []}

    monkeypatch.setattr(pl, "run_hunt_batch", fake_batch)
    rc = pl._cli(["run-hunt-batch", "--passes", "code-quality:2", "edge-case:3", "--parallel", "2"])
    assert rc == 0
    assert captured["passes"] == [("code-quality", 2), ("edge-case", 3)]
    assert captured["parallel"] == 2
    # prints JSON
    assert '"ok": true' in capsys.readouterr().out


def test_cli_exit_code_reflects_ok(monkeypatch, capsys):
    monkeypatch.setattr(pl, "run_repro_batch", lambda ids, par: {"ok": False, "error": "nope"})
    rc = pl._cli(["run-repro-batch"])
    assert rc == 1


def test_cli_parallel_is_bounded(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(pl, "run_backtest_batch",
                        lambda issues, par: seen.update(par=par) or {"ok": True})
    pl._cli(["run-backtest-batch", "--parallel", "999"])
    assert seen["par"] == 10  # capped
