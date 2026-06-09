"""Generic sandbox runner (plan §3.3) — the language-agnostic validator core.

For the M0 walking-skeleton spike this carries an inline Java/Maven flavor; the
per-language HarnessAdapter split (adapters/base.py) comes next. What's proven
here: an ec-1 verdict produced through the exec-backend abstraction, with the
Outcome enum, a portable per-worktree lock (G1), and run-repro.sh exit-code
parity.

Exit codes (parity with scripts/run-repro.sh):
  0  PASSED      test passed (bug did NOT reproduce)
  1  FAILED      test failed (bug REPRODUCES)
  2  tooling     NO_TESTS / BUILD_ERROR / DEP_ERROR / TOOL_ERROR (not a signal)
  3  bad args
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exec_backend import RunSpec, select_backend, BackendError  # noqa: E402
import adapters  # noqa: E402


# Outcome + TestVerdict live in adapters.py (the contract home) so there is a
# SINGLE enum identity even when this module is run as __main__ AND imported by
# name — otherwise `rv.outcome is Outcome.FAILED` spuriously fails (the
# two-module-copy trap).
from adapters import Outcome, TestVerdict  # noqa: E402,F401


_SUMMARY_RE = re.compile(
    r"Tests run:\s*(\d+).*?Failures:\s*(\d+).*?Errors:\s*(\d+).*?Skipped:\s*(\d+)")


def parse_surefire(output: str) -> TestVerdict:
    """JUnit/Surefire flavor of HarnessAdapter.parse_result -> Outcome enum."""
    matches = list(_SUMMARY_RE.finditer(output))
    if not matches:
        # no summary -> classify the failure so it's never read as a test signal
        if re.search(r"COMPILATION ERROR|cannot find symbol|package .* does not exist", output):
            oc = Outcome.BUILD_ERROR
        elif re.search(r"Could not resolve|Non-resolvable|Could not transfer|Cannot access",
                       output):
            oc = Outcome.DEP_ERROR
        else:
            oc = Outcome.TOOL_ERROR
        return TestVerdict(oc, 0, 0, 0, 0, "(no Surefire summary)")
    m = matches[-1]
    tr, fa, er, sk = (int(x) for x in m.groups())
    if (tr - sk) <= 0:
        oc = Outcome.NO_TESTS
    elif fa > 0 or er > 0:
        oc = Outcome.FAILED
    else:
        oc = Outcome.PASSED
    return TestVerdict(oc, tr, fa, er, sk, m.group(0))


@contextlib.contextmanager
def worktree_lock(worktree: str, timeout_warn: bool = True):
    """G1: per-WORKTREE exclusive lock (not the process-global pipeline lock), so
    two runs on different worktrees never serialize. Portable fcntl.flock (the
    macOS-absent flock(1) is not used)."""
    lockfile = Path(worktree) / ".repro-worktree.lock"
    fd = os.open(str(lockfile), os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def pristine(worktree: str) -> None:
    """Reset tracked files + remove untracked (preserving .m2). Fail-open."""
    subprocess.run(["git", "-C", worktree, "reset", "--hard", "-q"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", worktree, "clean", "-fdq", "-e", ".m2",
                    "-e", ".repro-worktree.lock", "-e", ".oss-venv",
                    "-e", ".oss-bootstrap.json", "-e", "node_modules"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def place_java_reproducer(worktree: str, test_file: str, fqcn: str) -> None:
    # Java requires the file name to match the public class. The artifact on disk
    # is ec-1.java but the public class is Repro_ec_1, so the file MUST land as
    # <SimpleClassName>.java under the FQCN's package path. (This is what an
    # adapter's place_reproducer() owns; the old run-repro.sh copied by basename
    # and would have failed to compile identically.)
    parts = fqcn.split(".")
    simple, pkg_parts = parts[-1], parts[:-1]
    pkg = Path(worktree).joinpath("src/test/java", *pkg_parts)
    pkg.mkdir(parents=True, exist_ok=True)
    shutil.copy(test_file, pkg / f"{simple}.java")


DEFAULT_CONSOLE_VERSION = "1.11.3"


def _find_console_jar(version: str = DEFAULT_CONSOLE_VERSION):
    # Honor the pinned version on a cache hit — a shared ~/.m2 may already hold a
    # different (possibly incompatible) console-standalone pulled transitively.
    base = os.path.expanduser(
        "~/.m2/repository/org/junit/platform/junit-platform-console-standalone")
    exact = os.path.join(base, version, f"junit-platform-console-standalone-{version}.jar")
    if os.path.exists(exact):
        return exact
    jars = sorted(glob.glob(os.path.join(base, "*", "junit-platform-console-standalone-*.jar")))
    return jars[-1] if jars else None      # newest as a fallback


def parse_console(output: str) -> TestVerdict:
    """JUnit Platform Console Launcher summary -> Outcome enum.

    The launcher prints a box like `[  1 tests failed ]`. Reproduces -> FAILED,
    fixed -> PASSED, nothing collected -> NO_TESTS.
    """
    def n(label: str) -> int:
        m = re.search(rf"\[\s*(\d+)\s+{label}\s*\]", output)
        return int(m.group(1)) if m else 0
    found, ok = n("tests found"), n("tests successful")
    failed, aborted, skipped = n("tests failed"), n("tests aborted"), n("tests skipped")
    if found == 0:
        oc = Outcome.NO_TESTS
    elif failed > 0:
        oc = Outcome.FAILED
    elif ok > 0:
        oc = Outcome.PASSED
    else:
        oc = Outcome.NO_TESTS                  # all aborted/skipped -> nothing ran
    summary = (f"found={found} successful={ok} failed={failed} "
               f"aborted={aborted} skipped={skipped}")
    return TestVerdict(oc, tests_run=found, failures=failed, errors=0,
                       skipped=skipped + aborted, raw_summary=summary)


def _capture(backend, argv, *, cwd, network, env, log, offline,
             mem_limit_mb=None, name=None, image=None, mounts=None):
    """Run a command through the backend, capturing output while still streaming.
    For container backends, `image` + `mounts` are honored (cwd is the in-container
    workdir, e.g. /work)."""
    lines: list = []

    def sink(line: str):
        lines.append(line)
        if log:
            log(line)
    spec = RunSpec(argv=argv, cwd=cwd, network=network, mem_limit_mb=mem_limit_mb,
                   env=env, offline_argv=offline, name=name, image=image,
                   mounts=mounts or [])
    rc = backend.run(spec, log=sink)
    return rc, "\n".join(lines)


# container workdir the worktree is mounted at for adapter (non-Java) runs.
_CONTAINER_WORK = "/work"


def _adapter_run(backend, ad, worktree, selector, *, network, log, name):
    """Run an adapter's test command on the selected backend. local → directly in
    the worktree; container (docker/podman) → build the per-language image, mount
    the worktree at /work, and run the in-container argv there."""
    abs_wt = os.path.abspath(worktree)
    if backend.name == "local":
        # pass the worktree so a Python adapter uses its per-target .oss-venv (M5) when present.
        return _capture(backend, ad.test_argv(selector, abs_wt), cwd=abs_wt, network=network,
                        env=dict(os.environ), log=log, offline=(network == "none"), name=name)
    # untrusted/container path
    log(f"[harness] building sandbox image {ad.image} ({backend.name})…")
    backend.build_image(ad.image_dir, ad.image,
                        build_args={"UID": str(os.getuid()), "GID": str(os.getgid())},
                        log=log)
    return _capture(backend, ad.container_argv(selector, abs_wt), cwd=_CONTAINER_WORK,
                    network=network, env=dict(os.environ), log=log,
                    offline=(network == "none"), name=name,
                    image=ad.image, mounts=[(abs_wt, _CONTAINER_WORK)])


def _compile_and_run_isolated(backend, cwd, fqcn, *, env, mvn_off, offline,
                              network, log, console_version) -> TestVerdict:
    """compile → ensure console jar → build classpath → run ONLY `fqcn`.

    Shared by validate_repro and validate_fix. The reproducer (and, for a fix,
    the applied patch) must already be in place in `cwd`.
    """
    name = Path(cwd).name
    # 1. compile main + test sources
    log("[harness] compiling (mvn test-compile)…")
    rc, out = _capture(backend, ["mvn", "-B", "-q", *mvn_off, "test-compile"],
                       cwd=cwd, network=network, env=env, log=log,
                       offline=offline, name=f"compile-{name}")
    if rc != 0:
        if re.search(r"COMPILATION ERROR|cannot find symbol|package .* does not exist", out):
            return TestVerdict(Outcome.BUILD_ERROR, 0, 0, 0, 0, "test-compile failed")
        if re.search(r"Could not resolve|Non-resolvable|Could not transfer|Cannot access", out):
            return TestVerdict(Outcome.DEP_ERROR, 0, 0, 0, 0, "dependency resolution failed")
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"test-compile rc={rc}")

    # 2. ensure the console launcher jar (one-time fetch; needs network)
    console = _find_console_jar(console_version)
    if not console:
        log("[harness] fetching JUnit Platform Console Launcher…")
        _capture(backend, ["mvn", "-B", "-q", "dependency:get",
                           f"-Dartifact=org.junit.platform:junit-platform-console-standalone:{console_version}"],
                 cwd=cwd, network="bridge", env=env, log=log, offline=False)
        console = _find_console_jar(console_version)
    if not console:
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, "console launcher jar unavailable")

    # 3. assemble classpath (deps + compiled main/test classes). Unique temp file
    # per run (basename collisions could otherwise read a stale classpath), removed
    # in finally.
    fd, cp_file = tempfile.mkstemp(prefix=f"cp-{name}-", suffix=".txt")
    os.close(fd)
    try:
        rc, out = _capture(backend, ["mvn", "-B", "-q", *mvn_off, "dependency:build-classpath",
                                     f"-Dmdep.outputFile={cp_file}"],
                           cwd=cwd, network=network, env=env, log=log,
                           offline=offline, name=f"cp-{name}")
        deps = Path(cp_file).read_text().strip() if os.path.exists(cp_file) else ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(cp_file)
    if rc != 0 or not deps:
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, "build-classpath failed")
    cp = os.pathsep.join([os.path.join(cwd, "target", "classes"),
                          os.path.join(cwd, "target", "test-classes"), deps])

    # 4. run ONLY the target class — isolated, bypasses the pom's <test>
    log(f"[harness] running ONLY {fqcn} via JUnit Console Launcher…")
    rc, out = _capture(backend, ["java", "-Xmx1024m", "-jar", console, "execute",
                                 "-cp", cp, f"--select-class={fqcn}",
                                 "--details=tree", "--disable-banner"],
                       cwd=cwd, network="none", env=env, log=log, offline=True,
                       name=f"run-{name}")
    return parse_console(out)


# R4 containment: reject prompt-injected diffs (symlink/mode/rename/.git) and
# anything outside src/{main,test}/java BEFORE applying on the host.
_BAD_HUNK = re.compile(
    r'^(new mode |old mode |rename (from|to) |(new|deleted) file mode 12|'
    r'diff --git a?/?\.git/|\+\+\+ b?/?\.git/)', re.M)
_ALLOWED_PATH = re.compile(r'^src/(main|test)/java/')


def check_patch_containment(worktree: str, patch_abs: str):
    # `git -C <worktree>` resolves relative paths against the worktree, so the
    # patch path MUST be absolute or the numstat check spuriously "fails".
    patch_abs = os.path.abspath(patch_abs)
    text = Path(patch_abs).read_text(errors="replace")
    if _BAD_HUNK.search(text):
        return False, "disallowed hunk (symlink/mode-change/rename/.git path)"
    p = subprocess.run(["git", "-C", worktree, "apply", "--numstat", patch_abs],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return False, "patch does not apply cleanly"
    bad = [cols[2] for cols in (ln.split("\t") for ln in p.stdout.splitlines())
           if len(cols) >= 3 and not _ALLOWED_PATH.match(cols[2])]
    if bad:
        return False, "touches paths outside src/{main,test}/java: " + ", ".join(bad)
    return True, ""


def _select_direct_backend(trusted: bool, log):
    """The harness compile+console-launcher flow runs commands DIRECTLY in the
    worktree (local execution). Container backends (docker/podman) need an
    in-container flow (build image, mount the worktree, run mvn/java inside, with
    host↔container path translation) that is NOT yet wired (plan §11 TODO). So
    prefer 'local'; if a container backend is selected anyway (e.g. an UNTRUSTED
    target on a docker host), fail with a clear message instead of the opaque
    'container backend requires spec.image'."""
    try:
        backend = select_backend(trusted=trusted, prefer="local")
    except BackendError as e:
        return None, TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
    if backend.name != "local":
        return None, TestVerdict(
            Outcome.TOOL_ERROR, 0, 0, 0, 0,
            f"harness needs the local backend but selected '{backend.name}'; "
            "container execution is not yet wired — use a trusted target (local).")
    return backend, None


def _select_adapter_backend(trusted: bool, log):
    """Adapter (non-Java) path: trusted -> local (fast, proven); untrusted ->
    docker/podman (isolated container). Unlike the Java console-launcher path, the
    adapter test commands are relative (cwd=/work) and containerize cleanly."""
    try:
        backend = select_backend(trusted=trusted, prefer="local" if trusted else None)
    except BackendError as e:
        return None, TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
    return backend, None


def _contained_generic(worktree: str, patch_abs: str, allowed, denied):
    """Per-language patch containment: reject symlink/mode/rename/.git hunks, and
    require every modified path to match an allowed glob and no denied glob."""
    patch_abs = os.path.abspath(patch_abs)
    if _BAD_HUNK.search(Path(patch_abs).read_text(errors="replace")):
        return False, "disallowed hunk (symlink/mode-change/rename/.git path)"
    p = subprocess.run(["git", "-C", worktree, "apply", "--numstat", patch_abs],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return False, "patch does not apply cleanly"
    for ln in p.stdout.splitlines():
        cols = ln.split("\t")
        if len(cols) < 3:
            continue
        path = cols[2]
        if any(d.search(path) for d in denied):
            return False, f"touches a denied path: {path}"
        if not any(a.search(path) for a in allowed):
            return False, f"touches a non-allowed path: {path}"
    return True, ""


def _container_run_step(backend, ad, abs_wt, *, log):
    """A bootstrap run_step that runs each step INSIDE the per-language container (image
    built once; worktree mounted at /work; cwd=/work; network=bridge). For untrusted
    targets, so install commands (which execute target code) never touch the host (#62)."""
    backend.build_image(ad.image_dir, ad.image,
                        build_args={"UID": str(os.getuid()), "GID": str(os.getgid())}, log=log)

    def run_step(argv, *, cwd=None, network="bridge"):       # cwd ignored → always /work
        return _capture(backend, argv, cwd=_CONTAINER_WORK, network=network,
                        env=dict(os.environ), log=log, offline=(network == "none"),
                        name="bootstrap", image=ad.image, mounts=[(abs_wt, _CONTAINER_WORK)])

    return run_step


def _maybe_bootstrap(ad, worktree, backend, *, log):
    """M5: resolve the target's deps once (idempotent) before the first test. Returns a
    DEP_ERROR verdict on refusal/failure, else None. Single-file targets are a no-op;
    pristine() preserves the resulting .oss-venv/node_modules.

    TRUST GATE (review P0): bootstrap runs install commands that EXECUTE target code
    (npm pre/postinstall, pip/PEP517 backends). So: TRUSTED → local backend → run on the
    host. UNTRUSTED → container backend → run IN the container if the deps land in the
    worktree (#62: Python .oss-venv / JS node_modules, shared via /work); otherwise
    (cache-based langs: go/rust — caches live outside the worktree and don't survive
    between the bootstrap and test containers) FAIL CLOSED until a shared cache mount is
    wired. Never run an untrusted target's installs on the host."""
    try:
        import bootstrap as _bs
        if not _bs.needs_bootstrap(worktree, ad):
            return None
        if backend.name == "local":
            r = _bs.bootstrap(worktree, ad, network="bridge", log=log)
        elif getattr(ad, "bootstrap_in_worktree", False):
            abs_wt = os.path.abspath(worktree)
            r = _bs.bootstrap(worktree, ad, network="bridge", log=log,
                              run_step=_container_run_step(backend, ad, abs_wt, log=log))
        else:
            return TestVerdict(Outcome.DEP_ERROR, 0, 0, 0, 0,
                               f"untrusted {getattr(ad, 'language', '?')} multi-dep target: "
                               "in-container bootstrap needs a shared dependency-cache mount "
                               "(not wired) — refusing to run installs on the host")
        if not r.get("ok"):
            return TestVerdict(Outcome.DEP_ERROR, 0, 0, 0, 0,
                               f"env-bootstrap failed at {r.get('step')}")
    except Exception as e:                       # review P2: missing toolchain etc. → DEP_ERROR, not a silent skip
        log(f"[harness] bootstrap error: {e}")
        return TestVerdict(Outcome.DEP_ERROR, 0, 0, 0, 0, f"env-bootstrap error: {e}")
    return None


def _adapter_validate_repro(backend, worktree, test_file, *, lang, network, log):
    ad = adapters.get_adapter(lang)
    if ad is None:
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"no adapter for language '{lang}'")
    cwd = os.path.abspath(worktree)
    with worktree_lock(worktree):
        pristine(worktree)
        berr = _maybe_bootstrap(ad, worktree, backend, log=log)
        if berr is not None:
            return berr
        try:
            selector = ad.place_reproducer(worktree, test_file, Path(test_file).stem)
            log(f"[harness] {lang}: running {selector}")
            _, out = _adapter_run(backend, ad, worktree, selector, network=network,
                                  log=log, name=f"repro-{Path(worktree).name}")
            return ad.parse_result(out)
        except (BackendError, OSError) as e:
            log(f"[harness] error: {e}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
        finally:
            pristine(worktree)


def _adapter_validate_fix(backend, worktree, test_file, patch, *, lang, network, log):
    ad = adapters.get_adapter(lang)
    if ad is None:
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"no adapter for language '{lang}'")
    cwd = os.path.abspath(worktree)
    patch_abs = os.path.abspath(patch)
    with worktree_lock(worktree):
        pristine(worktree)
        berr = _maybe_bootstrap(ad, worktree, backend, log=log)
        if berr is not None:
            return berr
        ok, reason = _contained_generic(worktree, patch_abs, ad.patch_allowed, ad.patch_denied)
        if not ok:
            log(f"[harness] patch REJECTED: {reason}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"patch rejected: {reason}")
        try:
            ap = subprocess.run(["git", "-C", worktree, "apply", patch_abs],
                                capture_output=True, text=True)
            if ap.returncode != 0:
                return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0,
                                   f"git apply failed: {ap.stderr.strip()[:200]}")
            selector = ad.place_reproducer(worktree, test_file, Path(test_file).stem)
            log(f"[harness] {lang}: running {selector} after fix")
            _, out = _adapter_run(backend, ad, worktree, selector, network=network,
                                  log=log, name=f"fix-{Path(worktree).name}")
            return ad.parse_result(out)
        except (BackendError, OSError) as e:
            log(f"[harness] error: {e}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
        finally:
            pristine(worktree)


def validate_repro(worktree: str, fqcn: str, test_file: str, *,
                   trusted: bool, network: str, log=print,
                   console_version: str = DEFAULT_CONSOLE_VERSION,
                   lang: str = "java") -> TestVerdict:
    """Validate a single reproducer in ISOLATION.

    jackson-databind (like many mature projects) pins surefire to a JUnit Platform
    Suite in the pom (`<test>PrimarySuite</test>`), which OVERRIDES `mvn -Dtest=`
    and runs the whole suite — so a single reproducer never runs and the result
    falsely PASSES. We instead compile, then run ONLY the target class via the
    JUnit Platform Console Launcher, bypassing the pom's surefire config entirely.
    """
    if not Path(worktree).is_dir():
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"worktree not found: {worktree}")
    if lang != "java":
        backend, err = _select_adapter_backend(trusted, log)
        if err:
            log(f"[harness] {err.raw_summary}")
            return err
        log(f"[harness] backend={backend.name} network={network} trusted={trusted} lang={lang}")
        return _adapter_validate_repro(backend, worktree, test_file, lang=lang,
                                       network=network, log=log)
    backend, err = _select_direct_backend(trusted, log)
    if err:
        log(f"[harness] {err.raw_summary}")
        return err
    log(f"[harness] backend={backend.name} network={network} trusted={trusted}")
    cwd = os.path.abspath(worktree)
    env = dict(os.environ)
    # JVM memory bounded via -Xmx (not prlimit --as, which kills JVM startup).
    env["MAVEN_OPTS"] = ("-Xmx1536m -XX:MaxMetaspaceSize=512m "
                         "-XX:CompressedClassSpaceSize=128m "
                         "-XX:ReservedCodeCacheSize=256m")
    mvn_off = ["-o"] if network == "none" else []
    offline = network == "none"

    with worktree_lock(worktree):
        pristine(worktree)
        place_java_reproducer(worktree, test_file, fqcn)
        try:
            return _compile_and_run_isolated(
                backend, cwd, fqcn, env=env, mvn_off=mvn_off, offline=offline,
                network=network, log=log, console_version=console_version)
        except (BackendError, OSError) as e:
            log(f"[harness] error: {e}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
        finally:
            pristine(worktree)


def validate_fix(worktree: str, fqcn: str, test_file: str, patch: str, *,
                 trusted: bool, network: str, log=print,
                 console_version: str = DEFAULT_CONSOLE_VERSION,
                 lang: str = "java") -> TestVerdict:
    """Apply a candidate fix patch (contained), then re-run the isolated
    reproducer. PASSED => the fix works; FAILED => still broken. The worktree is
    restored to pristine afterward no matter what."""
    if not Path(worktree).is_dir():
        return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"worktree not found: {worktree}")
    if lang != "java":
        backend, err = _select_adapter_backend(trusted, log)
        if err:
            log(f"[harness] {err.raw_summary}")
            return err
        log(f"[harness] backend={backend.name} network={network} trusted={trusted} lang={lang} (fix)")
        return _adapter_validate_fix(backend, worktree, test_file, patch, lang=lang,
                                     network=network, log=log)
    backend, err = _select_direct_backend(trusted, log)
    if err:
        log(f"[harness] {err.raw_summary}")
        return err
    log(f"[harness] backend={backend.name} network={network} trusted={trusted} (fix)")
    cwd = os.path.abspath(worktree)
    patch_abs = os.path.abspath(patch)
    env = dict(os.environ)
    env["MAVEN_OPTS"] = ("-Xmx1536m -XX:MaxMetaspaceSize=512m "
                         "-XX:CompressedClassSpaceSize=128m "
                         "-XX:ReservedCodeCacheSize=256m")
    mvn_off = ["-o"] if network == "none" else []
    offline = network == "none"

    with worktree_lock(worktree):
        pristine(worktree)
        ok, reason = check_patch_containment(worktree, patch_abs)
        if not ok:
            log(f"[harness] patch REJECTED: {reason}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, f"patch rejected: {reason}")
        try:
            ap = subprocess.run(["git", "-C", worktree, "apply", patch_abs],
                                capture_output=True, text=True)
            if ap.returncode != 0:
                return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0,
                                   f"git apply failed: {ap.stderr.strip()[:200]}")
            log(f"[harness] applied {patch_abs}")
            place_java_reproducer(worktree, test_file, fqcn)
            return _compile_and_run_isolated(
                backend, cwd, fqcn, env=env, mvn_off=mvn_off, offline=offline,
                network=network, log=log, console_version=console_version)
        except (BackendError, OSError) as e:
            log(f"[harness] error: {e}")
            return TestVerdict(Outcome.TOOL_ERROR, 0, 0, 0, 0, str(e))
        finally:
            pristine(worktree)


