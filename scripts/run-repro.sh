#!/usr/bin/env bash
# LEGACY (Cell #1 / Day-3 batch path). The converged multi-language orchestrator
# does NOT use this script — it validates through tool/run_harness.py +
# tool/exec_backend.py (Java runs locally via the JUnit console launcher; non-Java
# via per-language adapters). Kept for the jackson-only Day-3 `run-repros` flow and
# as the documented Docker primitive. See docs/MULTI-LANGUAGE-VISION.md §11.
#
# Run a JUnit test against a pinned jackson-databind worktree, in a Docker
# sandbox. P1-17 fix (2026-05-19).
#
# Provides the primitive that the Phase-1 validation gates would consume —
# this script does NOT auto-wire into Day-3 gate evaluation. Wiring happens
# when Phase 1 ships.
#
# Usage:
#   scripts/run-repro.sh <worktree-dir> <test-class-fqcn> [test-file]
#
# Example:
#   scripts/run-repro.sh cell-1/backtest/worktrees/5608 \
#                        com.fasterxml.jackson.databind.repro.FooBar5608Test \
#                        cell-1/hunt/repros/cq-1.java
#
# Args:
#   <worktree-dir>      path to a git worktree (e.g., from day2-backtest.py prepare)
#   <test-class-fqcn>   fully-qualified class name for `-Dtest=`
#   [test-file]         optional .java file to copy into src/test/java/.../repro/
#
# Exit codes:
#   0  test passed
#   1  test failed (the reproducer "reproduces" — bug is real)
#   2  docker / mvn invocation error
#   3  bad args / missing input

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPRO_DIR="${PROJECT_ROOT}/tool/repro"
# shellcheck source=_repro_decide.sh
source "$(dirname "${BASH_SOURCE[0]}")/_repro_decide.sh"

usage() {
  sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# //;s/^#//'
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage; exit 0
fi

if [[ $# -lt 2 ]]; then
  echo "ERROR: need at least <worktree-dir> <test-class-fqcn>" >&2
  usage >&2
  exit 3
fi

WORKTREE="$1"
TEST_FQCN="$2"
TEST_FILE="${3:-}"

if [[ ! -d "${WORKTREE}" ]]; then
  echo "ERROR: worktree dir not found: ${WORKTREE}" >&2
  exit 3
fi

repro_network_validate                                       # R6: refuse host net by default
repro_acquire_lock "${PROJECT_ROOT}/.repro-sandbox.lock"     # R7: serialize worktree use
repro_pristine "${WORKTREE}"                                 # R12: clean tree before the run

# Optional: drop a test source file into the worktree at the conventional
# package path so mvn picks it up.
if [[ -n "${TEST_FILE}" ]]; then
  if [[ ! -f "${TEST_FILE}" ]]; then
    echo "ERROR: test file not found: ${TEST_FILE}" >&2
    exit 3
  fi
  # Place under src/test/java/com/fasterxml/jackson/databind/repro/<basename>
  pkg_dir="${WORKTREE}/src/test/java/com/fasterxml/jackson/databind/repro"
  mkdir -p "${pkg_dir}"
  cp "${TEST_FILE}" "${pkg_dir}/$(basename "${TEST_FILE}")"
  echo "[repro] copied ${TEST_FILE} → ${pkg_dir}/" >&2
fi

# Preflight: the Docker daemon MUST be reachable. Without this check a missing
# daemon makes `docker build` (below) fail with exit 1 under `set -e` — and exit
# 1 is this script's code for "test failed / bug reproduces". That would report
# a FALSE reproduction. Exit 2 (tooling error) instead, before any test runs.
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not on PATH — cannot run sandbox" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon not reachable — cannot run sandbox" >&2
  exit 2
fi

# Build image (cached after first build). Guard the build so its failure maps
# to exit 2 (tooling), never the ambiguous set -e exit 1.
if ! docker image inspect oss-bug-hunter-repro:latest >/dev/null 2>&1; then
  echo "[repro] building Docker image oss-bug-hunter-repro (first run only)" >&2
  if ! docker build -t oss-bug-hunter-repro:latest \
      --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
      "$(repro_host_bind_src "${REPRO_DIR}")" >&2; then
    echo "ERROR: docker build failed" >&2
    exit 2
  fi
fi

# Run mvn test in the sandbox.
#   --rm                       — auto-remove container after run
#   --network none             — NO NETWORK (would prevent maven from
#                                resolving deps on first run; the caller can
#                                opt out by re-running with NETWORK=host).
#                                Default off-network is safer for the
#                                "non-AI validator" rule.
# NOTE: no `-q` — we need the Surefire "Tests run: N, Failures: F, Errors: E"
# summary. The raw mvn exit code is AMBIGUOUS (exit 1 = test failure OR compile
# error; exit 0 = passed OR *no test matched* under -DfailIfNoTests=false), so
# we decide from the summary, not the exit code. This closes the false-NEGATIVE
# where a wrong/mis-named test class runs nothing and mvn exits 0, which would
# otherwise be scored as "bug did not reproduce".
NETWORK_FLAG="--network=${REPRO_NETWORK:-none}"
echo "[repro] running mvn test -Dtest=${TEST_FQCN} (network=${REPRO_NETWORK:-none})" >&2

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

# Docker-level failure (daemon down, image/OOM) — not a test signal.
if [[ "${exit_code}" -eq 125 ]]; then
  echo "[repro] ERROR docker invocation failed (exit 125)" >&2
  exit 2
fi

# Decide from the Surefire summary, not the ambiguous mvn exit code (R2: the
# parser never aborts the script on a missing summary).
read -r tests_run fails errors skipped <<<"$(surefire_counts "${output}")"

# R8: a skipped-only run (or no test at all) is NOT a reproduction signal.
if [[ "$((tests_run - skipped))" -le 0 ]]; then
  echo "[repro] ERROR no test actually ran (Tests run: ${tests_run}, Skipped: ${skipped}) — wrong class name or compile failure? (mvn exit ${exit_code})" >&2
  exit 2
fi
if [[ "${fails}" -gt 0 || "${errors}" -gt 0 ]]; then
  echo "[repro] FAIL (Tests run: ${tests_run}, Failures: ${fails}, Errors: ${errors}) — reproduces the bug" >&2
  exit 1
fi
echo "[repro] PASS (Tests run: ${tests_run}, no failures) — bug did NOT reproduce" >&2
exit 0
