"""Phase 3 §12.5 — scheduler/outer-loop driver. Hermetic: a FakeSteps double exercises
the loop logic (budget, idempotency, kill-switch, error isolation, audit) with no
clone/hunt/LLM. The real EngineSteps wiring is structural (not run here)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import scheduler   # noqa: E402
import discovery   # noqa: E402


class FakeSteps(scheduler.Steps):
    def __init__(self, *, findings=("f1",), verify="reproduced", fix="fixed", draft=True):
        self._f, self._v, self._fx, self._d = findings, verify, fix, draft

    def clone(self, cand):
        return "tgt"

    def hunt(self, target):
        return list(self._f)

    def verify(self, fid):
        return self._v

    def fix(self, fid):
        return self._fx

    def draft(self, fid):
        return self._d


def test_run_once_drives_to_draft(tmp_path):
    r = scheduler.run_once([{"repo": "o/a", "score": 5}], FakeSteps(), state_path=tmp_path / "s.yaml")
    assert r["processed"] == 1 and r["outcomes"] == {"drafted": 1}
    assert {"clone", "hunt", "verify", "fix", "draft"} <= {a["step"] for a in r["audit"]}


def test_budget_caps(tmp_path):
    cands = [{"repo": f"o/r{i}"} for i in range(5)]
    r = scheduler.run_once(cands, FakeSteps(), budget=scheduler.Budget(max_targets=2),
                           state_path=tmp_path / "s.yaml")
    assert r["processed"] == 2


def test_idempotent_skip_on_rerun(tmp_path):
    sp = tmp_path / "s.yaml"
    scheduler.run_once([{"repo": "o/a"}], FakeSteps(), state_path=sp)         # drafted, attempts=1
    r = scheduler.run_once([{"repo": "o/a"}], FakeSteps(), state_path=sp)     # max_attempts=1 -> skip
    assert r["processed"] == 0 and r["skipped"] == 1


def test_kill_switch_halts(tmp_path):
    r = scheduler.run_once([{"repo": "o/a"}, {"repo": "o/b"}], FakeSteps(),
                           state_path=tmp_path / "s.yaml", kill_switch=lambda: True)
    assert r["processed"] == 0 and any(a["step"] == "kill-switch" for a in r["audit"])


def test_error_isolation_continues(tmp_path):
    class S(scheduler.Steps):
        def clone(self, c):
            if c["repo"] == "o/bad":
                raise RuntimeError("clone boom")
            return "t"
        def hunt(self, t):
            return []                       # the good one finds nothing
    r = scheduler.run_once([{"repo": "o/bad"}, {"repo": "o/good"}], S(), state_path=tmp_path / "s.yaml")
    assert r["processed"] == 2
    assert r["outcomes"].get("error") == 1 and r["outcomes"].get("no-bug-found") == 1


def test_no_bug_and_no_draft(tmp_path):
    assert scheduler.run_once([{"repo": "o/a"}], FakeSteps(findings=()),
                              state_path=tmp_path / "a.yaml")["outcomes"] == {"no-bug-found": 1}
    assert scheduler.run_once([{"repo": "o/b"}], FakeSteps(fix="fix-failed"),
                              state_path=tmp_path / "b.yaml")["outcomes"] == {"no-draft": 1}


def test_load_queue_roundtrip(tmp_path):
    q = tmp_path / "q.yaml"
    discovery.enqueue([{"repo": "o/x", "score": 3}], path=q)
    assert scheduler.load_queue(q)[0]["repo"] == "o/x"


def test_plan_dry_run(tmp_path):
    sp = tmp_path / "s.yaml"
    scheduler.run_once([{"repo": "o/done"}], FakeSteps(), state_path=sp)   # mark o/done terminal
    p = scheduler.plan([{"repo": "o/done"}, {"repo": "o/new", "score": 4}, {"repo": "o/new2"}],
                       budget=scheduler.Budget(max_targets=1), state_path=sp)
    assert [w["repo"] for w in p["would_process"]] == ["o/new"]    # o/done skipped, capped at 1
    assert [s["repo"] for s in p["would_skip"]] == ["o/done"]
