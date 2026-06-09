# Phase 0 Scope — "Does it find anything real?"

**Owner:** mshaikh@guidewire.com
**Date:** 2026-05-18
**Duration:** 1-2 weeks (hard stop at week 2)
**Parent:** [oss-bug-hunter-research.md](./oss-bug-hunter-research.md) §7
**Status:** Draft scope, awaiting approval

---

## 0. TL;DR

You asked for "all targets × all angles × all success metrics" — that's 48 cells and defeats Phase 0's purpose (small, fast, learn-or-kill). This doc:

1. **Recommends one starting cell** to run in week 1: **Jackson-databind × correctness/edge-case × backtest-then-novel**.
2. **Lays out the full 4×4 matrix** as the eventual Phase 0+ surface — additional cells run *after* cell #1 either proves the loop or kills the idea.
3. **Adds a backtest step** the research brief didn't call out: before hunting novel bugs, replay known-historical bugs to calibrate signal/noise without the slop risk.

Hard kill condition for the whole project: 0 validated findings across 3 cells at week 2.

---

## 1. The Matrix (eventual Phase 0+ surface)

|                       | Security (deserialization/auth/crypto) | Correctness / edge-case | J2EE modernization smells | Stale-issue triage (read-only) |
|---|---|---|---|---|
| **Jackson (databind/core)** | skip — too crowded, gadget-chain bounty arms race; will mostly regenerate CVE-2017-7525 lineage | **CELL #1 — START HERE** | limited surface (lib, not container) | backlog is small and well-managed |
| **Spring Framework / Boot** | skip — same arms race; needs scoping to a single module first | huge surface, need to narrow to one module (e.g., spring-web HTTP parsing) | javax→jakarta migration is mostly done | backlog is huge but well-triaged |
| **Drools / rules engine** | niche threat model — possible but novel | **CELL #2** — niche, Guidewire-strategic, scanner-undercovered | some EJB-era patterns in older modules | **CELL #4** — lowest-risk pipeline test |
| **Apache Tomcat / Jetty** | skip — huge bounty target, under constant scrutiny | hard — concurrency bugs need fuzzing infra | **CELL #3** — config-pitfall + lifecycle smells = highest internal ROI | maintainers are noise-averse |

**Why the four chosen cells, in this order:**

- **Cell #1 (Jackson × correctness):** You know it (you said "all targets"). Massive historical-bug dataset → enables backtest calibration. Correctness angle sidesteps the bug-bounty arms race. Reproducer = trivial JUnit test. Guidewire definitely depends on it.
- **Cell #2 (Drools × correctness):** Niche enough that Semgrep/CodeQL under-cover it. Strategic to Guidewire (rules engine is core IP). Run only if Cell #1 proves the loop.
- **Cell #3 (Tomcat × J2EE modernization):** Highest internal ROI per the brief — config-pitfall surface aligns with `gw:ha-check` patterns you already have.
- **Cell #4 (Drools × issue triage):** Lowest blast radius. Tests pipeline (clone → triage → report → dedup cache) without needing the reproducer-builder. Good cell to run in parallel with #1 if you have a second day-job-free block.

Run additional cells **only after** cell #1 results land — no parallelizing on day 1.

---

## 2. Cell #1 — Jackson-databind × correctness (week 1, primary)

### Target

- Repo: `FasterXML/jackson-databind`
- Commit: pin to latest tag (2.18.x line at time of scope)
- Sub-targets in priority order:
  1. `JsonDeserializer` implementations for collection/map types (long history of edge-case bugs)
  2. Polymorphic type handling (the historical bug minefield, but on the *correctness* side: type erasure, generic resolution, not security gadgets)
  3. `@JsonCreator` constructor resolution
  4. Date/time deserializers (timezone edge cases)

### Investigation plan — the "manual-with-agent" loop

The Phase 0 loop is interactive, not autonomous. You drive Claude Code; sub-agents execute focused tasks. Steps:

**Day 1 — Recon (≈2h)**

