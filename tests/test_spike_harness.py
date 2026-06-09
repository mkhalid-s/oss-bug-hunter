"""Unit tests for the M0 spike modules: exec_backend, run_harness, run_store.
Pure-logic only — no Docker/mvn/network, so they run anywhere."""
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))

import exec_backend as eb       # noqa: E402
import run_harness as rh        # noqa: E402
import run_store as rs          # noqa: E402


# ---- run_harness.parse_console -> Outcome ----
@pytest.mark.parametrize("box,expected", [
    ("[ 1 tests found ]\n[ 0 tests successful ]\n[ 1 tests failed ]", rh.Outcome.FAILED),
    ("[ 1 tests found ]\n[ 1 tests successful ]\n[ 0 tests failed ]", rh.Outcome.PASSED),
    ("[ 0 tests found ]\n[ 0 tests successful ]\n[ 0 tests failed ]", rh.Outcome.NO_TESTS),
    ("[ 1 tests found ]\n[ 0 tests successful ]\n[ 0 tests failed ]\n[ 1 tests aborted ]",
     rh.Outcome.NO_TESTS),
])
def test_parse_console(box, expected):
    assert rh.parse_console(box).outcome is expected


# ---- run_harness.parse_surefire -> Outcome ----
@pytest.mark.parametrize("txt,expected", [
    ("Tests run: 1, Failures: 1, Errors: 0, Skipped: 0", rh.Outcome.FAILED),
    ("Tests run: 1, Failures: 0, Errors: 0, Skipped: 0", rh.Outcome.PASSED),
    ("Tests run: 1, Failures: 0, Errors: 0, Skipped: 1", rh.Outcome.NO_TESTS),
    ("COMPILATION ERROR cannot find symbol", rh.Outcome.BUILD_ERROR),
    ("Could not resolve dependencies", rh.Outcome.DEP_ERROR),
    ("docker boom", rh.Outcome.TOOL_ERROR),
])
def test_parse_surefire(txt, expected):
    assert rh.parse_surefire(txt).outcome is expected


def test_exit_code_mapping():
    mk = lambda oc: rh.TestVerdict(oc, 0, 0, 0, 0, "")
    assert mk(rh.Outcome.PASSED).exit_code() == 0
    assert mk(rh.Outcome.FAILED).exit_code() == 1
    assert mk(rh.Outcome.NO_TESTS).exit_code() == 2
    assert mk(rh.Outcome.BUILD_ERROR).exit_code() == 2


# ---- patch containment ----
def test_bad_hunk_regex():
    assert rh._BAD_HUNK.search("old mode 100644\nnew mode 100755\n")
    assert rh._BAD_HUNK.search("new file mode 120000\n")          # symlink
    assert rh._BAD_HUNK.search("rename from a\nrename to b\n")
    assert not rh._BAD_HUNK.search("--- a/src/main/java/Foo.java\n+++ b/src/main/java/Foo.java\n")


def test_containment_rejects_outside_src(tmp_path):
    # a git repo with a patch touching a manifest path -> rejected
    import subprocess
    wt = tmp_path / "repo"
    wt.mkdir()
    subprocess.run(["git", "-C", str(wt), "init", "-q"], check=True)
    (wt / "pom.xml").write_text("<x/>\n")
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "init"], check=True)
    patch = tmp_path / "p.patch"
    patch.write_text("diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n"
                     "@@ -1 +1 @@\n-<x/>\n+<y/>\n")
    ok, reason = rh.check_patch_containment(str(wt), str(patch))
    assert not ok and "outside" in reason


# ---- exec_backend trust gating (fail-closed) ----
def test_local_never_runs_untrusted():
    assert eb.LocalBackend().supports_untrusted() is False
    assert eb.DockerBackend().supports_untrusted() is True
    assert eb.PodmanBackend().supports_untrusted() is True


def test_select_backend_fail_closed(monkeypatch):
    # force no container engine -> untrusted gets NO backend; trusted gets local
    monkeypatch.setattr(eb.DockerBackend, "detect", lambda self: False)
    monkeypatch.setattr(eb.PodmanBackend, "detect", lambda self: False)
    with pytest.raises(eb.BackendError):
        eb.select_backend(trusted=False)
    assert eb.select_backend(trusted=True).name == "local"


# ---- run_store: store + broker + worker ----
def test_run_store_crud_and_replay(tmp_path):
    rs.init_db(str(tmp_path / "runs.db"))
    rid = rs.create_run("demo", {"a": 1})
    assert rs.get_run(rid)["status"] == "queued"
    rs.set_status(rid, "running")
    assert rs.get_run(rid)["status"] == "running"
    s1 = rs.append_log(rid, "line one")
    s2 = rs.append_log(rid, "line two", "stderr")
    assert s2 > s1
    logs = rs.get_logs(rid, after=0)
    assert [l[2] for l in logs] == ["line one", "line two"]
    assert rs.get_logs(rid, after=s1) == [(s2, "stderr", "line two")]   # replay-after
    assert any(r["id"] == rid for r in rs.list_runs())


def test_run_store_broker_publishes(tmp_path):
    rs.init_db(str(tmp_path / "runs.db"))
    rid = rs.create_run("demo", {})
    got = []
    rs.subscribe(rid, got.append)
    rs.append_log(rid, "hello")
    rs.unsubscribe(rid, got.append)
    assert got and got[0]["line"] == "hello"


