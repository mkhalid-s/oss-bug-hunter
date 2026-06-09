"""Pins run_backtest_batch (parallel fan-out via run_claude_batch) and the
dashboard route ordering that makes the /batch endpoint reachable."""
from __future__ import annotations

import pipeline as pl


def _make_runs(cell, issues):
    runs = cell / "backtest" / "runs"
    for n in issues:
        d = runs / n
        d.mkdir(parents=True)
        (d / "prompt.md").write_text(f"prompt for {n}")
    return runs


def test_run_backtest_batch_all(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    runs = _make_runs(cell, ["5001", "5002", "5003"])
    monkeypatch.setattr(pl, "CELL", cell)
    # fake claude: valid findings block so post-processing writes findings.yaml
    monkeypatch.setattr(pl._cd, "run_claude",
                        lambda prompt, **kw: {"returncode": 0, "stderr": "",
                                              "stdout": "```yaml\nfindings: []\n```",
                                              "elapsed_s": 0.1})
    res = pl.run_backtest_batch(max_parallel=3)
    assert res["ok"] and res["total"] == 3 and res["succeeded"] == 3 and res["failed"] == 0
    for n in ("5001", "5002", "5003"):
        assert (runs / n / "findings.yaml").exists()
    # results are sorted by issue
    assert [r["issue"] for r in res["results"]] == ["5001", "5002", "5003"]


def test_run_backtest_batch_subset_and_missing(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    _make_runs(cell, ["5001"])
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude",
                        lambda prompt, **kw: {"returncode": 0, "stderr": "",
                                              "stdout": "```yaml\nfindings: []\n```",
                                              "elapsed_s": 0.1})
    res = pl.run_backtest_batch(issue_nums=["5001", "9999"], max_parallel=2)
    by = {r["issue"]: r for r in res["results"]}
    assert by["5001"]["ok"] is True
    assert by["9999"]["ok"] is False  # no prompt.md -> reported, not crashed


def test_run_backtest_batch_no_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CELL", tmp_path / "cell-1")
    res = pl.run_backtest_batch()
    assert res["ok"] is False and "no backtest entries" in res["error"]


def test_run_backtest_batch_propagates_failure(tmp_path, monkeypatch):
    cell = tmp_path / "cell-1"
    _make_runs(cell, ["5001"])
    monkeypatch.setattr(pl, "CELL", cell)
    monkeypatch.setattr(pl._cd, "run_claude",
                        lambda prompt, **kw: {"returncode": 1, "stderr": "boom", "stdout": ""})
    res = pl.run_backtest_batch(max_parallel=1)
    assert res["ok"] is True            # batch ran
    assert res["succeeded"] == 0 and res["failed"] == 1
    assert res["results"][0]["ok"] is False


def test_batch_route_declared_before_issue_route():
    # FastAPI matches in declaration order; /batch MUST precede /{issue_num}
    # or "batch" is swallowed as an issue_num.
    import server
    paths = [r.path for r in server.app.routes if getattr(r, "path", "").startswith("/api/subagent/backtest")]
    assert "/api/subagent/backtest/batch" in paths
    assert paths.index("/api/subagent/backtest/batch") < paths.index("/api/subagent/backtest/{issue_num}")
