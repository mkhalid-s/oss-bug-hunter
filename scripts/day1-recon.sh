#!/usr/bin/env bash
# Day 1 recon for Cell #1 — Jackson-databind x correctness.
#
# Phase 0, Cell #1 of the OSS bug-hunter project.
# See ../phase-0-scope.md §2 (Day 1) for context.
#
# This script GATHERS CONTEXT for the agent — it does not produce findings.
# After this runs, drive the Explore subagent against the artifacts in
# cell-1/recon/ to produce the module map, hot-spot ranking, and recon report.
#
# Idempotent. Re-run as needed. Output overwrites prior recon artifacts.
# All output stays local under cell-1/.

set -euo pipefail

# ---- config ----
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # P0-2: relocatable
TARGETS_DIR="${PROJECT_ROOT}/targets"
TARGET_DIR="${TARGETS_DIR}/jackson-databind"
CELL_DIR="${PROJECT_ROOT}/cell-1"
RECON_DIR="${CELL_DIR}/recon"
SCANNER_DIR="${RECON_DIR}/scanners"
TARGET_REPO="https://github.com/FasterXML/jackson-databind.git"

# JACKSON_TAG env var overrides auto-detect.
TARGET_TAG="${JACKSON_TAG:-}"

# ---- helpers ----
log()  { printf '[recon] %s\n' "$*" >&2; }
warn() { printf '[recon][WARN] %s\n' "$*" >&2; }
fail() { printf '[recon][FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- preflight ----
log "preflight: checking required tools"
for tool in git jq curl mvn java; do
  have "$tool" || fail "missing required tool: ${tool}"
done

# Optional but recommended
HAS_SEMGREP=0;  have semgrep && HAS_SEMGREP=1
HAS_SPOTBUGS=0; have spotbugs && HAS_SPOTBUGS=1
HAS_GH=0;       have gh && gh auth status >/dev/null 2>&1 && HAS_GH=1

[[ $HAS_SEMGREP -eq 0 ]]  && warn "semgrep missing — baseline incomplete. Install: pipx install semgrep"
[[ $HAS_SPOTBUGS -eq 0 ]] && warn "spotbugs missing — baseline incomplete. Install: see https://spotbugs.readthedocs.io/en/latest/installing.html"
[[ $HAS_GH -eq 0 ]]       && warn "gh CLI not authenticated — using unauthenticated GitHub API (low rate limit). Run: gh auth login"

mkdir -p "${TARGETS_DIR}" "${RECON_DIR}" "${SCANNER_DIR}"

# ---- step 1: clone & pin ----
if [[ -d "${TARGET_DIR}/.git" ]]; then
  # Refuse to overwrite local edits — surface them to the user.
  if ! git -C "${TARGET_DIR}" diff --quiet || ! git -C "${TARGET_DIR}" diff --cached --quiet; then
    fail "target dir has uncommitted changes: ${TARGET_DIR} — resolve before re-running"
  fi
  log "target exists; fetching tags"
  git -C "${TARGET_DIR}" fetch --tags --quiet origin
else
  log "cloning ${TARGET_REPO} (this can be ~500MB)"
  git clone --quiet --no-checkout "${TARGET_REPO}" "${TARGET_DIR}"
fi

# Auto-detect latest stable 2.x tag if not pinned
if [[ -z "${TARGET_TAG}" ]]; then
  TARGET_TAG="$(git -C "${TARGET_DIR}" tag --sort=-v:refname \
                  | grep -E '^jackson-databind-2\.[0-9]+\.[0-9]+$' \
                  | head -1)"
  [[ -z "${TARGET_TAG}" ]] && fail "could not auto-detect a jackson-databind-2.x.y tag"
fi
log "pinning to tag: ${TARGET_TAG}"
git -C "${TARGET_DIR}" checkout --quiet "${TARGET_TAG}" \
  || fail "tag ${TARGET_TAG} not found; list with: git -C ${TARGET_DIR} tag | tail -20"

PINNED_SHA="$(git -C "${TARGET_DIR}" rev-parse HEAD)"
PINNED_VERSION="${TARGET_TAG#jackson-databind-}"
log "pinned ${TARGET_TAG} @ ${PINNED_SHA:0:12}"

cat > "${RECON_DIR}/target-pin.json" <<JSON
{
  "repo": "${TARGET_REPO}",
  "tag": "${TARGET_TAG}",
  "version": "${PINNED_VERSION}",
  "commit": "${PINNED_SHA}",
  "pinned_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

# ---- step 2: GitHub metadata ----
# gh_api: GET a GitHub API path. Prints JSON on stdout, returns 0 on success.
#   On any non-success (network, 403 rate-limit, 404, 5xx) it writes a
#   warning to stderr and returns non-zero — callers MUST check exit code
#   before parsing stdout. Previously this swallowed errors via `|| echo '[]'`,
#   which silently truncated pagination on rate-limit. P1-7 fix (2026-05-19).
gh_api() {
  local path="$1"
  if [[ $HAS_GH -eq 1 ]]; then
    gh api -X GET "${path}"
    return $?
  fi
  # Unauthenticated path: capture body + HTTP code separately so we can
  # detect rate-limits + other errors instead of dropping them on the floor.
  local tmp http_code
  tmp="$(mktemp)"
  http_code="$(curl -sS -o "${tmp}" -w '%{http_code}' \
                 -H "Accept: application/vnd.github+json" \
                 "https://api.github.com${path}")" || {
    warn "curl failed for ${path}"
    rm -f "${tmp}"
    return 1
  }
  if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
    cat "${tmp}"
    rm -f "${tmp}"
    return 0
  fi
  # Surface the error body (truncated) so the user can see WHY it failed.
  warn "GitHub API ${http_code} for ${path}"
  warn "  body: $(head -c 240 "${tmp}" | tr -d '\n')"
  if [[ "${http_code}" == "403" ]] || [[ "${http_code}" == "429" ]]; then
    warn "  hint: hit unauthenticated rate limit (60/h). Try: gh auth login"
  fi
  rm -f "${tmp}"
  return 1
}

log "fetching last 50 releases (release-notes mining for backtest)"
# P1-7: handle gh_api failure explicitly (was: silent on 403/rate limit).
if ! gh_api "/repos/FasterXML/jackson-databind/releases?per_page=50" > "${RECON_DIR}/releases.json"; then
  warn "releases endpoint failed; writing empty array (Jackson uses git tags, so this is often empty anyway)"
  echo "[]" > "${RECON_DIR}/releases.json"
fi
REL_COUNT=$(jq 'length' "${RECON_DIR}/releases.json" 2>/dev/null || echo 0)
log "  got ${REL_COUNT} releases"

# `since` filters by updated_at — close enough for "closed in last 24mo"
if SINCE=$(date -u -d '24 months ago' +%Y-%m-%dT00:00:00Z 2>/dev/null); then :;
else SINCE=$(date -u -v-24m +%Y-%m-%dT00:00:00Z); fi
log "fetching closed issues since ${SINCE}"
# Jackson doesn't use a generic 'bug' label — they categorize by version (2.18, 3.2, ...)
# so we fetch all closed issues and filter out PRs in jq below. Day-2 ranker
# applies the correctness/feature/security keyword heuristics.

# Pull up to 400 closed items (4 pages of 100). ~50% are PRs which get filtered,
# leaving ~200 real issues — plenty for the backtest seed pool.
echo "[]" > "${RECON_DIR}/closed-bugs.json"
TMP_BUGS="$(mktemp)"
trap 'rm -f "${TMP_BUGS}"' EXIT
echo "[]" > "${TMP_BUGS}"
for page in 1 2 3 4; do
  # P1-7: stop pagination on first failure instead of silently treating
  # `[]` as "empty page". A rate-limit 403 mid-loop used to look like
  # "we've exhausted the data" — now it loudly stops with a warning.
  if PAGE_JSON="$(gh_api "/repos/FasterXML/jackson-databind/issues?state=closed&per_page=100&page=${page}&since=${SINCE}")"; then
    jq -s '.[0] + .[1]' "${TMP_BUGS}" <(printf '%s' "${PAGE_JSON}") > "${TMP_BUGS}.new"
    mv "${TMP_BUGS}.new" "${TMP_BUGS}"
  else
    warn "page ${page} fetch failed — stopping pagination; bug count will be partial"
    break
  fi
done
# Filter out pull requests (GitHub API returns PRs in /issues)
jq '[.[] | select(.pull_request == null)]' "${TMP_BUGS}" > "${RECON_DIR}/closed-bugs.json"
BUG_COUNT=$(jq 'length' "${RECON_DIR}/closed-bugs.json")
log "  got ${BUG_COUNT} closed issues (PRs filtered out)"

# ---- step 3: scanner baselines (context inputs, NOT our findings) ----
SEMGREP_COUNT="NOT_RUN"
SPOTBUGS_BUGS="NOT_RUN"

if [[ $HAS_SEMGREP -eq 1 ]]; then
  log "running semgrep (java + security-audit rulesets) — this can take 1-3min"
  semgrep --config p/java --config p/security-audit \
    --json --quiet --no-git-ignore --metrics=off \
    --output "${SCANNER_DIR}/semgrep.json" \
    "${TARGET_DIR}/src/main/java" 2>/dev/null || true
  SEMGREP_COUNT=$(jq '.results | length' "${SCANNER_DIR}/semgrep.json" 2>/dev/null || echo "PARSE_ERR")
  log "  semgrep: ${SEMGREP_COUNT} baseline findings"
fi

# P0-8: gate `mvn package` behind explicit opt-in. `mvn package` resolves and
# executes arbitrary plugin code from the upstream pom.xml — that's a
# supply-chain RCE primitive against the dev's machine on every recon. Default
# is now to SKIP the build (semgrep doesn't need it; spotbugs is the only
# consumer). Set OSS_BUG_HUNTER_ALLOW_MVN=1 to enable, after reading the
# warning and ideally sandboxing (bubblewrap/firejail/docker) yourself.
ALLOW_MVN="${OSS_BUG_HUNTER_ALLOW_MVN:-0}"

if [[ $HAS_SPOTBUGS -eq 1 && "${ALLOW_MVN}" != "1" ]]; then
  warn "spotbugs is installed but mvn package is SKIPPED by default (P0-8 supply-chain guard)."
  warn "  \`mvn package\` runs arbitrary upstream plugin code from FasterXML's pom.xml."
  warn "  To run anyway:  OSS_BUG_HUNTER_ALLOW_MVN=1 make    (preferably inside bubblewrap/docker)"
  warn "  Continuing WITHOUT spotbugs baseline — semgrep alone may suffice."
elif [[ $HAS_SPOTBUGS -eq 1 ]]; then
  warn "OSS_BUG_HUNTER_ALLOW_MVN=1 set — running \`mvn package\` (arbitrary upstream plugin code)"
  log "building jackson-databind (mvn -q package -DskipTests) — required for spotbugs"
  if (cd "${TARGET_DIR}" && mvn -q package -DskipTests -Dmaven.javadoc.skip=true); then
    JAR_PATH="${TARGET_DIR}/target/jackson-databind-${PINNED_VERSION}.jar"
    if [[ -f "${JAR_PATH}" ]]; then
      log "running spotbugs on ${JAR_PATH}"
      spotbugs -textui -xml -output "${SCANNER_DIR}/spotbugs.xml" "${JAR_PATH}" 2>/dev/null || true
      SPOTBUGS_BUGS=$(grep -c '<BugInstance' "${SCANNER_DIR}/spotbugs.xml" 2>/dev/null) || SPOTBUGS_BUGS=0
      log "  spotbugs: ${SPOTBUGS_BUGS} baseline bug instances"
    else
      warn "expected jar not found at ${JAR_PATH} — skipping spotbugs"
    fi
  else
    warn "maven build failed (JDK 17+ required for 2.18.x) — skipping spotbugs"
  fi
fi

# ---- step 4: structural recon ----
log "inventorying deserializer hot-spots"
DESER_DIR="${TARGET_DIR}/src/main/java/com/fasterxml/jackson/databind/deser"
DESER_COUNT=0
if [[ -d "${DESER_DIR}" ]]; then
  find "${DESER_DIR}" -name '*.java' -type f \
    | sed "s|^${TARGET_DIR}/||" \
    | sort > "${RECON_DIR}/deserializer-inventory.txt"
  DESER_COUNT=$(wc -l < "${RECON_DIR}/deserializer-inventory.txt")
  log "  ${DESER_COUNT} deserializer files indexed"
fi

# Per-file historical bug density (proxy for hot-spots): count how many times
# each file appears in the closed-bugs body/title. Coarse — agent refines later.
log "computing coarse historical-bug density"
jq -r '.[] | "\(.number)\t\(.title)\t\(.body // "")"' "${RECON_DIR}/closed-bugs.json" \
  > "${RECON_DIR}/closed-bugs.tsv"

if [[ -s "${RECON_DIR}/deserializer-inventory.txt" ]]; then
  awk -F/ '{print $NF}' "${RECON_DIR}/deserializer-inventory.txt" \
    | sed 's/\.java$//' \
    | while read -r cls; do
        [[ -z "$cls" ]] && continue
        # grep -c prints "0" AND exits 1 on no-match; the explicit `|| hits=0`
        # form avoids "0\n0" that `|| echo 0` would produce.
        hits=$(grep -c -F "$cls" "${RECON_DIR}/closed-bugs.tsv" 2>/dev/null) || hits=0
        printf '%d\t%s\n' "$hits" "$cls"
      done \
    | sort -rn \
    | head -30 \
    > "${RECON_DIR}/hot-spots-coarse.txt"
  log "  top hot-spots written to hot-spots-coarse.txt (top 30)"
fi

# ---- step 5: recon report stub ----
log "writing recon report stub"
cat > "${RECON_DIR}/cell-1-recon.md" <<MD
# Cell #1 Recon — Jackson-databind × correctness

**Run:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Target:** ${TARGET_REPO}
**Pinned:** ${TARGET_TAG} (${PINNED_SHA:0:12})
**Generated by:** scripts/day1-recon.sh

## Artifacts (auto-generated)

| File | What |
|---|---|
| \`target-pin.json\` | Exact commit pinned for this cell |
| \`releases.json\` | Last ${REL_COUNT} releases (mine for backtest dataset on Day 2) |
| \`closed-bugs.json\` | ${BUG_COUNT} closed bug-labelled issues since ${SINCE} |
| \`closed-bugs.tsv\` | Same, flattened to (number, title, body) for grep |
| \`deserializer-inventory.txt\` | ${DESER_COUNT} \`JsonDeserializer\`-region files |
| \`hot-spots-coarse.txt\` | Top 30 deserializer classes by mention-count in closed bugs |
| \`scanners/semgrep.json\` | Baseline — ${SEMGREP_COUNT} findings (NOT our findings) |
| \`scanners/spotbugs.xml\` | Baseline — ${SPOTBUGS_BUGS} bug instances (NOT our findings) |

## TODO — fill in by driving the Explore subagent

1. **Module map** of the deserialization pipeline: factory → resolver → deserializer instance → bound value
2. **Polymorphic-type resolution flow** — TypeDeserializer / TypeIdResolver / @JsonTypeInfo paths
3. **Refined hot-spots**: take the coarse list, cross-reference with backtest candidate bugs, narrow to a target shortlist for Day 3 novel hunt
4. **Scanner baseline summary** — categorize Semgrep/SpotBugs findings so any future candidate finding can be cleanly checked for novelty

## Reminders

- Scanner output is a BASELINE / context input. Any candidate finding from the agent must be cross-checked against \`scanners/*\` to prove it's novel.
- All output stays local. Nothing leaves \`cell-1/\`. See phase-0-scope.md §4.
- If 0 hot-spots emerge with mention-count > 1, the bug-label query may be too narrow — try \`labels=2.x\` or no label filter.

## Next

Read this report, then in Claude Code:

> Drive the Explore subagent against \`${RECON_DIR}\` to produce the module map and refined hot-spot ranking. Update this file in place.
MD

# ---- done ----
log "DONE."
log "Output:  ${RECON_DIR}"
log "Report:  ${RECON_DIR}/cell-1-recon.md"
log "Next:    drive the Explore subagent against the recon dir (see report's Next section)"