def test_run_store_worker_runs_to_done(tmp_path):
    rs.init_db(str(tmp_path / "runs.db"))

    def fn(run_id, emit):
        emit("working")
        return {"exit": 0, "backend": "none"}

    rid = rs.submit_job("demo", {}, fn)
    for _ in range(50):                       # poll up to ~5s
        if rs.get_run(rid)["status"] in rs.TERMINAL:
            break
        time.sleep(0.1)
    r = rs.get_run(rid)
    assert r["status"] == "done" and r["exit_code"] == 0
    assert any(l[2] == "working" for l in rs.get_logs(rid))


def test_run_store_reconciles_zombies(tmp_path):
    db = str(tmp_path / "runs.db")
    rs.init_db(db)
    rid = rs.create_run("demo", {})
    rs.set_status(rid, "running")
    rs.init_db(db)                            # simulate restart
    assert rs.get_run(rid)["status"] == "interrupted"


# ---- run_harness.orchestrate: self-correcting loop (stubbed validators) ----
def _v(outcome):
    return rh.TestVerdict(outcome, 1, 0, 0, 0, "stub")


def _orch(**kw):
    return rh.orchestrate("wt", "F", "t", "p", trusted=True, network="bridge",
                          log=lambda *a: None, **kw)


def test_orchestrate_not_reproduced(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.PASSED))
    res = _orch()
    assert res.status == "not-reproduced" and res.exit_code() == 2
    assert not res.reproduced and not res.fixed


def test_orchestrate_inconclusive(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.BUILD_ERROR))
    res = _orch()
    assert res.status == "inconclusive" and res.exit_code() == 3


def test_orchestrate_validated_first_try(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.FAILED))
    monkeypatch.setattr(rh, "validate_fix", lambda *a, **k: _v(rh.Outcome.PASSED))
    res = _orch()
    assert res.status == "validated" and res.attempts == 1 and res.exit_code() == 0
    assert res.reproduced and res.fixed


def test_orchestrate_fix_failed_no_provider(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.FAILED))
    monkeypatch.setattr(rh, "validate_fix", lambda *a, **k: _v(rh.Outcome.FAILED))
    res = _orch()
    assert res.status == "fix-failed" and res.attempts == 1 and res.exit_code() == 1
    assert res.reproduced and not res.fixed


def test_orchestrate_retry_then_validated(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.FAILED))
    seq = [rh.Outcome.FAILED, rh.Outcome.PASSED]
    calls = {"n": 0}

    def fix(*a, **k):
        oc = seq[calls["n"]]
        calls["n"] += 1
        return _v(oc)
    monkeypatch.setattr(rh, "validate_fix", fix)
    provided = {"n": 0}

    def provider(feedback, attempt):
        provided["n"] += 1
        return "revised.patch"
    res = _orch(fix_provider=provider, max_retries=1)
    assert res.status == "validated" and res.attempts == 2
    assert provided["n"] == 1          # the failure fed back exactly once


def test_orchestrate_retry_exhausted(monkeypatch):
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.FAILED))
    monkeypatch.setattr(rh, "validate_fix", lambda *a, **k: _v(rh.Outcome.FAILED))
    res = _orch(fix_provider=lambda feedback, attempt: "p2", max_retries=2)
    assert res.status == "fix-failed" and res.attempts == 3   # 1 + 2 retries


# ---- LLM fix-builder wired as orchestrate fix_provider ----
import llm_fix_provider as lfp  # noqa: E402

_DIFF_RESP = ("Here is the corrected patch:\n```diff\n"
              "--- a/src/main/java/F.java\n+++ b/src/main/java/F.java\n"
              "@@ -1 +1 @@\n-a\n+b\n```\n")
_SCAFFOLD = {"finding_id": "ec-1", "summary": "s", "location": "src/main/java/F.java:1",
             "type": "t", "evidence": "e", "reproducer_hint": "h"}


def test_llm_fix_provider_writes_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(lfp.claude_driver, "run_claude_with_retry",
                        lambda prompt, **k: {"returncode": 0, "stdout": _DIFF_RESP, "stderr": ""})
    prov = lfp.make_llm_fix_provider(_SCAFFOLD, "class X {}", str(tmp_path), log=lambda *a: None)
    p = prov(feedback="reproducer still fails", attempt=1)
    assert p and Path(p).exists()
    assert "--- a/src/main/java/F.java" in Path(p).read_text()


def test_llm_fix_provider_no_diff_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lfp.claude_driver, "run_claude_with_retry",
                        lambda prompt, **k: {"returncode": 0, "stdout": "sorry, no patch", "stderr": ""})
    prov = lfp.make_llm_fix_provider(_SCAFFOLD, None, str(tmp_path), log=lambda *a: None)
    assert prov("fb", 1) is None


def test_llm_fix_provider_claude_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lfp.claude_driver, "run_claude_with_retry",
                        lambda prompt, **k: {"returncode": 1, "stdout": "", "stderr": "boom"})
    prov = lfp.make_llm_fix_provider(_SCAFFOLD, None, str(tmp_path), log=lambda *a: None)
    assert prov("fb", 1) is None


def test_orchestrate_drives_llm_provider(tmp_path, monkeypatch):
    """End-to-end wiring: orchestrate -> fix fails -> LLM provider regenerates a
    patch (stubbed claude) -> retry passes. Only the LLM call + validators stubbed."""
    monkeypatch.setattr(rh, "validate_repro", lambda *a, **k: _v(rh.Outcome.FAILED))
    seq = [rh.Outcome.FAILED, rh.Outcome.PASSED]
    calls = {"n": 0}

    def fix(*a, **k):
        oc = seq[calls["n"]]; calls["n"] += 1
        return _v(oc)
    monkeypatch.setattr(rh, "validate_fix", fix)
    monkeypatch.setattr(lfp.claude_driver, "run_claude_with_retry",
                        lambda prompt, **k: {"returncode": 0, "stdout": _DIFF_RESP, "stderr": ""})
    provider = lfp.make_llm_fix_provider(_SCAFFOLD, "class X {}", str(tmp_path), log=lambda *a: None)
    res = rh.orchestrate("wt", "F", "t", "p", trusted=True, network="bridge",
                         log=lambda *a: None, fix_provider=provider, max_retries=1)
    assert res.status == "validated" and res.attempts == 2
    assert (tmp_path / "ec-1-retry1.patch").exists()      # the LLM-regenerated patch


