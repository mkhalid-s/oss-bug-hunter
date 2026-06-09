"""Language adapters (plan §3.2). Each adapter knows how to place a reproducer,
run ONLY that test, parse the result into the shared Outcome enum, and declare
patch-containment globs. The generic runner (run_harness) drives them; Java keeps
its bespoke console-launcher path inline in run_harness for now.

Outcome/TestVerdict are the shared verdict contract and live HERE (run_harness
imports them) so there is a single enum identity even when run_harness runs as
__main__ and is also imported by name.
"""
from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


class Outcome(Enum):
    PASSED = "passed"
    FAILED = "failed"
    NO_TESTS = "no_tests"
    BUILD_ERROR = "build_error"
    DEP_ERROR = "dep_error"
    TOOL_ERROR = "tool_error"


@dataclass
class TestVerdict:
    outcome: Outcome
    tests_run: int
    failures: int
    errors: int
    skipped: int
    raw_summary: str

    def exit_code(self) -> int:                # parity with run-repro.sh
        if self.outcome is Outcome.PASSED:
            return 0
        if self.outcome is Outcome.FAILED:
            return 1
        return 2


class PythonPytestAdapter:
    language = "python"
    # containment: a fix may touch any .py, but never dependency/CI manifests
    # (blocks a prompt-injected dependency-pinning attack).
    patch_allowed = [re.compile(r"\.py$")]
    patch_denied = [re.compile(
        r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|"
        r"tox\.ini|Pipfile|Pipfile\.lock|poetry\.lock|conftest\.py|\.github/|"
        r"\.oss-venv/|\.oss-bootstrap\.json)")]
    # container backend (untrusted targets): a small image with pytest preinstalled.
    image = "oss-bug-hunter-py:latest"
    image_dir = str(_ROOT / "tool" / "repro-py")
    VENV_DIR = ".oss-venv"                               # M5: per-target uv venv
    MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
                 "poetry.lock", "Pipfile.lock")   # lockfiles too → cache invalidates on a deps bump
    bootstrap_in_worktree = True   # the .oss-venv lives in the worktree → shareable into a container via /work (#62)

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        """Copy the reproducer into the repo root where pytest collects it.
        Returns the test SELECTOR (relative path) for test_argv."""
        fn = f"test_repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}.py"
        shutil.copy(test_src, Path(worktree) / fn)
        return fn

    def venv_python(self, worktree) -> str | None:
        p = Path(worktree) / self.VENV_DIR / "bin" / "python"
        return str(p) if p.exists() else None

    def bootstrap_steps(self, worktree) -> list:
        """M5: uv-based per-target venv + (editable | requirements) install. Returns []
        when no manifest is present (our single-file synthetic targets need nothing).
        uv is used because Python 3.13 venvs here lack setuptools/ensurepip."""
        # the per-target venv needs the target's deps AND pytest (the test runner).
        # CWD-RELATIVE paths (".oss-venv"): steps run with cwd=worktree locally OR
        # cwd=/work in a container, so a bare relative path is correct in BOTH (the
        # venv lands in the worktree, shared into the container via the /work mount).
        # NOTE: must be relative-to-cwd, not worktree-prefixed (that double-nests).
        wt = Path(worktree)
        venv, py = self.VENV_DIR, f"{self.VENV_DIR}/bin/python"
        if any((wt / m).exists() for m in ("pyproject.toml", "setup.py", "setup.cfg")):
            return [["uv", "venv", "--clear", venv],
                    ["uv", "pip", "install", "-e", ".", "pytest", "--python", py]]
        reqs = sorted(p.name for p in wt.glob("requirements*.txt"))
        if reqs:
            return [["uv", "venv", "--clear", venv]] + \
                   [["uv", "pip", "install", "-r", r, "--python", py] for r in reqs] + \
                   [["uv", "pip", "install", "pytest", "--python", py]]
        return []

    def test_argv(self, selector: str, worktree=None) -> list:
        # use the per-target bootstrap venv (with the target's deps) when present;
        # else sys.executable (the harness env, fine for single-file synthetic targets).
        py = (self.venv_python(worktree) if worktree else None) or sys.executable
        return [py, "-m", "pytest", selector, "-q",
                "-p", "no:cacheprovider", "--no-header"]

    def container_argv(self, selector: str, worktree=None) -> list:
        # IN-CONTAINER (cwd=/work): use the in-worktree bootstrapped venv (mounted at
        # /work/.oss-venv) when present, else the image's own python (#62).
        py = (f"{self.VENV_DIR}/bin/python"
              if (worktree and self.venv_python(worktree)) else "python")
        return [py, "-m", "pytest", selector, "-q",
                "-p", "no:cacheprovider", "--no-header"]

    def container_cache_env(self, work: str) -> dict:
        return {}     # the in-worktree .oss-venv is found via its python path; no env needed

    def parse_result(self, output: str):
        # Parse ONLY pytest's summary line (the last line that carries a count),
        # so a stray "3 errors" printed by a test/library can't flip the verdict.
        summary = ""
        for line in output.splitlines():
            if re.search(r"\b\d+ (passed|failed|errors?|skipped|xfailed|xpassed)\b", line):
                summary = line

        def n(word: str) -> int:
            m = re.search(rf"(\d+) {word}", summary)
            return int(m.group(1)) if m else 0

        passed, failed, errors, skipped = n("passed"), n("failed"), n("error"), n("skipped")
        if re.search(r"ERROR collecting|ImportError|ModuleNotFoundError|SyntaxError", output) \
                and passed == 0 and failed == 0:
            oc = Outcome.BUILD_ERROR
        elif failed > 0 or errors > 0:
            oc = Outcome.FAILED
        elif passed > 0:
            oc = Outcome.PASSED
        else:
            oc = Outcome.NO_TESTS                  # "no tests ran"
        return TestVerdict(oc, tests_run=passed + failed + errors, failures=failed,
                           errors=errors, skipped=skipped,
                           raw_summary=f"passed={passed} failed={failed} "
                                       f"errors={errors} skipped={skipped}")


