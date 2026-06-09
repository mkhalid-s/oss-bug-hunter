"""M5 #47/#48 — env-bootstrap runner + wiring. Hermetic: run_step is injected (no
real uv/go/cargo/npm), so this tests the idempotency/marker/failure logic + the
run_harness DEP_ERROR seam."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import bootstrap as bs   # noqa: E402
import adapters as ad    # noqa: E402
import run_harness as rh  # noqa: E402

PY = ad.get_adapter("python")


def _wt(tmp_path, body="[project]\nname='x'\n"):
    (tmp_path / "pyproject.toml").write_text(body)
    return str(tmp_path)


class _Rec:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, argv, *, cwd, network=None):
        self.calls.append(argv)
        return self.rc, "ok"


def test_needs_bootstrap(tmp_path):
    assert bs.needs_bootstrap(str(tmp_path), PY) is False     # no manifest → nothing to do
    assert bs.needs_bootstrap(_wt(tmp_path), PY) is True


def test_bootstrap_runs_and_marks(tmp_path):
    wt, r = _wt(tmp_path), _Rec()
    res = bs.bootstrap(wt, PY, run_step=r, log=lambda *a: None)
    assert res["ok"] and res["status"] == "bootstrapped" and res["steps_run"] == 2
    assert r.calls[0][:2] == ["uv", "venv"] and (Path(wt) / bs.MARKER).exists()


def test_bootstrap_idempotent_cached(tmp_path):
    wt = _wt(tmp_path)
    bs.bootstrap(wt, PY, run_step=_Rec(), log=lambda *a: None)
    r2 = _Rec()
    res = bs.bootstrap(wt, PY, run_step=r2, log=lambda *a: None)
    assert res["status"] == "cached" and r2.calls == []        # unchanged → skipped


def test_bootstrap_rehashes_on_manifest_change(tmp_path):
    wt = _wt(tmp_path)
    bs.bootstrap(wt, PY, run_step=_Rec(), log=lambda *a: None)
    (Path(wt) / "pyproject.toml").write_text("[project]\nname='y'\n")   # manifest changed
    r2 = _Rec()
    res = bs.bootstrap(wt, PY, run_step=r2, log=lambda *a: None)
    assert res["status"] == "bootstrapped" and r2.calls         # re-ran


def test_bootstrap_failure_marks_failed(tmp_path):
    wt = _wt(tmp_path)
    res = bs.bootstrap(wt, PY, run_step=_Rec(rc=1), log=lambda *a: None)
    assert res["ok"] is False and res["status"] == "failed"
    assert json.loads((Path(wt) / bs.MARKER).read_text())["status"] == "failed"


def test_bootstrap_skipped_no_manifest(tmp_path):
    res = bs.bootstrap(str(tmp_path), PY, run_step=_Rec(), log=lambda *a: None)
    assert res["status"] == "skipped"


class _FakeBackend:
    def __init__(self, name):
        self.name = name


def test_maybe_bootstrap_trust_gate_and_errors(tmp_path, monkeypatch):
    wt = _wt(tmp_path)
    local, container = _FakeBackend("local"), _FakeBackend("docker")
    # P0: untrusted (container backend) + needs bootstrap → FAIL CLOSED, no host install
    v = rh._maybe_bootstrap(PY, wt, container, log=lambda *a: None)
    assert v is not None and v.outcome is rh.Outcome.DEP_ERROR and "host" in v.raw_summary
    # local + bootstrap returns ok:False → DEP_ERROR
    monkeypatch.setattr(bs, "bootstrap", lambda *a, **k: {"ok": False, "step": ["uv", "x"]})
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None).outcome is rh.Outcome.DEP_ERROR
    # P2: bootstrap RAISES (e.g. uv not on PATH) → DEP_ERROR, not a silent skip

    def _boom(*a, **k):
        raise FileNotFoundError("uv")
    monkeypatch.setattr(bs, "bootstrap", _boom)
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None).outcome is rh.Outcome.DEP_ERROR
    # local + success → None (proceed)
    monkeypatch.setattr(bs, "bootstrap", lambda *a, **k: {"ok": True, "status": "bootstrapped"})
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None) is None
    # no manifest → None regardless of backend (no-op)
    empty = tmp_path / "empty"; empty.mkdir()
    assert rh._maybe_bootstrap(PY, str(empty), container, log=lambda *a: None) is None


def test_lockfiles_in_manifest_hash(tmp_path):
    go = ad.get_adapter("go")
    (tmp_path / "go.mod").write_text("module x\n")
    h1 = bs._manifest_hash(str(tmp_path), go)
    (tmp_path / "go.sum").write_text("h1:abc=\n")          # a lockfile-only change
    assert bs._manifest_hash(str(tmp_path), go) != h1      # go.sum now invalidates the cache