# ---- review fixes: direct-backend selection + missing-worktree guards ----
def test_select_direct_backend_trusted_local():
    backend, err = rh._select_direct_backend(trusted=True, log=lambda *a: None)
    assert err is None and backend.name == "local"


def test_select_direct_backend_untrusted_no_engine(monkeypatch):
    monkeypatch.setattr(eb.DockerBackend, "detect", lambda self: False)
    monkeypatch.setattr(eb.PodmanBackend, "detect", lambda self: False)
    backend, err = rh._select_direct_backend(trusted=False, log=lambda *a: None)
    assert backend is None and err.outcome is rh.Outcome.TOOL_ERROR


def test_validate_repro_missing_worktree():
    v = rh.validate_repro("/nope/does/not/exist", "F", "t",
                          trusted=True, network="bridge", log=lambda *a: None)
    assert v.outcome is rh.Outcome.TOOL_ERROR and "worktree not found" in v.raw_summary


def test_validate_fix_missing_worktree():
    v = rh.validate_fix("/nope/does/not/exist", "F", "t", "p",
                        trusted=True, network="bridge", log=lambda *a: None)
    assert v.outcome is rh.Outcome.TOOL_ERROR and "worktree not found" in v.raw_summary


def test_find_console_jar_prefers_pinned_version(tmp_path, monkeypatch):
    base = tmp_path / ".m2/repository/org/junit/platform/junit-platform-console-standalone"
    (base / "1.9.3").mkdir(parents=True)
    (base / "1.11.3").mkdir(parents=True)
    (base / "1.9.3" / "junit-platform-console-standalone-1.9.3.jar").write_text("x")
    (base / "1.11.3" / "junit-platform-console-standalone-1.11.3.jar").write_text("x")
    monkeypatch.setattr(rh.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    got = rh._find_console_jar("1.11.3")
    assert got and got.endswith("junit-platform-console-standalone-1.11.3.jar")


# ---- U3 targets: detect + add-by-URL (local file:// clone, no network) ----
import subprocess as _sp  # noqa: E402
import targets as tg      # noqa: E402
import ingest as ing      # noqa: E402
import findings as fnd    # noqa: E402
import llm_repro_provider as lrp   # noqa: E402
import llm_fix_builder as lfb       # noqa: E402


def _make_repo(path, marker, content="x"):
    path.mkdir(parents=True)
    (path / marker).write_text(content)
    _sp.run(["git", "init", "-q", str(path)], check=True)
    _sp.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "add", "-A"], check=True)
    _sp.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "commit", "-qm", "init"], check=True)


def test_detect_language(tmp_path):
    (tmp_path / "pom.xml").write_text("<x/>")
    assert tg.detect_language(tmp_path) == "java"


def test_detect_language_python_go_unknown(tmp_path):
    (tmp_path / "go.mod").write_text("module x")
    assert tg.detect_language(tmp_path) == "go"
    assert tg.detect_language(tmp_path / "nope") == "unknown"


def test_add_target_clones_detects_and_fail_closed(tmp_path, monkeypatch):
    src = tmp_path / "src-go"
    _make_repo(src, "go.mod", "module x\n\ngo 1.22\n")
    tdir = tmp_path / "targets"
    tdir.mkdir()
    monkeypatch.setattr(tg, "TARGETS_DIR", tdir)
    monkeypatch.setattr(tg, "META_DIR", tdir / "_meta")
    res = tg.add_target(f"file://{src}", trusted=False, log=lambda *a: None)
    assert res["language"] == "go" and res["name"] == "src-go" and res["trusted"] is False
    assert (tdir / "src-go" / "go.mod").exists()
    assert (tdir / "_meta" / "src-go.yaml").exists()
    listed = tg.list_targets()
    assert any(t["name"] == "src-go" and t["language"] == "go" and t["trusted"] is False
               for t in listed)


def test_add_target_rejects_flag_url(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "TARGETS_DIR", tmp_path)
    monkeypatch.setattr(tg, "META_DIR", tmp_path / "_meta")
    with pytest.raises(ValueError):
        tg.add_target("--upload-pack=evil", log=lambda *a: None)


def test_add_target_rejects_duplicate(tmp_path, monkeypatch):
    src = tmp_path / "dup"
    _make_repo(src, "pom.xml")
    tdir = tmp_path / "t"
    (tdir / "dup").mkdir(parents=True)        # pre-existing target dir
    monkeypatch.setattr(tg, "TARGETS_DIR", tdir)
    monkeypatch.setattr(tg, "META_DIR", tdir / "_meta")
    with pytest.raises(ValueError):
        tg.add_target(f"file://{src}", name="dup", log=lambda *a: None)


# ---- U4: PR preview + identity gate ----
import pr as prmod  # noqa: E402


def test_owner_repo_parsing():
    assert prmod._owner_repo("https://github.com/FasterXML/jackson-databind") == "FasterXML/jackson-databind"
    assert prmod._owner_repo("git@github.com:org/repo.git") == "org/repo"
    assert prmod._owner_repo("file:///tmp/x") is None


