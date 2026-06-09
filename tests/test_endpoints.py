"""Endpoint-level smoke tests for the HTTP seam (the review found this whole
surface untested). FastAPI TestClient against the real app: auth, the
findings/targets envelopes, the SSE stream (replay of a finished run), and the
converged /api/orchestrate path."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402

TOK = server.AUTH_TOKEN
H = {"Authorization": f"Bearer {TOK}"}


def _client():
    # base_url=localhost so the Host header is in the allowlist (TestClient
    # defaults to "testserver", which the host-allowlist middleware rejects).
    return TestClient(server.app, base_url="http://localhost")


def test_auth_required():
    with _client() as c:
        assert c.get("/api/status").status_code == 401          # no token
        assert c.get("/api/status", headers=H).status_code == 200


def test_findings_envelope_and_traversal():
    with _client() as c:
        d = c.get("/api/findings", headers=H).json()
        assert d["ok"] and isinstance(d["findings"], list)
        ec = [f for f in d["findings"] if f["id"] == "ec-1"]
        assert ec and ec[0]["language"] == "java" and ec[0]["target"] == "jackson-databind"
        assert c.get("/api/findings/..", headers=H).status_code == 404       # traversal
        assert c.get("/api/findings/nope-xyz", headers=H).status_code == 404


def test_targets_envelope():
    with _client() as c:
        d = c.get("/api/targets", headers=H).json()
        assert d["ok"] and any(t["name"] == "jackson-databind" for t in d["targets"])


def test_pr_preview_blocked_for_non_keeper():
    with _client() as c:
        d = c.get("/api/findings/ec-1/pr-preview", headers=H).json()
        assert d["ok"] and d["ready"] is False and d["blockers"]   # ec-1 not a keeper


def test_demo_run_streams_and_replays():
    with _client() as c:
        rid = c.post("/api/runs", json={"kind": "demo"}, headers=H).json()["run_id"]
        for _ in range(100):                                     # wait for terminal (~3s)
            if c.get(f"/api/runs/{rid}", headers=H).json()["status"] in (
                    "done", "error", "interrupted"):
                break
            time.sleep(0.1)
        r = c.get(f"/api/runs/{rid}/stream?token={TOK}")         # replay finished run
        assert "event: done" in r.text and "[demo]" in r.text


def test_unknown_run_kind_400():
    with _client() as c:
        assert c.post("/api/runs", json={"kind": "bogus"}, headers=H).status_code == 400


def test_orchestrate_endpoint_multilang():
    if not (ROOT / "cell-1/hunt/validation/py-1.yaml").is_file():
        pytest.skip("py-1 finding not present")
    with _client() as c:
        r = c.post("/api/orchestrate",
                   json={"finding_ids": ["py-1"], "network": "none", "max_fix_attempts": 0},
                   headers=H).json()
        assert r["ok"] and r["outcomes"].get("fixed") == 1
        assert r["results"][0]["lang"] == "python"
