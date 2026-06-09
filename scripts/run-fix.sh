#!/usr/bin/env bash
# LEGACY (Cell #1 / Day-3 batch path). The converged multi-language orchestrator
# does NOT use this script — it validates fixes through tool/run_harness.py +
# tool/exec_backend.py. Kept for the jackson-only Day-3 `run-fixes` flow and as
# the documented Docker primitive. See docs/MULTI-LANGUAGE-VISION.md §11.
#
# Apply a candidate fix patch to a jackson-databind worktree, re-run the bug's
# JUnit reproducer in the Docker sandbox, and report whether the fix makes the
# reproducer PASS. The worktree's tracked files are restored afterward (the
# target clone is left as it was found).
#
# Pairs with run-repro.sh: the reproducer "passes" (gate) when the JUnit test
# FAILS on buggy HEAD; the fix "passes" (gate) when that same test now PASSES.
#
# Usage:
#   scripts/run-fix.sh <worktree-dir> <patch-file> <test-class-fqcn> [test-file]
#
# Exit codes:
#   0  reproducer PASSES after the fix    (fix works)
#   1  reproducer still FAILS after fix   (fix doesn't work)
#   2  docker / sandbox error             (not a fix signal)
#   3  patch did not apply / bad args
#   4  no test ran after patch            (fix likely broke compilation)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPRO_DIR="${PROJECT_ROOT}/tool/repro"
# shellcheck source=_repro_decide.sh
source "$(dirname "${BASH_SOURCE[0]}")/_repro_decide.sh"

usage() { sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# //;s/^#//'; }
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
if [[ $# -lt 3 ]]; then
  echo "ERROR: need <worktree-dir> <patch-file> <test-class-fqcn>" >&2
  usage >&2; exit 3
fi

WORKTREE="$1"; PATCH="$2"; TEST_FQCN="$3"; TEST_FILE="${4:-}"
[[ -d "${WORKTREE}" ]] || { echo "ERROR: worktree not found: ${WORKTREE}" >&2; exit 3; }
[[ -f "${PATCH}" ]]    || { echo "ERROR: patch not found: ${PATCH}" >&2; exit 3; }

repro_network_validate                                       # R6: refuse host net by default
repro_acquire_lock "${PROJECT_ROOT}/.repro-sandbox.lock"     # R7: serialize worktree use
repro_pristine "${WORKTREE}"                                 # R12: clean tree before --check/apply

abs_patch="$(cd "$(dirname "${PATCH}")" && pwd)/$(basename "${PATCH}")"

# R4: CONTAINMENT — the patch is agent-authored from (untrusted) finding text and
# is applied on the HOST, so confine what it can touch BEFORE we spin Docker.
# Reject symlink/mode/rename hunks and any .git target outright, then require
# every modified path to live under src/{main,test}/java. This blocks a
# prompt-injected diff from reaching pom.xml/.mvn/.git/hooks or escaping via a
# symlink. (Done pre-preflight so it's cheap and testable without a daemon.)
if grep -qE '^(new mode |old mode |rename (from|to) |(new|deleted) file mode 12|diff --git a?/?\.git/|\+\+\+ b?/?\.git/)' "${abs_patch}"; then
  echo "[fix] ERROR patch contains a disallowed hunk (symlink / mode-change / rename / .git path)" >&2
  exit 3
fi
if ! git -C "${WORKTREE}" apply --check "${abs_patch}" 2>/dev/null; then
  echo "[fix] ERROR patch does not apply cleanly to ${WORKTREE}" >&2
  exit 3
fi
bad_paths="$(git -C "${WORKTREE}" apply --numstat "${abs_patch}" 2>/dev/null \
             | awk '{print $3}' | grep -vE '^src/(main|test)/java/' || true)"
if [[ -n "${bad_paths}" ]]; then
  echo "[fix] ERROR patch touches paths outside src/{main,test}/java:" >&2
  printf '  %s\n' ${bad_paths} >&2
  exit 3
fi

# Docker preflight (see run-repro.sh: a missing daemon must NOT masquerade as a
# test signal).
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not on PATH — cannot run sandbox" >&2; exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon not reachable — cannot run sandbox" >&2; exit 2
fi

# Build image (cached; shared with run-repro.sh).
if ! docker image inspect oss-bug-hunter-repro:latest >/dev/null 2>&1; then
  echo "[fix] building Docker image oss-bug-hunter-repro (first run only)" >&2
  if ! docker build -t oss-bug-hunter-repro:latest \
      --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$(repro_host_bind_src "${REPRO_DIR}")" >&2; then
    echo "ERROR: docker build failed" >&2; exit 2
  fi
fi

# Apply the patch (containment + --check already validated above). Restore on
# exit no matter what — R4: `reset --hard` restores tracked files AND `clean -fdq`
# removes any files the patch created (a bare `git checkout -- .` left them
# behind to poison later runs / persist a dropped hook). `.m2` is preserved.
git -C "${WORKTREE}" apply "${abs_patch}"
trap 'git -C "${WORKTREE}" reset --hard -q >/dev/null 2>&1; git -C "${WORKTREE}" clean -fdq -e .m2 >/dev/null 2>&1 || true' EXIT
echo "[fix] applied ${abs_patch}" >&2

# Drop the reproducer test into place (untracked).
if [[ -n "${TEST_FILE}" ]]; then
  [[ -f "${TEST_FILE}" ]] || { echo "ERROR: test file not found: ${TEST_FILE}" >&2; exit 3; }
  pkg_dir="${WORKTREE}/src/test/java/com/fasterxml/jackson/databind/repro"
  mkdir -p "${pkg_dir}"; cp "${TEST_FILE}" "${pkg_dir}/"
fi

NETWORK_FLAG="--network=${REPRO_NETWORK:-none}"
echo "[fix] running mvn test -Dtest=${TEST_FQCN} after fix (network=${REPRO_NETWORK:-none})" >&2

set +e
output="$(docker run --rm \
  ${NETWORK_FLAG} \
  -v "$(repro_host_bind_src "${WORKTREE}"):/work:rw" \
  -w /work \
  oss-bug-hunter-repro:latest \
  mvn -B test -Dtest="${TEST_FQCN}" -DfailIfNoTests=false 2>&1)"
exit_code=$?
set -e
printf '%s\n' "${output}" >&2

if [[ "${exit_code}" -eq 125 ]]; then
  echo "[fix] ERROR docker invocation failed (exit 125)" >&2; exit 2
fi

# Decide from the Surefire summary, not the ambiguous mvn exit code (R2: the
# parser never aborts the script on a missing summary).
read -r tests_run fails errors skipped <<<"$(surefire_counts "${output}")"

# R8: a skipped-only run (or none) means the fix produced no executable test.
if [[ "$((tests_run - skipped))" -le 0 ]]; then
  echo "[fix] no test actually ran (Tests run: ${tests_run}, Skipped: ${skipped}) — fix likely broke compilation" >&2
  exit 4
fi
if [[ "${fails}" -gt 0 || "${errors}" -gt 0 ]]; then
  echo "[fix] reproducer STILL FAILS after fix (Tests run: ${tests_run}, Failures: ${fails}, Errors: ${errors})" >&2
  exit 1
fi
echo "[fix] reproducer PASSES after fix (Tests run: ${tests_run}, no failures) — fix works" >&2
exit 0
