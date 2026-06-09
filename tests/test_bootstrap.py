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


def test_maybe_bootstrap_dep_error_seam(tmp_path, monkeypatch):
    wt = _wt(tmp_path)
    monkeypatch.setattr(bs, "bootstrap", lambda *a, **k: {"ok": False, "step": ["uv", "x"]})
    v = rh._maybe_bootstrap(PY, wt, log=lambda *a: None)
    assert v is not None and v.outcome is rh.Outcome.DEP_ERROR
    monkeypatch.setattr(bs, "bootstrap", lambda *a, **k: {"ok": True, "status": "bootstrapped"})
    assert rh._maybe_bootstrap(PY, wt, log=lambda *a: None) is None      # success → proceed
    empty = tmp_path / "empty"; empty.mkdir()
    assert rh._maybe_bootstrap(PY, str(empty), log=lambda *a: None) is None  # no manifest → no-op