@dataclass
class OrchestrationResult:
    status: str                       # validated | not-reproduced | fix-failed | inconclusive
    reproduced: bool
    fixed: bool
    attempts: int
    repro: TestVerdict
    fix: "TestVerdict | None"
    detail: str

    def exit_code(self) -> int:
        return {"validated": 0, "fix-failed": 1,
                "not-reproduced": 2, "inconclusive": 3}.get(self.status, 3)


def orchestrate(worktree: str, fqcn: str, test_file: str, patch: str, *,
                trusted: bool, network: str, log=print, fix_provider=None,
                max_retries: int = 0,
                console_version: str = DEFAULT_CONSOLE_VERSION,
                lang: str = "java") -> OrchestrationResult:
    """Self-correcting loop on the validated primitives: reproduce → fix
    (→ retry-with-feedback).

    Invariant (load-bearing): the AI only *proposes* patches; the non-AI
    validators (validate_repro / validate_fix) *dispose*. `fix_provider(feedback,
    attempt) -> patch_path` supplies a revised patch when a fix attempt fails;
    without it (or once max_retries is exhausted) the loop stops.
    """
    log("[orchestrate] step 1/2: does the bug reproduce on HEAD?")
    rv = validate_repro(worktree, fqcn, test_file, trusted=trusted, network=network,
                        log=log, console_version=console_version, lang=lang)
    if rv.outcome is Outcome.PASSED:
        log("[orchestrate] reproducer did NOT trigger the bug — nothing to fix.")
        return OrchestrationResult("not-reproduced", False, False, 0, rv, None,
                                   "reproducer passed on HEAD (bug absent / repro too weak)")
    if rv.outcome is not Outcome.FAILED:
        log(f"[orchestrate] reproducer inconclusive: {rv.outcome.value}")
        return OrchestrationResult("inconclusive", False, False, 0, rv, None,
                                   f"reproducer inconclusive: {rv.outcome.value} ({rv.raw_summary})")
    log("[orchestrate] bug REPRODUCES ✓ — validating the fix")

    current_patch, fv, attempt = patch, None, 0
    while attempt < max_retries + 1:
        attempt += 1
        log(f"[orchestrate] step 2/2: fix attempt {attempt}")
        fv = validate_fix(worktree, fqcn, test_file, current_patch, trusted=trusted,
                          network=network, log=log, console_version=console_version, lang=lang)
        if fv.outcome is Outcome.PASSED:
            log(f"[orchestrate] VALIDATED ✓ reproduces AND fix works (attempt {attempt})")
            return OrchestrationResult("validated", True, True, attempt, rv, fv,
                                       "reproduces and the fix makes the test pass")
        log(f"[orchestrate] fix attempt {attempt} did not pass ({fv.outcome.value})")
        if fix_provider is None or attempt >= max_retries + 1:
            break
        log("[orchestrate] requesting a revised patch from fix_provider…")
        new_patch = fix_provider(feedback=fv.raw_summary, attempt=attempt)
        if not new_patch:
            log("[orchestrate] no revised patch produced — stopping")
            break
        current_patch = new_patch
    return OrchestrationResult(
        "fix-failed", True, False, attempt, rv, fv,
        f"fix did not pass after {attempt} attempt(s) "
        f"(last: {fv.outcome.value if fv else 'n/a'})")