- Clone target into `/workspaces/GW/OpenSource/oss-bug-hunter/targets/jackson-databind/`
- Spawn `Explore` subagent: map the deserialization pipeline, list all `JsonDeserializer` subclasses, summarize the polymorphic-type resolution flow
- Read `CHANGELOG`, last 24mo of release notes, last 50 closed bugs labeled `bug` or `regression` — feed into the backtest dataset (§3)
- Run Semgrep + SpotBugs + Error Prone as **context inputs**. Record their findings to a baseline file so we can later prove our findings are *not* in their output.
- Deliverable: `cell-1-recon.md` with module map, baseline scanner output, candidate hot-spots ranked by historical-bug density

**Day 2 — Backtest (≈4h) — see §3 for dataset**

- For each of 5-10 known historical bugs, check out the commit just *before* the fix
- Run the security-reviewer + code-quality sub-agents against the relevant module
- Score: did the agent find the known bug? (recall) Did it produce >5 unrelated findings? (precision)
- Deliverable: `cell-1-backtest.md` with recall@1, recall@3, precision@5 numbers

**Day 3 — Novel hunt, pass 1 (≈4h)**

- Only if backtest recall@3 ≥ 30%. Otherwise → §6 kill protocol.
- Run code-quality + edge-case sub-agents against current HEAD
- Each candidate finding goes through the validation gate (research brief §5.4):
  - Reproducer must be a runnable JUnit test
  - Dedup against OSV/GHSA + Jackson issue tracker (open + closed)
  - CWE-mapped (likely CWE-20, CWE-704, CWE-754, CWE-1284 territory)
  - Suggested fix must not break the existing test suite
- Deliverable: `cell-1-candidates-pass1.md` — N candidates, each with status: `validated` / `unreproducible` / `dupe` / `false-positive`

**Day 4 — Novel hunt, pass 2 + write-up (≈4h)**

- Re-run with self-consistency: spawn 3 fresh sub-agent contexts for any `validated` finding, require 2-of-3 agreement before counting it
- Final report: `cell-1-report.md` — what we found, what the loop cost, what to do next

### Tools and skills used in Cell #1

| Need | What exists | What needs building |
|---|---|---|
| Codebase exploration | `Explore` subagent | — |
| Security/code review | `code-reviewer`, `security-review` skills | — |
| Run Semgrep/SpotBugs locally | Bash tool | Wrapper script that captures baseline → `cell-1/baseline-scanners.json` |
| Run Maven test suite in sandbox | Bash | Dockerfile for jackson-databind reproducer execution (use eclipse-temurin:17-jdk image) |
| Reproducer-builder | — | **Critical gap.** For Phase 0, the *human* writes the reproducer with agent assistance; we are testing whether the agent's pointing is good enough that writing the reproducer is the easy step. Real reproducer-builder agent is a Phase 1 deliverable. |
| Dedup against OSV/GHSA | WebFetch + `mcp__github__search_issues` | Small Python helper to query OSV API + SimHash-compare issue titles |
| Finding storage | Filesystem (`cell-1/findings/<id>.md`) | — |

### Cost budget (week 1)

- LLM token spend: **\$25 hard cap** for Cell #1 (engineer-time is the real cost in Phase 0; tokens are noise at this scale)
- GitHub API: stay under 4000 req/h (well within unauthenticated limit; use a PAT to be safe)
- Local compute: jackson-databind builds in <2min on a dev box; no concerns

### Success criteria for Cell #1

- **Pass**: ≥1 finding that is (a) novel — not in OSV/GHSA, not in open or closed issues; (b) reproducible — Docker-sandboxed JUnit test fails on HEAD; (c) you judge would have taken you >2h to find unaided; (d) two of three self-consistency contexts agree it's a real bug.
- **Calibration target**: backtest recall@3 ≥ 30%, backtest precision@5 ≥ 20%. These are bars to prove the loop is sensible, separate from finding novel bugs.

### Kill criteria for Cell #1

