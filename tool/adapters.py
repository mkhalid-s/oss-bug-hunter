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
        r"tox\.ini|Pipfile|Pipfile\.lock|poetry\.lock|conftest\.py|\.github/)")]
    # container backend (untrusted targets): a small image with pytest preinstalled.
    image = "oss-bug-hunter-py:latest"
    image_dir = str(_ROOT / "tool" / "repro-py")

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        """Copy the reproducer into the repo root where pytest collects it.
        Returns the test SELECTOR (relative path) for test_argv."""
        fn = f"test_repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}.py"
        shutil.copy(test_src, Path(worktree) / fn)
        return fn

    def test_argv(self, selector: str) -> list:
        # LOCAL: sys.executable = the env running the harness (has pytest). A real
        # multi-dep target would get a per-target venv (the documented setup step).
        return [sys.executable, "-m", "pytest", selector, "-q",
                "-p", "no:cacheprovider", "--no-header"]

    def container_argv(self, selector: str) -> list:
        # IN-CONTAINER: the image's own `python` (with pytest), not sys.executable.
        return ["python", "-m", "pytest", selector, "-q",
                "-p", "no:cacheprovider", "--no-header"]

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
    patch_denied = [re.compile(r"(^|/)(go\.mod|go\.sum|\.github/)")]
    _TESTFUNC = re.compile(r"func\s+(Test[A-Za-z0-9_]*)\s*\(")
    image = "oss-bug-hunter-go:latest"
    image_dir = str(_ROOT / "tool" / "repro-go")

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        body = Path(test_src).read_text()
        m = self._TESTFUNC.search(body)            # selector = the Test func name
        fn = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}_test.go"
        (Path(worktree) / fn).write_text(body)
        return m.group(1) if m else "."

    def test_argv(self, selector: str) -> list:
        # `.` (the root package the reproducer is placed in), NOT `./...`: a build
        # error in an UNRELATED package must not mask this reproducer's verdict.
        run = f"^{selector}$" if selector and selector != "." else "."
        return ["go", "test", "-run", run, "-count=1", "-v", "."]

    # `go` is on PATH in the golang image, so the in-container argv is identical.
    def container_argv(self, selector: str) -> list:
        return self.test_argv(selector)

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
    patch_denied = [re.compile(r"(^|/)(Cargo\.toml|Cargo\.lock|\.github/)")]
    image = "oss-bug-hunter-rust:latest"
    image_dir = str(_ROOT / "tool" / "repro-rust")

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        # integration test: tests/<stem>.rs (a separate crate using the public API).
        stem = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}"
        d = Path(worktree) / "tests"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(test_src, d / f"{stem}.rs")
        return stem                                   # selector = the test binary name

    def test_argv(self, selector: str) -> list:
        return ["cargo", "test", "--test", selector]

    def container_argv(self, selector: str) -> list:  # cargo is on PATH in rust image
        return self.test_argv(selector)

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
                               r"pnpm-lock\.yaml|\.github/)")]
    image = "oss-bug-hunter-js:latest"
    image_dir = str(_ROOT / "tool" / "repro-js")

    def place_reproducer(self, worktree: str, test_src: str, name: str) -> str:
        # node's built-in runner; ESM .js (target package.json should be type:module).
        fn = f"repro_{re.sub(r'[^A-Za-z0-9_]', '_', name)}.test.js"
        shutil.copy(test_src, Path(worktree) / fn)
        return fn

    def test_argv(self, selector: str) -> list:
        return ["node", "--test", "--test-reporter=tap", selector]

    def container_argv(self, selector: str) -> list:   # node is on PATH in the image
        return self.test_argv(selector)

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