def _print_verdict(v: TestVerdict) -> int:
    print(f"\n[harness] VERDICT: outcome={v.outcome.value} "
          f"(Tests run={v.tests_run}, Failures={v.failures}, Errors={v.errors}, "
          f"Skipped={v.skipped}) [{v.raw_summary}] -> exit {v.exit_code()}",
          file=sys.stderr)
    return v.exit_code()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate-repro", "validate-fix", "orchestrate"):
        p = sub.add_parser(name)
        p.add_argument("worktree")
        p.add_argument("fqcn")
        p.add_argument("test_file")
        if name in ("validate-fix", "orchestrate"):
            p.add_argument("patch")
        if name == "orchestrate":
            p.add_argument("--max-retries", type=int, default=0,
                           help="fix retries via the LLM fix-builder (needs --finding)")
            p.add_argument("--finding", default="",
                           help="finding YAML scaffold; enables the LLM fix-builder "
                                "as the retry fix_provider")
        p.add_argument("--trusted", action="store_true")
        p.add_argument("--network", default="bridge", choices=["none", "bridge"])
        p.add_argument("--lang", default="java",
                       choices=["java", "python", "go", "rust", "javascript"],
                       help="target language (selects the harness adapter)")
    args = ap.parse_args(argv)

    if not Path(args.worktree).is_dir():
        print(f"ERROR: worktree not found: {args.worktree}", file=sys.stderr)
        return 3
    if not Path(args.test_file).is_file():
        print(f"ERROR: test file not found: {args.test_file}", file=sys.stderr)
        return 3

    if args.cmd == "validate-repro":
        return _print_verdict(validate_repro(
            args.worktree, args.fqcn, args.test_file,
            trusted=args.trusted, network=args.network, lang=args.lang))
    if args.cmd == "validate-fix":
        if not Path(args.patch).is_file():
            print(f"ERROR: patch not found: {args.patch}", file=sys.stderr)
            return 3
        return _print_verdict(validate_fix(
            args.worktree, args.fqcn, args.test_file, args.patch,
            trusted=args.trusted, network=args.network, lang=args.lang))
    if args.cmd == "orchestrate":
        if not Path(args.patch).is_file():
            print(f"ERROR: patch not found: {args.patch}", file=sys.stderr)
            return 3
        provider = None
        if args.finding:
            if not Path(args.finding).is_file():
                print(f"ERROR: finding not found: {args.finding}", file=sys.stderr)
                return 3
            import yaml
            import llm_fix_provider
            scaffold = yaml.safe_load(Path(args.finding).read_text()) or {}
            provider = llm_fix_provider.make_llm_fix_provider(
                scaffold, Path(args.test_file).read_text(),
                str(Path(args.patch).parent))
        res = orchestrate(args.worktree, args.fqcn, args.test_file, args.patch,
                          trusted=args.trusted, network=args.network,
                          fix_provider=provider, max_retries=args.max_retries,
                          lang=args.lang)
        print(f"\n[orchestrate] RESULT: status={res.status} reproduced={res.reproduced} "
              f"fixed={res.fixed} attempts={res.attempts} -> exit {res.exit_code()}\n"
              f"  {res.detail}", file=sys.stderr)
        return res.exit_code()
    return 3


if __name__ == "__main__":
    sys.exit(main())
