#!/usr/bin/env bash
# Shared Surefire-summary parser for run-repro.sh / run-fix.sh.
#
# surefire_counts "<mvn output>" -> echoes "<tests_run> <failures> <errors> <skipped>"
#
# R2: never aborts under `set -euo pipefail`. A build that prints no "Tests run:"
# line (compile error, dependency-resolution failure, OOM) would otherwise make
# `grep` exit 1 and `pipefail`+`set -e` kill the *caller* with exit 1 — which the
# scripts interpret as "test failed" (false reproduction / false fix-rejection).
# The `|| true` guards make a missing summary yield zeros instead.
# R8: Skipped is returned so callers can treat a skipped-only run (tests_run -
# skipped == 0) as "no test actually ran" rather than a pass/fail signal.
surefire_counts() {
  local output="$1" summary
  summary="$(printf '%s\n' "${output}" | grep -E 'Tests run: [0-9]+' | tail -1 || true)"
  _sf_num() { printf '%s\n' "${summary}" | grep -oE "$1: [0-9]+" | grep -oE '[0-9]+' | head -1 || true; }
  local tr fa er sk
  tr="$(_sf_num 'Tests run')"; fa="$(_sf_num 'Failures')"; er="$(_sf_num 'Errors')"; sk="$(_sf_num 'Skipped')"
  echo "${tr:-0} ${fa:-0} ${er:-0} ${sk:-0}"
}


# R6: validate REPRO_NETWORK against an allowlist. `host` is REFUSED unless the
# operator explicitly opts in with REPRO_ALLOW_HOST_NET=1 (loud warning) — with
# host networking, `mvn test` on a poisoned pom can fetch+execute attacker Maven
# plugins and reach host/cloud-metadata endpoints. Call as a statement (not in
# $(...)) so its `exit` reaches the calling script.
repro_network_validate() {
  local n="${REPRO_NETWORK:-none}"
  case "${n}" in
    none|bridge) return 0 ;;
    host)
      if [[ "${REPRO_ALLOW_HOST_NET:-0}" == "1" ]]; then
        echo "[repro] ⚠ SUPPLY-CHAIN WARNING: REPRO_NETWORK=host — a poisoned pom can fetch+run untrusted Maven plugins and reach host/metadata endpoints. Use only with TRUSTED findings." >&2
        return 0
      fi
      echo "ERROR: REPRO_NETWORK=host refused; set REPRO_ALLOW_HOST_NET=1 to override (supply-chain risk)." >&2; exit 2 ;;
    *) echo "ERROR: REPRO_NETWORK='${n}' invalid — use none|bridge (or host with REPRO_ALLOW_HOST_NET=1)." >&2; exit 2 ;;
  esac
}

# R7: serialize worktree mutation across processes (orchestrator / run-fixes /
# make / parallel surfaces). Holds an exclusive flock for the lifetime of the
# calling script (fd 9 stays open). Fail-open if flock is unavailable.
repro_acquire_lock() {
  local lockfile="$1"
  exec 9>"${lockfile}" 2>/dev/null || return 0
  if command -v flock >/dev/null 2>&1; then
    flock -w 900 9 2>/dev/null || echo "[repro] WARN proceeding without worktree lock (timeout)" >&2
  fi
}

# R12: reset the worktree to a pristine checkout BEFORE a run so a prior crash,
# a leftover reproducer, or a session-driven agent edit can't contaminate the
# verdict. Preserves the maven cache (.m2).
repro_pristine() {
  local wt="$1"
  git -C "${wt}" reset --hard -q >/dev/null 2>&1 || true
  git -C "${wt}" clean -fdq -e .m2 >/dev/null 2>&1 || true
}


# Docker-outside-of-docker: when the in-container `docker` CLI talks to the HOST
# daemon (mounted /var/run/docker.sock), `-v <path>` and `docker build <ctx>` must
# use HOST paths, not container paths. If REPRO_HOST_PATH_PREFIX is set, rewrite a
# leading REPRO_CONTAINER_PATH_PREFIX (default /workspaces) to the host prefix.
# No-op when unset (docker-in-docker, or host path == container path).
repro_host_bind_src() {
  local abs; abs="$(cd "$1" && pwd)"
  local hp="${REPRO_HOST_PATH_PREFIX:-}" cp="${REPRO_CONTAINER_PATH_PREFIX:-/workspaces}"
  if [[ -n "$hp" && "$abs" == "$cp"* ]]; then
    printf '%s\n' "${hp}${abs#"$cp"}"
  else
    printf '%s\n' "$abs"
  fi
}