def test_is_keeper():
    keeper = {"gates_full": {"fix_passes_tests": {"status": "pass"}, "dedup": {"is_duplicate": False}},
              "final_status": "validated"}
    assert prmod._is_keeper(keeper) is True
    # fix passed but failed self-consistency (ec-1's situation) -> not a keeper
    not_keeper = {"gates_full": {"fix_passes_tests": {"status": "pass"}},
                  "final_status": "failed-self-consistency"}
    assert prmod._is_keeper(not_keeper) is False
    # dupe -> not a keeper
    dupe = {"gates_full": {"fix_passes_tests": {"status": "pass"}, "dedup": {"is_duplicate": True}},
            "final_status": "validated"}
    assert prmod._is_keeper(dupe) is False


def test_pr_preview_ec1_blocked_not_keeper():
    p = prmod.pr_preview("ec-1")
    assert p is not None
    assert p["keeper"] is False and p["ready"] is False
    assert any("not a validated keeper" in b for b in p["blockers"])
    assert p["upstream"] == "FasterXML/jackson-databind"
    assert p["branch"] == "oss-bug-hunter/fix-ec-1"
    assert "identity" in p and "manual_steps" in p


def test_pr_preview_unknown_none():
    assert prmod.pr_preview("no-such-finding") is None


# ---- M2: Python HarnessAdapter ----
import adapters as _adapters  # noqa: E402


def test_get_adapter():
    assert _adapters.get_adapter("python") is not None
    assert _adapters.get_adapter("cobol") is None