class GoTestAdapter:
    language = "go"
    # a fix may edit any .go file, but never the module manifest or CI config.
    patch_allowed = [re.compile(r"\.go$")]
    patch_denied = [re.compile(r"(^|/)(go\.mod|go\.sum|\.github/|\.oss-go/|\.oss-bootstrap\.json)")]
    _TESTFUNC = re.compile(r"func\s+(Test[A-Za-z0-9_]*)\s*\(")
    image = "oss-bug-hunter-go:latest"
    image_dir = str(_ROOT / "tool" / "repro-go")
    MANIFESTS = ("go.mod", "go.sum")
    CACHE_DIR = ".oss-go"          # module+build cache, redirected into the worktree (#63)
    bootstrap_in_worktree = True   # /work/.oss-go shared bootstrap↔test container (no host ~/go)

    def bootstrap_steps(self, worktree) -> list:
        return [["go", "mod", "download"]] if (Path(worktree) / "go.mod").exists() else []

    def container_cache_env(self, work: str) -> dict:
        # redirect GOMODCACHE/GOCACHE under the /work mount so `go mod download` (bootstrap
        # container) and `go test` (test container) share one cache, isolated from the host's
        # ~/go. Absolute in-container paths — go rejects a relative GOMODCACHE.
        base = f"{work}/{self.CACHE_DIR}"
        return {"GOMODCACHE": f"{base}/mod", "GOCACHE": f"{base}/build"}

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        body = Path(test_src).read_text()
        m = self._TESTFUNC.search(body)            # selector = the Test func name
        fn = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}_test.go"
        (Path(worktree) / fn).write_text(body)
        return m.group(1) if m else "."

    def test_argv(self, selector: str, worktree=None) -> list:
        # `.` (the root package the reproducer is placed in), NOT `./...`: a build
        # error in an UNRELATED package must not mask this reproducer's verdict.
        run = f"^{selector}$" if selector and selector != "." else "."
        return ["go", "test", "-run", run, "-count=1", "-v", "."]

    # `go` is on PATH in the golang image, so the in-container argv is identical.
    def container_argv(self, selector: str, worktree=None) -> list:
        return self.test_argv(selector, worktree)

    def parse_result(self, output: str):
        passes = len(re.findall(r"--- PASS:", output))
        fails = len(re.findall(r"--- FAIL:", output))
        if fails > 0:
            oc = Outcome.FAILED
        elif passes > 0:
            oc = Outcome.PASSED
        elif re.search(r"\[build failed\]|undefined:|^# ", output, re.M):
            oc = Outcome.BUILD_ERROR
        elif re.search(r"^panic:|\bexit status [1-9]", output, re.M):
            oc = Outcome.FAILED                    # process-level panic / non-zero
        else:
            oc = Outcome.NO_TESTS                  # "no test files" / nothing ran
        return TestVerdict(oc, tests_run=passes + fails, failures=fails, errors=0,
                           skipped=0, raw_summary=f"pass={passes} fail={fails}")


