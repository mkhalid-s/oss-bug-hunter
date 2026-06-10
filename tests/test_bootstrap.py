"""M5 #47/#48 — env-bootstrap runner + wiring. Hermetic: run_step is injected (no
real uv/go/cargo/npm), so this tests the idempotency/marker/failure logic + the
run_harness DEP_ERROR seam."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import bootstrap as bs   # noqa: E402
import adapters as ad    # noqa: E402
import run_harness as rh  # noqa: E402
import exec_backend as eb  # noqa: E402

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

    def build_image(self, *a, **k):          # no-op (no daemon in tests)
        pass

    def run(self, spec, log=None):
        return 0


def test_maybe_bootstrap_trust_gate_and_errors(tmp_path, monkeypatch):
    wt = _wt(tmp_path)                        # python (bootstrap_in_worktree=True)
    local, container = _FakeBackend("local"), _FakeBackend("docker")
    cap = {}

    def _fake_bs(worktree, adapter, *, run_step=None, network="bridge", log=print, force=False):
        cap["run_step"] = run_step
        return {"ok": True, "status": "bootstrapped"}
    monkeypatch.setattr(bs, "bootstrap", _fake_bs)
    # local (trusted) → bootstrap on the host, NO container run_step
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None) is None and cap["run_step"] is None
    # #62: container (untrusted) + python (in-worktree) → IN-CONTAINER bootstrap (run_step set)
    assert rh._maybe_bootstrap(PY, wt, container, log=lambda *a: None) is None
    assert cap["run_step"] is not None        # ran in the container, not on the host
    # #63: cache-based lang (go) + container → NOW in-container bootstrap (GOMODCACHE/GOCACHE
    # redirected under /work by container_cache_env), so run_step is set — no longer fail-closed.
    go = ad.get_adapter("go")
    (tmp_path / "go.mod").write_text("module x\n")
    cap["run_step"] = None
    assert rh._maybe_bootstrap(go, str(tmp_path), container, log=lambda *a: None) is None
    assert cap["run_step"] is not None
    # the fail-closed else now requires an adapter that keeps deps OUTSIDE the worktree:
    class _NoWt:
        language = "exotic"
        MANIFESTS = ("exotic.toml",)
        def bootstrap_steps(self, wt): return [["true"]] if (Path(wt) / "exotic.toml").exists() else []
        def container_cache_env(self, work): return {}
    (tmp_path / "exotic.toml").write_text("x\n")
    v = rh._maybe_bootstrap(_NoWt(), str(tmp_path), container, log=lambda *a: None)
    assert v is not None and v.outcome is rh.Outcome.DEP_ERROR and "outside the worktree" in v.raw_summary
    # local + bootstrap ok:False → DEP_ERROR; raise → DEP_ERROR (P2)
    monkeypatch.setattr(bs, "bootstrap", lambda *a, **k: {"ok": False, "step": ["uv", "x"]})
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None).outcome is rh.Outcome.DEP_ERROR

    def _boom(*a, **k):
        raise FileNotFoundError("uv")
    monkeypatch.setattr(bs, "bootstrap", _boom)
    assert rh._maybe_bootstrap(PY, wt, local, log=lambda *a: None).outcome is rh.Outcome.DEP_ERROR
    # no manifest → None regardless of backend (no-op)
    empty = tmp_path / "empty"; empty.mkdir()
    assert rh._maybe_bootstrap(PY, str(empty), container, log=lambda *a: None) is None


def test_python_container_argv_uses_venv_when_present(tmp_path):
    a = ad.get_adapter("python")
    assert a.container_argv("t.py")[0] == "python"                    # no venv → image python
    assert a.container_argv("t.py", str(tmp_path))[0] == "python"     # no venv dir → image python
    vpy = tmp_path / ".oss-venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True); vpy.write_text("")
    assert a.container_argv("t.py", str(tmp_path))[0] == ".oss-venv/bin/python"   # venv → /work-relative


def test_lockfiles_in_manifest_hash(tmp_path):
    go = ad.get_adapter("go")
    (tmp_path / "go.mod").write_text("module x\n")
    h1 = bs._manifest_hash(str(tmp_path), go)
    (tmp_path / "go.sum").write_text("h1:abc=\n")          # a lockfile-only change
    assert bs._manifest_hash(str(tmp_path), go) != h1      # go.sum now invalidates the cache


# ---- #63: worktree-local go/rust cache redirection -------------------------------------

def test_container_cache_env_per_lang():
    # python/js keep deps in the worktree (venv path / node_modules) → no env needed.
    assert ad.get_adapter("python").container_cache_env("/work") == {}
    assert ad.get_adapter("javascript").container_cache_env("/work") == {}
    # go/rust redirect their caches under /work so they survive bootstrap-container → test-container.
    go = ad.get_adapter("go").container_cache_env("/work")
    assert go == {"GOMODCACHE": "/work/.oss-go/mod", "GOCACHE": "/work/.oss-go/build"}
    assert ad.get_adapter("rust").container_cache_env("/work") == {"CARGO_HOME": "/work/.oss-cargo"}


def test_container_backend_emits_only_allowlisted_env(tmp_path):
    """RunSpec.container_env → -e flags (the cache allowlist), NOT host os.environ — so a
    secret like GH_TOKEN in the harness env can never leak into the untrusted container."""
    cap = {}

    class _Cap(eb._ContainerBackend):
        cli = "docker"
        def _spawn(self, argv, cwd, env, log, on_start):
            cap["argv"] = list(argv)
            return 0

    spec = eb.RunSpec(argv=["cargo", "test"], cwd="/work", network="bridge", image="img",
                      mounts=[(str(tmp_path), "/work")],
                      container_env={"CARGO_HOME": "/work/.oss-cargo"})
    assert _Cap().run(spec) == 0
    a = cap["argv"]
    assert a.count("-e") == 1 and "CARGO_HOME=/work/.oss-cargo" in a   # exactly the allowlist
    assert "-v" in a and any(s.endswith(":/work:rw") for s in a)       # worktree mount
    assert a[a.index("img") + 1:] == ["cargo", "test"]                 # image, then argv


def test_js_bootstrap_steps_per_lockfile(tmp_path):
    js = ad.get_adapter("javascript")
    (tmp_path / "package.json").write_text("{}")
    assert js.bootstrap_steps(str(tmp_path)) == [["npm", "install"]]
    (tmp_path / "package-lock.json").write_text("{}")
    assert js.bootstrap_steps(str(tmp_path)) == [["npm", "ci"]]
    (tmp_path / "yarn.lock").write_text("")
    assert js.bootstrap_steps(str(tmp_path))[0][:2] == ["corepack", "yarn"]   # yarn beats npm-lock
    (tmp_path / "pnpm-lock.yaml").write_text("")
    s = js.bootstrap_steps(str(tmp_path))[0]
    assert s[:2] == ["corepack", "pnpm"] and "--frozen-lockfile" in s          # pnpm wins


# ---- #63: pristine() refuses to nuke an uncommitted manifest (review P1) ----------------

def _git(wt, *args):
    # -c overrides (highest precedence): a fixed identity + no signing, so these throwaway
    # repos don't inherit the host's enterprise gpgsign=true (no key in the devcontainer).
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t.dev", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_pristine_guard_nonrepo_committed_and_untracked_manifest(tmp_path):
    # (a) non-repo → clear error, deletes nothing.
    plain = tmp_path / "plain"; plain.mkdir()
    (plain / "junk.txt").write_text("x")
    err = rh.pristine(str(plain))
    assert err and "not a git work tree" in err and (plain / "junk.txt").exists()

    # (b) repo with a COMMITTED manifest + untracked junk → cleans junk, keeps manifest, None.
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / "app.py").write_text("x = 1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
    (repo / "untracked_junk.py").write_text("junk\n")
    assert rh.pristine(str(repo)) is None
    assert not (repo / "untracked_junk.py").exists()       # untracked non-manifest cleaned
    assert (repo / "pyproject.toml").exists()              # committed manifest preserved

    # (c) an UNTRACKED manifest would be deleted by clean → refuse, and don't delete it.
    (repo / "setup.cfg").write_text("[metadata]\n")
    err2 = rh.pristine(str(repo))
    assert err2 and "uncommitted manifest" in err2 and "setup.cfg" in err2
    assert (repo / "setup.cfg").exists()                   # guard fired BEFORE any clean


def test_pristine_preserves_oss_caches(tmp_path):
    """The worktree-local caches (.oss-venv/.oss-go/.oss-cargo/node_modules) survive a reset
    so bootstrap stays idempotent across attempts."""
    repo = tmp_path / "r"; repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "go.mod").write_text("module x\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
    for d in (".oss-go", ".oss-cargo", ".oss-venv", "node_modules"):
        (repo / d).mkdir(); (repo / d / "marker").write_text("cached\n")
    assert rh.pristine(str(repo)) is None
    for d in (".oss-go", ".oss-cargo", ".oss-venv", "node_modules"):
        assert (repo / d / "marker").exists()              # cache NOT cleaned


def test_pristine_guard_refuses_manifest_in_untracked_subdir(tmp_path):
    # review P0/P1: `git clean -fdn` COLLAPSES a wholly-untracked dir to one line, so the guard
    # lists untracked files INDIVIDUALLY (git status -uall) — catching a nested manifest at any
    # depth, even under a build/cache-named dir, without following symlinks.
    import os as _os
    import shutil
    repo = tmp_path / "r"; repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")

    # (a) manifest nested in a fresh untracked dir → caught (the dir collapses to one clean line)
    (repo / "crates" / "core").mkdir(parents=True)
    (repo / "crates" / "core" / "Cargo.toml").write_text("[package]\nname = 'core'\n")
    err = rh.pristine(str(repo))
    assert err and "uncommitted manifest" in err and "Cargo.toml" in err
    assert (repo / "crates" / "core" / "Cargo.toml").exists()      # NOT deleted — guard fired
    shutil.rmtree(repo / "crates")

    # (b) review P0: a dir literally named build/dist/target is a LEGAL package dir — a manifest
    # under it must STILL be caught (the old _SCAN_SKIP shortcut skipped it → silent deletion).
    (repo / "build").mkdir(); (repo / "build" / "Cargo.toml").write_text("[package]\nname = 'b'\n")
    err2 = rh.pristine(str(repo))
    assert err2 and "build/Cargo.toml" in err2 and (repo / "build" / "Cargo.toml").exists()
    shutil.rmtree(repo / "build")

    # (c) review P1: an untracked symlink-to-a-tree must NOT be traversed (no false refusal/crash)
    (tmp_path / "outside").mkdir(); (tmp_path / "outside" / "Cargo.toml").write_text("x\n")
    _os.symlink(tmp_path / "outside", repo / "evil")
    assert rh.pristine(str(repo)) is None                          # symlink listed as 1 entry, not walked
    assert (tmp_path / "outside" / "Cargo.toml").exists()          # untouched

    # (d) a pure build-output dir (no manifest) is still cleaned, guard proceeds
    (repo / "target" / "debug").mkdir(parents=True); (repo / "target" / "junk.o").write_text("x")
    assert rh.pristine(str(repo)) is None
    assert not (repo / "target").exists()                          # untracked build dir cleaned


def test_container_run_step_routes_into_container(tmp_path):
    # #62/#63 glue (review P2): the bootstrap run_step must run each step IN the container —
    # image built, worktree mounted at /work, cwd=/work, and the cache-redirect env passed as -e.
    captured = {}

    class _Rec:
        name = "docker"
        def build_image(self, *a, **k):
            captured["built"] = True
        def run(self, spec, log=None):
            captured["spec"] = spec
            return 0

    go = ad.get_adapter("go")
    abs_wt = str(tmp_path)
    step = rh._container_run_step(_Rec(), go, abs_wt, log=lambda *a: None)
    rc, _ = step(["go", "mod", "download"], network="bridge")
    spec = captured["spec"]
    assert rc == 0 and captured.get("built") is True
    assert spec.argv == ["go", "mod", "download"] and spec.cwd == rh._CONTAINER_WORK
    assert spec.image == go.image and (abs_wt, rh._CONTAINER_WORK) in spec.mounts
    assert spec.container_env == {"GOMODCACHE": "/work/.oss-go/mod", "GOCACHE": "/work/.oss-go/build"}