def test_python_adapter_place_and_argv(tmp_path):
    a = _adapters.get_adapter("python")
    src = tmp_path / "r.py"
    src.write_text("def test_x():\n    assert True\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    sel = a.place_reproducer(str(wt), str(src), "ec-9")
    assert (wt / sel).exists() and sel.startswith("test_repro_")
    argv = a.test_argv(sel)
    assert argv[1:3] == ["-m", "pytest"] and sel in argv


def test_python_adapter_validates_synthetic_target():
    wt = str(ROOT / "targets" / "pybug-demo")
    repro = str(ROOT / "cell-1" / "hunt" / "repros" / "py-1.py")
    patch = str(ROOT / "cell-1" / "hunt" / "patches" / "py-1.patch")
    if not (Path(wt).is_dir() and Path(repro).is_file() and Path(patch).is_file()):
        import pytest as _pt
        _pt.skip("pybug-demo synthetic target not present")
    v = rh.validate_repro(wt, "py-1", repro, trusted=True, network="none",
                          lang="python", log=lambda *a: None)
    assert v.outcome is rh.Outcome.FAILED          # reproduces (IndexError on [])
    vf = rh.validate_fix(wt, "py-1", repro, patch, trusted=True, network="none",
                         lang="python", log=lambda *a: None)
    assert vf.outcome is rh.Outcome.PASSED         # fix works


# ---- Go HarnessAdapter ----
def test_go_adapter_registered():
    assert _adapters.get_adapter("go") is not None


def test_go_adapter_place_and_argv(tmp_path):
    a = _adapters.get_adapter("go")
    src = tmp_path / "t.go"
    src.write_text("package x\nimport \"testing\"\nfunc TestFoo(t *testing.T){}\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    sel = a.place_reproducer(str(wt), str(src), "go-9")
    assert sel == "TestFoo"
    assert (wt / "repro_go_9_test.go").exists()
    argv = a.test_argv(sel)
    assert argv[:3] == ["go", "test", "-run"] and "^TestFoo$" in argv


def test_go_adapter_parse():
    a = _adapters.get_adapter("go")
    assert a.parse_result("--- FAIL: TestX\nFAIL").outcome is rh.Outcome.FAILED
    assert a.parse_result("--- PASS: TestX\nPASS\nok ex").outcome is rh.Outcome.PASSED
    assert a.parse_result("FAIL ex [build failed]").outcome is rh.Outcome.BUILD_ERROR


def test_go_adapter_validates_synthetic_target():
    wt = str(ROOT / "targets" / "gobug-demo")
    repro = str(ROOT / "cell-1" / "hunt" / "repros" / "go-1.go")
    patch = str(ROOT / "cell-1" / "hunt" / "patches" / "go-1.patch")
    import shutil as _sh
    if not (Path(wt).is_dir() and Path(repro).is_file() and _sh.which("go")):
        import pytest as _pt
        _pt.skip("gobug-demo target or go toolchain not present")
    v = rh.validate_repro(wt, "go-1", repro, trusted=True, network="none",
                          lang="go", log=lambda *a: None)
    assert v.outcome is rh.Outcome.FAILED          # panic on empty slice
    vf = rh.validate_fix(wt, "go-1", repro, patch, trusted=True, network="none",
                         lang="go", log=lambda *a: None)
    assert vf.outcome is rh.Outcome.PASSED         # fix works


# ---- review-refine fixes: targets traversal/scheme, owner_repo, go panic, findings ext ----
def test_targets_rejects_traversal_and_scheme(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "TARGETS_DIR", tmp_path)
    monkeypatch.setattr(tg, "META_DIR", tmp_path / "_meta")
    assert tg.get_target("..") is None
    assert tg.get_target("a/b") is None
    with pytest.raises(ValueError):
        tg.add_target("https://github.com/o/r", name="..", log=lambda *a: None)
    with pytest.raises(ValueError):                       # ext:: => command exec, refused
        tg.add_target("ext::sh -c id", log=lambda *a: None)
    with pytest.raises(ValueError):                       # fd:: also refused
        tg.add_target("fd::3", log=lambda *a: None)


def test_owner_repo_anchored_and_dotted():
    assert prmod._owner_repo("https://github.com/o/repo.name") == "o/repo.name"
    assert prmod._owner_repo("git@github.com:o/r.git") == "o/r"
    # unanchored injection must NOT match
    assert prmod._owner_repo("https://evil.test/x#github.com/attacker/repo") is None
    assert prmod._owner_repo("ext::sh -c 'github.com/a/b'") is None


def test_go_adapter_panic_outside_test_is_failed():
    a = _adapters.get_adapter("go")
    out = "panic: runtime error: index out of range\nexit status 2\nFAIL\tpkg\t0.01s"
    assert a.parse_result(out).outcome is rh.Outcome.FAILED


def test_python_conftest_denied():
    a = _adapters.get_adapter("python")
    assert any(d.search("conftest.py") for d in a.patch_denied)


def test_findings_multilang_repro_ext():
    if not (ROOT / "cell-1/hunt/validation/py-1.yaml").is_file():
        import pytest as _pt
        _pt.skip("py-1 finding not present")
    import findings as _f
    d = _f.get_finding("py-1")
    assert d["language"] == "python" and d["target"] == "pybug-demo"
    assert d["reproducer_src"] is not None        # picked .py, not .java


# ---- container-execution path (spec assembly; no daemon here) ----
def test_container_spec_assembly_go(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-q", str(repo)], check=True)
    src = tmp_path / "t.go"
    src.write_text('package x\nimport "testing"\nfunc TestX(t *testing.T){}\n')
    cap = {}

    class _Fake:
        name = "docker"
        def build_image(self, d, tag, build_args=None, log=None):
            cap["built"] = (d, tag, build_args)
        def run(self, spec, log=None, on_start=None):
            cap["spec"] = spec
            if log:
                log("--- PASS: TestX\nPASS\nok x")
            return 0

    monkeypatch.setattr(rh, "select_backend", lambda trusted, prefer=None: _Fake())
    v = rh.validate_repro(str(repo), "x", str(src), trusted=False, network="none",
                          lang="go", log=lambda *a: None)
    s = cap["spec"]
    assert s.image == "oss-bug-hunter-go:latest"          # per-language image
    assert s.cwd == "/work"                               # in-container workdir
    assert s.mounts == [(str(repo), "/work")]             # worktree bind-mounted
    assert s.argv[:2] == ["go", "test"]                   # container argv
    assert cap["built"][1] == "oss-bug-hunter-go:latest"  # image built first
    assert dict(cap["built"][2]) and "UID" in cap["built"][2]
    assert v.outcome is rh.Outcome.PASSED


def test_untrusted_adapter_no_engine_fails_closed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-q", str(repo)], check=True)
    src = tmp_path / "t.go"
    src.write_text('package x\nimport "testing"\nfunc TestX(t *testing.T){}\n')
    monkeypatch.setattr(eb.DockerBackend, "detect", lambda self: False)
    monkeypatch.setattr(eb.PodmanBackend, "detect", lambda self: False)
    v = rh.validate_repro(str(repo), "x", str(src), trusted=False, network="none",
                          lang="go", log=lambda *a: None)
    assert v.outcome is rh.Outcome.TOOL_ERROR and "no usable" in v.raw_summary


# ---- Rust HarnessAdapter (parse/place/containment + cargo-guarded e2e) ----
def test_rust_adapter_registered():
    assert _adapters.get_adapter("rust") is not None


def test_rust_adapter_place_and_argv(tmp_path):
    a = _adapters.get_adapter("rust")
    src = tmp_path / "r.rs"
    src.write_text("#[test]\nfn t(){ assert!(true); }\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    sel = a.place_reproducer(str(wt), str(src), "rs-9")
    assert sel == "repro_rs_9"
    assert (wt / "tests" / "repro_rs_9.rs").exists()       # integration test placement
    assert a.test_argv(sel) == ["cargo", "test", "--test", "repro_rs_9"]


def test_rust_adapter_parse():
    a = _adapters.get_adapter("rust")
    assert a.parse_result("test result: ok. 1 passed; 0 failed; 0 ignored").outcome is rh.Outcome.PASSED
    assert a.parse_result("test result: FAILED. 0 passed; 1 failed; 0 ignored").outcome is rh.Outcome.FAILED
    assert a.parse_result("error[E0425]: cannot find\nerror: could not compile `x`").outcome is rh.Outcome.BUILD_ERROR
    assert a.parse_result("running 0 tests\ntest result: ok. 0 passed; 0 failed").outcome is rh.Outcome.NO_TESTS


def test_rust_adapter_containment():
    a = _adapters.get_adapter("rust")
    assert any(x.search("src/lib.rs") for x in a.patch_allowed)
    assert any(d.search("Cargo.toml") for d in a.patch_denied)
    assert any(d.search("Cargo.lock") for d in a.patch_denied)
    assert not any(x.search("Cargo.toml") for x in a.patch_allowed)   # manifest not a .rs


def test_rust_adapter_validates_synthetic_target():
    # cargo arrived in the devcontainer 2026-06-08, so Rust now runs end-to-end
    # here like Java/Python/Go/JS. Guarded: skips cleanly where cargo is absent.
    wt = str(ROOT / "targets" / "rustbug-demo")
    repro = str(ROOT / "cell-1" / "hunt" / "repros" / "rs-1.rs")
    patch = str(ROOT / "cell-1" / "hunt" / "patches" / "rs-1.patch")
    import shutil as _sh
    if not (Path(wt).is_dir() and Path(repro).is_file() and _sh.which("cargo")):
        import pytest as _pt
        _pt.skip("rustbug-demo target or cargo toolchain not present")
    v = rh.validate_repro(wt, "rs-1", repro, trusted=True, network="none",
                          lang="rust", log=lambda *a: None)
    assert v.outcome is rh.Outcome.FAILED          # panic on empty slice
    vf = rh.validate_fix(wt, "rs-1", repro, patch, trusted=True, network="none",
                         lang="rust", log=lambda *a: None)
    assert vf.outcome is rh.Outcome.PASSED         # fix works


# ---- JS HarnessAdapter (node --test; Node is available -> end-to-end) ----
def test_js_adapter_registered():
    assert _adapters.get_adapter("javascript") is not None


def test_js_adapter_place_and_argv(tmp_path):
    a = _adapters.get_adapter("javascript")
    src = tmp_path / "r.js"
    src.write_text("import test from 'node:test';\ntest('t',()=>{});\n")
    wt = tmp_path / "wt"; wt.mkdir()
    sel = a.place_reproducer(str(wt), str(src), "js-9")
    assert sel == "repro_js_9.test.js" and (wt / sel).exists()
    assert a.test_argv(sel) == ["node", "--test", "--test-reporter=tap", sel]


def test_js_adapter_parse():
    a = _adapters.get_adapter("javascript")
    assert a.parse_result("ok 1 - t\n# pass 1\n# fail 0").outcome is rh.Outcome.PASSED
    assert a.parse_result("not ok 1 - t\n# pass 0\n# fail 1").outcome is rh.Outcome.FAILED
    assert a.parse_result("Error: Cannot find module './x'").outcome is rh.Outcome.BUILD_ERROR
    assert a.parse_result("# tests 0\n# pass 0\n# fail 0").outcome is rh.Outcome.NO_TESTS


def test_js_validates_synthetic_target():
    wt = str(ROOT / "targets" / "jsbug-demo")
    repro = str(ROOT / "cell-1" / "hunt" / "repros" / "js-1.js")
    patch = str(ROOT / "cell-1" / "hunt" / "patches" / "js-1.patch")
    import shutil as _sh
    if not (Path(wt).is_dir() and Path(repro).is_file() and _sh.which("node")):
        import pytest as _pt
        _pt.skip("jsbug-demo target or node not present")
    v = rh.validate_repro(wt, "js-1", repro, trusted=True, network="none",
                          lang="javascript", log=lambda *a: None)
    assert v.outcome is rh.Outcome.FAILED          # all-negative bug reproduces
    vf = rh.validate_fix(wt, "js-1", repro, patch, trusted=True, network="none",
                         lang="javascript", log=lambda *a: None)
    assert vf.outcome is rh.Outcome.PASSED         # fix works


# ---- review-pass-4 correctness fixes ----
def test_rust_panic_with_no_summary_is_failed():
    a = _adapters.get_adapter("rust")
    out = ("running 1 test\nthread 'main' panicked at src/lib.rs:5:13:\n"
           "index out of bounds\nerror: test failed, to rerun pass `--test repro_x`")
    assert a.parse_result(out).outcome is rh.Outcome.FAILED   # was silently NO_TESTS


def test_python_anchors_counts_to_summary_line():
    a = _adapters.get_adapter("python")
    # a stray "3 errors" in test stdout must NOT flip a passing run to FAILED
    out = "my linter found 3 errors in the code\n===== 5 passed in 0.12s ====="
    assert a.parse_result(out).outcome is rh.Outcome.PASSED
    # but pytest's own summary "2 errors" IS honored
    out2 = "===== 2 errors in 0.01s ====="
    assert a.parse_result(out2).outcome is rh.Outcome.FAILED


def test_go_argv_uses_root_package_not_recursive():
    a = _adapters.get_adapter("go")
    assert a.test_argv("TestX")[-1] == "."        # not ./... (avoid unrelated-pkg compile mask)


# ---- M5: monorepo-aware component detection (motivated by the headroom pilot) ----
def test_detect_components_monorepo(tmp_path):
    # polyglot monorepo: a Rust workspace + crates, a Python root, a TS sdk, plus
    # vendored node_modules that MUST be pruned (like headroom's real shape).
    (tmp_path / "Cargo.toml").write_text("[workspace]\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    for c in ("core", "proxy"):
        d = tmp_path / "crates" / c
        d.mkdir(parents=True)
        (d / "Cargo.toml").write_text("[package]\n")
    sdk = tmp_path / "sdk" / "ts"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text("{}")
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}")            # vendored — must be skipped
    comps = tg.detect_components(str(tmp_path))
    dirs = {c["dir"]: c["language"] for c in comps}
    assert dirs.get(".") == "python"                  # root: pyproject beats Cargo by precedence
    assert dirs.get("crates/core") == "rust" and dirs.get("crates/proxy") == "rust"
    assert dirs.get(os.path.join("sdk", "ts")) == "javascript"
    assert not any("node_modules" in d for d in dirs)  # skip-dir pruned
    # full polyglot picture, unlike the root-only detect_language()
    assert {c["language"] for c in comps} == {"python", "rust", "javascript"}


def test_detect_components_skips_deep_and_empty(tmp_path):
    assert tg.detect_components(str(tmp_path)) == []   # no manifests anywhere
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    (deep / "go.mod").write_text("module x\n")
    assert tg.detect_components(str(tmp_path), max_depth=3) == []  # beyond depth → not found


# ---- Phase 2: ingest Anthropic VULN-FINDINGS.json / TRIAGE.json (docs/ADOPTION.md) ----
def _wj(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def test_ingest_maps_anthropic_finding(tmp_path):
    src = _wj(tmp_path, "VULN-FINDINGS.json", {"findings": [
        {"id": "f001", "file": "src/x.py", "line": 25, "category": "sql-injection",
         "severity": "HIGH", "title": "SQLi in q()", "description": "concats user input"}]})
    r = ing.ingest(src, language="python", target="demo", cell=tmp_path)
    assert r["written"] == ["vs-f001"] and r["source"] == "anthropic:vuln-scan"
    rec = yaml.safe_load((tmp_path / "hunt" / "validation" / "vs-f001.yaml").read_text())
    assert rec["type"] == "sql-injection" and rec["location"] == "src/x.py:25"
    assert rec["summary"] == "SQLi in q()" and rec["language"] == "python"
    assert rec["target"] == "demo" and rec["source"] == "anthropic:vuln-scan"
    assert rec["severity"] == "HIGH"                  # first-class (for the board)
    assert rec["triage"]["severity"] == "HIGH"        # fuller discipline preserved
    # unverified candidate: gates not-attempted, pending -> 'proposed' column
    assert rec["gates"]["reproducer"]["status"] == "not-attempted"
    assert rec["final_status"] == "pending"
    assert fnd._column(rec["gates"], rec["final_status"]) == "proposed"
    # severity + provenance are surfaced by the board summary (not dead YAML)
    summ = fnd._summary(rec)
    assert summ["severity"] == "HIGH" and summ["source"] == "anthropic:vuln-scan"
    assert rec["reproducer_hint"].startswith("Write a python test")  # seeds a future PoC builder


def test_ingest_skips_triage_rejected(tmp_path):
    src = _wj(tmp_path, "TRIAGE.json", {"findings": [
        {"id": "keep", "file": "a.go", "line": 1, "category": "bug", "verdict": "true_positive"},
        {"id": "fp", "file": "b.go", "line": 2, "category": "bug", "verdict": "false_positive"},
        {"id": "dup", "file": "c.go", "line": 3, "category": "bug", "is_duplicate": True}]})
    r = ing.ingest(src, language="go", target="demo", cell=tmp_path)
    assert r["written"] == ["vs-keep"] and r["source"] == "anthropic:triage"
    reasons = {s.get("id"): s["reason"] for s in r["skipped"]}
    assert "false_positive" in reasons["fp"] and reasons["dup"] == "duplicate"


def test_ingest_tolerant_shapes(tmp_path):
    one = {"id": "z", "file": "f", "line": 1, "category": "c"}
    for name, doc in (("a.json", [one]), ("b.json", {"findings": [one]}),
                      ("c.json", {"triaged": [one]})):
        r = ing.ingest(_wj(tmp_path, name, doc), language="rust", target="t",
                       cell=tmp_path, write=False)
        assert r["written"] == ["vs-z"]


def test_ingest_real_canary_fixture(tmp_path):
    # The vendored Anthropic fixture is genuine *raw* VULN-FINDINGS (pre-triage):
    # it deliberately includes planted false positives (f004 "no randomness
    # consumer", f005 guarded null-deref) + a dup (f002). Ingest of RAW scan output
    # is intentionally UNFILTERED — dedup/FP-removal is /triage's job (proven in
    # test_ingest_skips_triage_rejected). So all 5 land in 'proposed' until
    # execution-verification or a triaged re-ingest culls them.
    fix = ROOT / "vendor" / "anthropic-skills" / "skills" / "triage" / "fixtures" / "canary-findings.json"
    if not fix.is_file():
        pytest.skip("vendored canary fixture not present")
    r = ing.ingest(fix, language="c", target="canary", cell=tmp_path)
    assert len(r["written"]) == 5 and r["written"][0] == "vs-f001"   # unfiltered raw scan
    rec = yaml.safe_load((tmp_path / "hunt" / "validation" / "vs-f001.yaml").read_text())
    assert "overflow" in rec["type"]                  # f001 is a heap-buffer-overflow
    assert fnd._summary(rec)["column"] == "proposed"  # unverified — NOT a confirmed bug


def test_ingest_bad_json_raises_clean(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read/parse"):
        ing.ingest(bad, language="python", target="t", cell=tmp_path)
    # unknown/garbage severity is dropped to None, not echoed verbatim
    assert ing.map_finding({"id": "x", "severity": "spicy"}, language="python",
                           target="t", source="s")["severity"] is None


# ---- #54: LLM reproducer-builder for non-Java (AI proposes; engine disposes) ----
def _fake_claude(stdout, rc=0):
    return lambda prompt, **k: {"returncode": rc, "stdout": stdout, "stderr": ""}


def test_repro_builder_extracts_and_writes(tmp_path):
    scaf = {"finding_id": "vs-1", "language": "python", "summary": "boom",
            "location": "x.py:1", "evidence": "e", "reproducer_hint": "h"}
    out = tmp_path / "vs-1.py"
    body = "def test_repro():\n    import widget\n    assert widget.f() == 1\n"
    p = lrp.build_repro(scaf, str(out), log=lambda *a: None,
                        _runner=_fake_claude(f"sure:\n```python\n{body}```\n"))
    assert p == str(out) and out.read_text().strip() == body.strip()


def test_repro_builder_no_block_returns_none(tmp_path):
    scaf = {"finding_id": "vs-2", "language": "go"}
    assert lrp.build_repro(scaf, str(tmp_path / "vs-2.go"), log=lambda *a: None,
                           _runner=_fake_claude("no fenced code here")) is None


def test_repro_builder_claude_failure_returns_none(tmp_path):
    scaf = {"finding_id": "vs-3", "language": "rust"}
    assert lrp.build_repro(scaf, str(tmp_path / "vs-3.rs"), log=lambda *a: None,
                           _runner=_fake_claude("```rust\nx\n```", rc=1)) is None


def test_repro_builder_skips_java(tmp_path):
    # Java has its own jackson-aware builder (pipeline.run_repro_subagent)
    assert lrp.build_repro({"finding_id": "j", "language": "java"},
                           str(tmp_path / "j.java"), log=lambda *a: None,
                           _runner=_fake_claude("```java\nx\n```")) is None


def test_repro_prompt_and_extract():
    scaf = {"language": "go", "summary": "S", "location": "L", "evidence": "E",
            "reproducer_hint": "H"}
    pr = lrp.build_repro_prompt(scaf)
    assert "S" in pr and "L" in pr and "E" in pr and "H" in pr and "TestRepro" in pr
    # lang-tagged block preferred over a stray earlier block
    txt = "```\nnope\n```\nthen:\n```go\nfunc TestRepro(t *testing.T){}\n```"
    assert "TestRepro" in lrp.extract_code_block(txt, "go")


# ---- #55: LLM fix-builder for non-Java (AI proposes a minimal patch; engine disposes) ----
_DIFF = ("```diff\ndiff --git a/src/mathx.py b/src/mathx.py\n--- a/src/mathx.py\n"
         "+++ b/src/mathx.py\n@@ -1,2 +1,2 @@\n-    m = nums[0]\n+    m = nums[0] if nums else None\n```")


def test_fix_builder_extracts_and_writes(tmp_path):
    scaf = {"finding_id": "vs-1", "language": "python", "summary": "boom", "location": "x.py:1"}
    out = tmp_path / "vs-1.patch"
    p = lfb.build_fix(scaf, "def test(): ...", str(out), log=lambda *a: None,
                      _runner=_fake_claude(f"here:\n{_DIFF}\n"))
    assert p == str(out) and "mathx.py" in out.read_text() and "nums else None" in out.read_text()


def test_fix_builder_no_diff_returns_none(tmp_path):
    scaf = {"finding_id": "vs-2", "language": "go"}
    assert lfb.build_fix(scaf, "x", str(tmp_path / "vs-2.patch"), log=lambda *a: None,
                         _runner=_fake_claude("no diff in here")) is None


def test_fix_builder_skips_java(tmp_path):
    assert lfb.build_fix({"finding_id": "j", "language": "java"}, "x",
                         str(tmp_path / "j.patch"), log=lambda *a: None,
                         _runner=_fake_claude(_DIFF)) is None


def test_fix_builder_prompt_has_feedback_and_rule():
    scaf = {"language": "rust", "summary": "S", "location": "L", "evidence": "E"}
    pr = lfb.build_fix_prompt(scaf, "REPROSRC", feedback="it still panicked")
    assert "REPROSRC" in pr and "it still panicked" in pr and ".rs" in pr and "Cargo.toml" in pr


def test_fix_builder_provider_retry(tmp_path):
    scaf = {"finding_id": "vs-9", "language": "javascript"}
    prov = lfb.make_provider(scaf, "src", str(tmp_path), log=lambda *a: None,
                             _runner=_fake_claude(_DIFF))
    p = prov("prev failure", 2)
    assert p == str(tmp_path / "vs-9-retry2.patch") and "mathx.py" in Path(p).read_text()


# ---- M5 #46: adapter bootstrap interface (manifest detect + steps + per-target venv) ----
def test_bootstrap_steps_empty_without_manifest(tmp_path):
    for lang in ("python", "go", "rust", "javascript"):
        assert _adapters.get_adapter(lang).bootstrap_steps(str(tmp_path)) == []


def test_bootstrap_steps_per_language(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert _adapters.get_adapter("go").bootstrap_steps(str(tmp_path)) == [["go", "mod", "download"]]
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    assert _adapters.get_adapter("rust").bootstrap_steps(str(tmp_path)) == [["cargo", "fetch"]]
    (tmp_path / "package-lock.json").write_text("{}")
    assert _adapters.get_adapter("javascript").bootstrap_steps(str(tmp_path)) == [["npm", "ci"]]


def test_python_bootstrap_uses_uv(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    steps = _adapters.get_adapter("python").bootstrap_steps(str(tmp_path))
    assert steps[0][:2] == ["uv", "venv"] and steps[1][:4] == ["uv", "pip", "install", "-e"]
    assert "--python" in steps[1]
    # requirements path
    wt2 = tmp_path / "r"; wt2.mkdir(); (wt2 / "requirements.txt").write_text("pytest\n")
    rs = _adapters.get_adapter("python").bootstrap_steps(str(wt2))
    assert rs[0][:2] == ["uv", "venv"] and rs[1][:4] == ["uv", "pip", "install", "-r"]


def test_python_test_argv_uses_venv_when_present(tmp_path):
    a = _adapters.get_adapter("python")
    assert a.test_argv("t.py")[0] == sys.executable                  # no worktree → harness python
    assert a.test_argv("t.py", str(tmp_path))[0] == sys.executable   # no venv yet → harness python
    vpy = tmp_path / ".oss-venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True); vpy.write_text("")
    assert a.test_argv("t.py", str(tmp_path))[0] == str(vpy)         # venv present → venv python


def test_containment_denies_bootstrap_dirs():
    js = _adapters.get_adapter("javascript")
    assert any(d.search("node_modules/dep/i.js") for d in js.patch_denied)   # vendored deps unpatchable
    py = _adapters.get_adapter("python")
    assert any(d.search(".oss-venv/lib/x.py") for d in py.patch_denied)
    assert any(d.search(".oss-bootstrap.json") for d in py.patch_denied)