class RustCargoAdapter:
    language = "rust"
    # a fix may edit any .rs; never the manifest/lockfile or CI config.
    patch_allowed = [re.compile(r"\.rs$")]
    patch_denied = [re.compile(r"(^|/)(Cargo\.toml|Cargo\.lock|\.github/|\.oss-cargo/|\.oss-bootstrap\.json)")]
    image = "oss-bug-hunter-rust:latest"
    image_dir = str(_ROOT / "tool" / "repro-rust")
    MANIFESTS = ("Cargo.toml", "Cargo.lock")
    CACHE_DIR = ".oss-cargo"       # CARGO_HOME, redirected into the worktree (#63)
    bootstrap_in_worktree = True   # /work/.oss-cargo shared bootstrap↔test container (no host ~/.cargo)

    def bootstrap_steps(self, worktree) -> list:
        return [["cargo", "fetch"]] if (Path(worktree) / "Cargo.toml").exists() else []

    def container_cache_env(self, work: str) -> dict:
        # CARGO_HOME under /work so `cargo fetch` (bootstrap) and `cargo test` (test) share the
        # registry cache across containers, isolated from the host's ~/.cargo.
        return {"CARGO_HOME": f"{work}/{self.CACHE_DIR}"}

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        # integration test: tests/<stem>.rs (a separate crate using the public API).
        stem = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}"
        d = Path(worktree) / "tests"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(test_src, d / f"{stem}.rs")
        return stem                                   # selector = the test binary name

    def test_argv(self, selector: str, worktree=None) -> list:
        return ["cargo", "test", "--test", selector]

    def container_argv(self, selector: str, worktree=None) -> list:  # cargo is on PATH in rust image
        return self.test_argv(selector, worktree)

    def parse_result(self, output: str):
        passed = sum(int(m) for m in re.findall(r"(\d+) passed", output))
        failed = sum(int(m) for m in re.findall(r"(\d+) failed", output))
        if failed > 0:
            oc = Outcome.FAILED
        elif passed > 0:
            oc = Outcome.PASSED
        elif re.search(r"error\[E\d+\]|could not compile|error: linking with", output):
            oc = Outcome.BUILD_ERROR
        elif re.search(r"^error: test failed|^thread '.*' panicked|"
                       r"process didn't exit successfully", output, re.M):
            oc = Outcome.FAILED                    # panic/abort with no summary line
        else:
            oc = Outcome.NO_TESTS
        return TestVerdict(oc, tests_run=passed + failed, failures=failed, errors=0,
                           skipped=0, raw_summary=f"passed={passed} failed={failed}")