- All candidates are duplicates of Semgrep/SpotBugs/Error Prone output → kill (no agentic signal over existing tooling)
- 0 reproducers run successfully → kill (reproducer-builder is more broken than expected; Phase 1 plan needs rework)
- Backtest recall@3 < 10% AND precision@5 < 10% → kill (the agents aren't pointing at real bug-shaped code)
- Token spend > \$50 with no validated findings → kill on budget

---

## 3. Backtest dataset (build before novel hunt)

The brief didn't call this out, but it's the cheapest calibration we have. Build before Day 3 of Cell #1.

**Approach:**
1. From Jackson's `release-notes/VERSION-2.x` files, identify 10 closed bugs from the last 24 months that:
   - Have a clear fix commit
   - Are correctness bugs (not feature requests)
   - Are non-trivial to spot from reading the diff
2. For each: record (issue#, fix-commit-sha, parent-commit-sha, one-line description, expected sub-agent that should find it)
3. Store at `cell-1/backtest/dataset.yaml`
4. Day-2 run: check out parent commit, run agent, score recall

**Why this matters:** novel-bug hunting on day 3 is high-variance — you might find a real bug or whiff entirely on noise. Backtest gives a controlled signal estimate before betting the week on novel hunting.

---

## 4. Output routing for Phase 0

**All output is local.** Nothing leaves `/workspaces/GW/OpenSource/oss-bug-hunter/cell-1/`.

- No GitHub issues filed
- No internal Asana/Jira tickets (Phase 1 territory)
- No upstream PRs (Phase 2 territory)
- If you happen to find something genuinely critical (security RCE-class), follow Jackson's `SECURITY.md` private disclosure path — don't file publicly even on a one-off

---

## 5. Schedule

| Day | Hours | Activity |
|---|---|---|
| 1 (Mon) | 2 | Cell #1 recon |
| 2 (Tue) | 4 | Cell #1 backtest dataset build + run |
| 3 (Wed) | 4 | Cell #1 novel hunt pass 1 (gated on day-2 results) |
| 4 (Thu) | 4 | Cell #1 novel hunt pass 2 + write-up |
| 5 (Fri) | 2 | Decision: kill / continue / launch cells #2-#4 in week 2 |
| 6-10 (week 2) | as available | Run cells #2/#3/#4 in priority order if cell #1 passed; otherwise post-mortem |

Hard stop: end of day 10. If we haven't concluded by then, the loop is too slow — that itself is a Phase 0 finding.

---

## 6. Kill protocol (if Cell #1 fails)

Before declaring the whole project dead, run **one** retry against a different target/angle pair (best candidates: Drools × issue-triage for lowest blast radius, or Tomcat × J2EE-modernization for highest signal asymmetry). Same 1-week budget, same gates.

If second cell also fails → kill the project. Write a post-mortem at `cell-1/post-mortem.md` capturing:
- What the agents pointed at
- Why findings were slop / dupes / unreproducible
- Whether the issue is tooling (fixable in Phase 1) or premise (kill)

---

## 7. Open questions

1. **Sandbox infra:** do we have a sanctioned Docker setup for running untrusted-ish OSS builds in `/workspaces/GW/`, or do we stand up an isolated VM? (Jackson is safe; this matters more for Phase 1.)
2. **GitHub PAT:** do you want to use a personal PAT for API calls, or set up a dedicated read-only token? (Phase 0 is read-only-against-OSS; either works.)
3. **Asana/Jira routing in Phase 1:** which project should internal-findings tickets land in? (Defer; not blocking.)
4. **Backtest dataset size:** 10 historical bugs is the minimum useful sample. If we can spare an extra day, 20 gives much tighter precision/recall estimates.
5. **Reproducer-builder skill:** Phase 0 has the human write reproducers. Should we draft a `reproducer-builder` skill spec at the end of week 2 (as Phase-1 input), or wait until Phase 1 starts?

---

## 8. What this scope explicitly does NOT do

- Build a reproducer-builder agent (Phase 1)
- Set up cron-driven sweeps (Phase 3)
- File anything upstream (Phase 2+)
- Touch curl, CPython, React, Airflow, or any project on the AI-skeptic blocklist
- Optimize for finding maximum bugs (optimizing for *learning whether the loop produces signal*)
- Try to beat XBOW or CodeMender (this is calibration on our own infra, not benchmarking)
