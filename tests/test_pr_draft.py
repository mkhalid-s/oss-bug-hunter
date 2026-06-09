"""Phase 3 §12.6 — gated-PR draft queue. Hermetic: pr.pr_preview is mocked, the
drafts dir is redirected to tmp. Asserts the never-push posture + review lifecycle."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

import pr as _pr            # noqa: E402
import pr_draft as pd       # noqa: E402


def _pv(ready=True):
    return {"finding_id": "x", "target": "t", "branch": "oss-bug-hunter/fix-x",
            "title": "Fix bug in x.py", "upstream": "o/r", "fork": "mkhalid-s/r",
            "commit_message": "Fix bug in x.py", "body": "BODY",
            "manual_steps": ["unset GH_TOKEN", "gh auth switch -u mkhalid-s"],
            "ready": ready, "identity": {},
            "blockers": [] if ready else ["finding is not a validated keeper"]}


def test_queue_keeper_persists_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "DRAFTS", tmp_path)
    monkeypatch.setattr(_pr, "pr_preview", lambda fid, target="jackson-databind": _pv(True))
    r = pd.queue_draft("vs-1", "demo")
    assert r["ok"] and r["draft"]["status"] == "pending-review"
    assert (tmp_path / "vs-1.yaml").exists()
    # the draft carries the identity-gated manual push steps (we never push)
    assert any("unset GH_TOKEN" in s for s in pd.get_draft("vs-1")["manual_steps"])


def test_queue_nonkeeper_refused_unless_forced(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "DRAFTS", tmp_path)
    monkeypatch.setattr(_pr, "pr_preview", lambda *a, **k: _pv(False))
    r = pd.queue_draft("vs-2", "demo")
    assert r["ok"] is False and r["blockers"] and not (tmp_path / "vs-2.yaml").exists()
    assert pd.queue_draft("vs-2", "demo", force=True)["ok"] is True   # force overrides


def test_decide_and_preserve_on_requeue(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "DRAFTS", tmp_path)
    monkeypatch.setattr(_pr, "pr_preview", lambda *a, **k: _pv(True))
    pd.queue_draft("vs-3", "demo")
    assert pd.decide_draft("vs-3", "approved", note="lgtm")["draft"]["status"] == "approved"
    r = pd.queue_draft("vs-3", "demo")           # re-queue must NOT reset the human decision
    assert r["draft"]["status"] == "approved" and r["draft"]["decision_note"] == "lgtm"
    assert pd.decide_draft("vs-3", "bogus")["ok"] is False


def test_traversal_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "DRAFTS", tmp_path)
    monkeypatch.setattr(_pr, "pr_preview", lambda *a, **k: _pv(True))
    assert pd.queue_draft("../etc/passwd", "demo")["ok"] is False
    assert pd.get_draft("../x") is None


def test_list_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "DRAFTS", tmp_path)
    monkeypatch.setattr(_pr, "pr_preview", lambda *a, **k: _pv(True))
    pd.queue_draft("vs-a", "demo")
    pd.queue_draft("vs-b", "demo")
    assert {d["finding_id"] for d in pd.list_drafts()} == {"vs-a", "vs-b"}