class JsNodeTestAdapter:
    language = "javascript"
    # a fix may edit any JS/TS source; never the manifest/lockfiles or CI config.
    patch_allowed = [re.compile(r"\.(m?js|cjs|jsx|ts|tsx)$")]
    patch_denied = [re.compile(r"(^|/)(package\.json|package-lock\.json|yarn\.lock|"
                               r"pnpm-lock\.yaml|node_modules/|\.github/)")]
    image = "oss-bug-hunter-js:latest"
    image_dir = str(_ROOT / "tool" / "repro-js")
    MANIFESTS = ("package-lock.json", "package.json", "yarn.lock", "pnpm-lock.yaml")
    bootstrap_in_worktree = True   # node_modules lives in the worktree → shareable via /work (node auto-resolves it) (#62)

    def bootstrap_steps(self, worktree) -> list:
        # match the lockfile to its package manager. corepack ships with node ≥16.10, so
        # `corepack yarn|pnpm` runs in the image and on a modern host with no global install.
        # Order: pnpm > yarn > npm-lock > bare package.json.
        wt = Path(worktree)
        if (wt / "pnpm-lock.yaml").exists():
            return [["corepack", "pnpm", "install", "--frozen-lockfile"]]
        if (wt / "yarn.lock").exists():
            return [["corepack", "yarn", "install"]]   # frozen flag differs v1/berry → omit
        if (wt / "package-lock.json").exists():
            return [["npm", "ci"]]
        return [["npm", "install"]] if (wt / "package.json").exists() else []

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        # node's built-in runner; ESM .js (target package.json should be type:module).
        fn = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}.test.js"
        shutil.copy(test_src, Path(worktree) / fn)
        return fn

    def test_argv(self, selector: str, worktree=None) -> list:
        return ["node", "--test", "--test-reporter=tap", selector]

    def container_argv(self, selector: str, worktree=None) -> list:   # node is on PATH in the image
        return self.test_argv(selector, worktree)

    def container_cache_env(self, work: str) -> dict:
        return {}     # node_modules lives in the worktree (/work); node auto-resolves it

    def parse_result(self, output: str):
        def n(label: str) -> int:
            m = re.search(rf"# {label} (\d+)", output)
            return int(m.group(1)) if m else 0
        passed, failed = n("pass"), n("fail")
        if failed > 0:
            oc = Outcome.FAILED
        elif passed > 0:
            oc = Outcome.PASSED
        elif re.search(r"Cannot find module|SyntaxError|ERR_MODULE_NOT_FOUND", output):
            oc = Outcome.BUILD_ERROR
        else:
            oc = Outcome.NO_TESTS
        return TestVerdict(oc, tests_run=passed + failed, failures=failed, errors=0,
                           skipped=0, raw_summary=f"pass={passed} fail={failed}")


_ADAPTERS = {"python": PythonPytestAdapter(), "go": GoTestAdapter(),
             "rust": RustCargoAdapter(), "javascript": JsNodeTestAdapter()}


def get_adapter(language: str):
    return _ADAPTERS.get(language)


def all_manifests() -> set:
    """Every manifest filename across all adapters."""
    return {m for ad in _ADAPTERS.values() for m in getattr(ad, "MANIFESTS", ())}


# lockfiles are DERIVED (bootstrap regenerates them: cargo fetch / go mod / npm ci), so it is
# safe for pristine() to clean an untracked one — that was always the behaviour. Only the
# source-of-truth manifests below must survive a reset; those are what pristine() refuses to
# delete (#63 / review P1), since losing one makes bootstrap behave as if there were no manifest.
_LOCKFILES = frozenset({"poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock",
                        "package-lock.json", "yarn.lock", "pnpm-lock.yaml"})


def primary_manifests() -> set:
    """Source-of-truth manifests (excludes derived lockfiles) — the set pristine() guards."""
    return all_manifests() - _LOCKFILES
