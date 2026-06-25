# OSS Bug Hunter — Phase 0 Cell #1

Phase 0 of an experiment in agent-driven OSS bug hunting. **Cell #1** targets `jackson-databind` from a **correctness** angle (not security), running 4 days of structured investigation that calibrates whether the agentic loop produces signal over free static-analysis tools (Semgrep, SpotBugs).

> Background: `oss-bug-hunter-research.md` (landscape + architecture) and `phase-0-scope.md` (cell scope, success criteria, kill protocol). Read those first if you're new to the project.

> **Update (2026-06): the project grew past the Java-only Cell #1 below into a multi-language engine + a visual app.** See **`docs/MULTI-LANGUAGE-VISION.md`** for the full picture. In short:
> - **Engine** — one `HarnessAdapter` interface over **Java · Python · Go · Rust · JS** (`tool/adapters.py`), one converged orchestrator (`run_harness.orchestrate`, reached by the CLI, the SSE job, `POST /api/orchestrate`, and the MCP tool), an execution-backend abstraction (`tool/exec_backend.py`: **trusted → local, untrusted → docker/podman**), and an LLM fix-builder retry provider. Java validates **locally** (JUnit console launcher) — *not* Docker-required.
> - **Visual app** — a React + Vite + Mantine SPA served by FastAPI at **`/app`**: Targets (add-by-URL) → Runs (live SSE log) → Findings board → finding detail (CodeMirror reproducer + diff2html fix) → Open-PR preview (identity-gated). New modules: `run_store.py` (SSE seam), `findings.py`, `targets.py`, `pr.py`, `llm_fix_provider.py`, `tool/webapp/`.
> - The **Makefile 17-step pipeline below remains the jackson-only Cell #1 flow**; the multi-language engine + app are reached via the multi-language CLI (`tool/run_harness.py … --lang`) / `/api` / MCP / the **React app at `/app`** (served by the same FastAPI server — distinct from the legacy `make dashboard` UI at `/`), not `make`.

---

## TL;DR

```bash
cd /workspaces/OpenSource/oss-bug-hunter

make            # advance pipeline — runs auto steps, stops at human steps with instructions
make status     # show where you are
make help       # list all targets (including reset/re-run)
```

The pipeline is **17 ordered steps across 4 days**, ~14h of engineer time. Auto steps chain; human steps pause with what-to-do instructions. Type `make` again after each human step.

---

## What you need

**Required:**
- Linux/Mac shell, GNU `make`
- `git`, `jq`, `curl`, `mvn`, `java` (JDK 17+ for jackson 2.18.x)
- `python3` + `pyyaml` (install into a project venv — see below)
- `uv` for venv management (`pip install uv` if needed, or use the bundled installer)

```bash
uv venv .venv --python=python3.13
uv pip install --python .venv/bin/python pyyaml pytest
# Optional, for the dashboard/MCP tool:
uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' mcp
```

**Recommended** (graceful degradation if missing — recon prints warnings):
- `pipx install semgrep` — Java + security-audit rulesets, used as baseline
- `spotbugs` — Java bug-finder baseline
- `gh` CLI, authenticated (`gh auth login`) — GitHub issue dedup + higher API rate limits

**Subagent execution** happens inside your **Claude Code session** — the Makefile prints prompt paths, you `cat` them and invoke via the `Agent({...})` tool. A headless driver also exists (`tool/claude_driver.py`, used by `make dashboard`, the CLI `python tool/pipeline.py`, and the MCP server) for unattended runs at `--model haiku --effort low`, including parallel `*-batch` fan-out; quality is lower than session-driven agents, so the Make/session path is the documented default. See [Headless & CLI command reference](#headless--cli-command-reference).

---

## The pipeline

| Day | Title | Time | Final output of the day |
|---|---|---|---|
| 1 | Recon | ~2h | `cell-1/shortlist.txt` (3-8 hot-spot files for the hunt) |
| 2 | Backtest (calibrate against 10 historical bugs) | ~4h | `cell-1/cell-1-backtest.md` (PROCEED / KILL / BASELINES_MISSING gate decision) |
| 3 | Novel hunt pass 1 + validation | ~4h | `cell-1/cell-1-candidates-pass1.md` |
| 4 | Self-consistency (pass 2+3) + final report | ~4h | `cell-1/cell-1-report.md` |

### How `make` chains

```
make → runs auto step → prints "next" → exits 0
make → runs auto step → hits human step → prints instructions → exits 1
  (you do the human work — drive agent / edit YAML)
make → resumes from where state landed
```

`make status` shows the 17-step checklist with the current position marked `[>]`.

---

## Day-by-day walkthrough

### Day 1 — Recon (~2h)

**Auto:** clones jackson-databind, fetches recent releases + closed bugs, runs Semgrep/SpotBugs as baselines, indexes `JsonDeserializer` files, computes coarse "historical-bug density" hot-spots.

```bash
make
# → runs scripts/day1-recon.sh
# → outputs land in cell-1/recon/
```

**Human #1: drive Explore subagent.** In Claude Code:

```bash
cat scripts/explore-prompt.md   # see the prompt body
```

Invoke via the Agent tool:

```
Agent({
  subagent_type: "Explore",
  description: "Cell #1 Jackson inventory",
  prompt: "<<paste the prompt body from explore-prompt.md>>"
})
```

The subagent returns a structured inventory in its message body. Save the body to:
```
cell-1/recon/explore-inventory.md
```

**Human #2: write the shortlist.** Pick 3-8 hot-spot files. Sources to look at:
- `cell-1/recon/hot-spots-coarse.txt` — top 30 ranked by mention-count in closed bugs
- `cell-1/recon/explore-inventory.md` — structured inventory from subagent
- `cell-1/recon/deserializer-inventory.txt` — raw file list

```bash
$EDITOR cell-1/shortlist.txt
# one repo-relative path per line, e.g.:
#   src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java
#   src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsArrayTypeDeserializer.java
#   ...

make    # advance to Day 2
```

### Day 2 — Backtest (~4h)

**Auto:** ranks all closed bugs as backtest candidates (by file-coverage, lines-changed, correctness-keyword score), writes top 30 to `candidates.yaml` and auto-picks top 10 to `dataset-autopick.yaml`.

```bash
make
# → runs scripts/day2-build-dataset.py
# → outputs cell-1/backtest/{candidates.yaml, dataset-autopick.yaml}
```

**Human #3: finalize dataset.** Either accept the auto-pick or hand-pick from candidates:

```bash
cp cell-1/backtest/dataset-autopick.yaml cell-1/backtest/dataset.yaml
$EDITOR cell-1/backtest/dataset.yaml
# For each of 10 entries, fill:
#   expected_subagent: code-quality | edge-case
#   notes: <one-line on why this bug is interesting to backtest>
```

**Auto:** prepares per-entry git worktrees + uniform prompts.

```bash
make
# → runs scripts/day2-backtest.py prepare
# → creates cell-1/backtest/worktrees/<issue>/ + runs/<issue>/{prompt.md,findings.yaml,labels.yaml}
# → writes cell-1/backtest/runbook.md (per-entry checklist)
```

**Human #4: run 10 backtest agents.** For each issue (see `runbook.md`):

```bash
cat cell-1/backtest/runs/<issue>/prompt.md   # see prompt
```

In Claude Code:

```
Agent({
  subagent_type: "general-purpose",      // or "code-reviewer"
  description: "Backtest issue #<N>",
  prompt: "<<paste prompt.md content>>"
})
```

Paste the agent's `findings:` YAML block into `cell-1/backtest/runs/<issue>/findings.yaml`. **That's the only required Day-2 human work** (post-P0-1 fix).

**`labels.yaml` is now optional and advisory.** The Phase-0 gate decision uses **deterministic file-coverage scoring** — it compares each finding's `location` against the historical fix's `files_touched`, no LLM judgment involved. If you want richer per-finding labels for triage, fill `labels.yaml` (using the vocabulary below) or run the auto-labeler (`POST /api/subagent/backtest/{N}/label` if the dashboard is running). The labels appear in the report as informational, never as a gate input.

Label vocabulary (only if you fill `labels.yaml`):
- `matches_known` — describes the historical bug
- `unrelated_tp` — different real bug
- `fp` — not actually a bug
- `dupe_of_baseline` — duplicates a Semgrep/SpotBugs finding

Set `matched_rank` to the position (1-indexed) of the first `matches_known` finding, or `null` if none.

#### Gate semantics (P0-1 fix, 2026-05-19)

The Day-2 score function computes **two parallel metric families**:

| Family | Inputs | Used as |
|---|---|---|
| **Deterministic file-coverage** | `finding.location` file vs `entry.files_touched` | **Gate input** — auditable, falsifiable, no LLM in the loop |
| LLM/human-judged labels | `labels.yaml` (optional) | Informational only — surfaced in the report alongside the gate, never gates |

The gate proceeds to Day 3 IFF all three pass:
- `file_coverage@3 ≥ 30%` (top-3 findings hit at least one fix-touched file in ≥30% of entries)
- `file_match_precision@5 ≥ 20%` (overall, ≥20% of findings point at fix-touched files)
- At least one entry has a `file_match` in a file that Semgrep/SpotBugs did NOT also flag (novel-signal-over-baseline check)

Why this changed: the prior gate read `recall@K`/`precision@K` from `labels.yaml`, which was filled by an auto-labeler that itself calls `claude -p`. An LLM grading an LLM's output and feeding the result into the gate directly violates the project's own rule from `oss-bug-hunter-research.md` §5.4 ("validators are non-AI"). The new gate measures something the prompt cannot game.

**Auto:** scores backtest, writes report with gate decision.

```bash
make
# → runs scripts/day2-backtest.py score
# → cell-1/cell-1-backtest.md contains:
#     - Deterministic: file_coverage@1/3/5, file_match_precision@5
#     - Informational: recall@1/3/5, precision_matches@5, precision_anyTP@5
#     - Baseline coverage
#     - Gate decision: PROCEED | KILL | BASELINES_MISSING

# If PROCEED:
mkdir -p cell-1/hunt && touch cell-1/hunt/.gate.ok && make
# If KILL: follow phase-0-scope.md §6 — try a retry cell or write post-mortem
# If BASELINES_MISSING: install semgrep + spotbugs, then `make reset-backtest && make`
```

**BASELINES_MISSING** decision: if neither Semgrep nor SpotBugs output is present in `cell-1/recon/scanners/`, the score function refuses to compute PROCEED/KILL and emits `BASELINES_MISSING` — the "novel signal over baseline" gate is structurally unfalsifiable without them. Fix: install the missing tool(s) and re-run Day 1 recon.

### Day 3 — Novel hunt + validation (~4h)

**Auto:** substitutes your shortlist into the two angle prompts.

```bash
make
# → runs scripts/day3-hunt.py prepare
# → writes cell-1/hunt/{code-quality,edge-case}/prompt.md
```

**Human #5: run both hunt agents in fresh contexts.**

```bash
cat cell-1/hunt/code-quality/prompt.md   # → run via Agent → paste findings into findings-pass1.yaml
cat cell-1/hunt/edge-case/prompt.md       # → run via Agent → paste findings into findings-pass1.yaml
```

Each run = one `Agent({...})` call. If an agent returns `findings: []`, that's a valid result — leave it.

**Auto:** generates per-finding validation scaffolds with auto-dedup against OSV + GitHub issues.

```bash
make
# → runs scripts/day3-hunt.py validate (first time — generates scaffolds)
# → cell-1/hunt/validation/<id>.yaml per finding (id: cq-N or ec-N)
```

**Human #6: fill the validation gates** for each scaffold. For each `cell-1/hunt/validation/<id>.yaml`:

1. **Reproducer**: write JUnit test → `cell-1/hunt/repros/<id>.java`, run it, fill `gates.reproducer.{status,path,notes}`
2. **Dedup**: review the auto-suggested OSV/GitHub matches; fill `gates.dedup.{is_duplicate,references,notes}`
3. **CWE**: assign a CWE id (CWE-476 for NPE, CWE-193 for off-by-one, etc.); fill `gates.cwe.{cwe,cvss,notes}`
4. **Fix passes tests**: write a patch → `cell-1/hunt/patches/<id>.patch`, apply it, run `mvn test` in `targets/jackson-databind/`, fill `gates.fix_passes_tests.{status,patch_path,notes}`
5. **Set `final_status`**: `validated` | `unreproducible` | `dupe` | `false-positive`

**Auto:** aggregates filled scaffolds into the pass-1 report.

```bash
make
# → runs scripts/day3-hunt.py validate (second time — writes report)
# → cell-1/cell-1-candidates-pass1.md
```

### Day 4 — Self-consistency + final report (~4h)

**Auto:** creates empty pass-2 and pass-3 stubs.

```bash
make
# → runs scripts/day4-finalize.py prepare
```

**Human #7: re-run hunt prompts** in **4 fresh** Agent contexts (2 angles × 2 passes). **CRITICAL:** each pass must be a fresh `Agent({...})` call — fresh contexts are what makes self-consistency meaningful.

```bash
cat cell-1/hunt/code-quality/prompt.md   # → Agent → findings-pass2.yaml
cat cell-1/hunt/code-quality/prompt.md   # → Agent → findings-pass3.yaml
cat cell-1/hunt/edge-case/prompt.md       # → Agent → findings-pass2.yaml
cat cell-1/hunt/edge-case/prompt.md       # → Agent → findings-pass3.yaml
```

**Auto:** scores 2-of-3 self-consistency, updates scaffolds, writes the final report.

```bash
make
# → runs scripts/day4-finalize.py report
# → cell-1/cell-1-report.md
```

**Human #8: fill the final report's HUMAN sections** — Cost (token spend + engineer hours), Lessons learned, Recommendation (proceed to Cell #2 / kill / retry).

---

## Reset / re-run

If you need to redo a phase, the Makefile has targeted reset commands. Each chains into the smaller ones, so the larger resets are supersets:

| Target | Removes | Keeps |
|---|---|---|
| `make reset-pass23` | Day 4 pass-2/3 + final report | pass-1 candidates, validation scaffolds, backtest, recon |
| `make reset-hunt` | Day 3 + Day 4 | backtest, recon, shortlist |
| `make reset-backtest` | Day 2 + Day 3 + Day 4 | recon, shortlist |
| `make reset-cell` | all of `cell-1/` | the jackson-databind clone |
| `make clean` | (alias for `reset-cell`) | |
| `make wipe` | `cell-1/` + the clone (~500MB re-clone) | nothing |

All destructive — they `rm -rf`. Worktree removal uses `git worktree remove` before deleting `cell-1/backtest/worktrees/`, so the main clone's worktree refs stay clean.

---

## What's automated vs. you-driven

| Script-driven (auto) | Subagent dispatch (auto *or* you) | You-driven (human judgment) |
|---|---|---|
| Clone + baseline scanners (Day 1) | Explore inventory (Day 1) | Picking the shortlist (Day 1) |
| Candidate ranking + auto-pick (Day 2) | Backtest runs (Day 2) | Finalizing `dataset.yaml` (Day 2) |
| Worktree creation + prompt generation | Hunt passes 1–3 (Day 3–4) | CWE / dedup judgment (Day 3 gates) |
| OSV + GitHub auto-dedup | Reproducer-builder `.java` (Day 3) | Cost / Lessons / Recommendation (final report) |
| Self-consistency matching | Fix-builder `.patch` (Day 3) | |
| Reproducer execution (`run-repro.sh`, non-AI) | Backtest auto-labeling (advisory) | |
| Fix validation (`run-fix.sh`, non-AI) | | |
| Aggregation + report writing | | |

The middle column is the headless path: each of those subagent steps can be
driven **either** by you (run the prompt via the Claude Code Agent tool and
paste the result — the documented, higher-quality default) **or** unattended via
`claude -p` (the dashboard/CLI/MCP surfaces, `haiku/low` by default — faster and
parallel, lower quality). The reproducer/fix *executors* and every gate decision
stay non-AI by design ("agent proposes, non-AI validators dispose").

**Self-correcting orchestrator** (`orchestrate`): chains the above into a loop —
ensure reproducer → validate (the bug must reproduce) → build fix → validate
(reproducer must flip green) → **on failure, feed the failure back to the
fix-builder and retry**. It's the SWE-agent/OpenHands pattern built on these
primitives (no extra platform, no API key, gates stay non-AI). Validation runs
on the execution backend `tool/exec_backend.py` picks: **trusted targets run
locally** (Java always, via the JUnit console launcher — no Docker), while
**untrusted non-Java targets are sandboxed in Docker/podman**. Its builders run
at **opus/high** by default (quality is
decisive when a fix must flip a test green) — override with `--model`/`--effort`;
the standalone/batch runners stay on haiku/low for throughput.

---

## Headless & CLI command reference

Three surfaces drive the same `tool/pipeline.py` operations over shared
`cell-1/` state; use whichever fits. All `claude -p` dispatch defaults to
`haiku/low` (tractable, lower quality) with automatic retry on transient errors.

**CLI** — `python tool/pipeline.py <command>` (JSON to stdout; exit code 0 when `ok`):

```bash
# state / artifacts
.venv/bin/python tool/pipeline.py status
.venv/bin/python tool/pipeline.py run-step day2-build       # run one AUTO step
.venv/bin/python tool/pipeline.py list-artifacts
.venv/bin/python tool/pipeline.py read-artifact recon-report
.venv/bin/python tool/pipeline.py write-artifact shortlist shortlist.txt   # or '-' for stdin

# Day 1 / Day 2
.venv/bin/python tool/pipeline.py run-explore
.venv/bin/python tool/pipeline.py list-backtest
.venv/bin/python tool/pipeline.py run-backtest 5608                       # one entry
.venv/bin/python tool/pipeline.py run-backtest-batch                      # all prepared, parallel
.venv/bin/python tool/pipeline.py run-backtest-batch --issues 5608 5615 --parallel 4
.venv/bin/python tool/pipeline.py label-backtest 5608                     # advisory

# Day 3 / Day 4
.venv/bin/python tool/pipeline.py run-hunt code-quality 1                 # one pass
.venv/bin/python tool/pipeline.py run-hunt-batch                          # the 4 Day-4 passes, parallel
.venv/bin/python tool/pipeline.py run-hunt-batch --passes code-quality:1 edge-case:1
.venv/bin/python tool/pipeline.py run-repro ec-1                          # build one reproducer .java
.venv/bin/python tool/pipeline.py run-repro-batch                         # all pending, parallel
.venv/bin/python tool/pipeline.py run-fix ec-1                            # build one fix patch (needs the reproducer)
.venv/bin/python tool/pipeline.py run-fix-batch                          # all reproducers w/o a patch, parallel
.venv/bin/python tool/pipeline.py suggest-gates                          # advisory: auto-fill blank dedup/CWE gates (non-AI)

# Self-correcting loop (reproduce → fix → validate → retry-with-feedback)
# pipeline.py orchestrate = the jackson-only Cell #1 driver (scaffold-driven; language is read per-finding — there is no --lang flag here)
.venv/bin/python tool/pipeline.py orchestrate                            # all findings; builders run opus/high; trusted→local (Java always, no Docker), untrusted non-Java→Docker (untrusted Java: not yet supported)
.venv/bin/python tool/pipeline.py orchestrate --ids ec-1 --max-fix-attempts 3
.venv/bin/python tool/pipeline.py orchestrate --model sonnet --effort medium   # override the opus/high default

# Multi-language CLI — the converged engine the app, /api, and MCP all call (any target, any of the 5 languages):
.venv/bin/python tool/run_harness.py orchestrate <worktree> <finding-id> <reproducer> <patch> --trusted --lang <java|python|go|rust|javascript>
```

`--parallel` is bounded to 10. The `*-batch` commands fan out via
`claude_driver.run_claude_batch` (concurrent, order-preserving, per-call retry).
The reproducer/fix *builders* only PROPOSE artifacts (`.java` / `.patch`); non-AI
validators dispose. On the **converged** `orchestrate` path the validator is
`run_harness.orchestrate` (backend chosen by `tool/exec_backend.py`), which decides
each gate and restores the worktree (`pristine()`). The legacy `day3-hunt.py
run-repros` / `run-fixes` (Docker sandbox) executors are the **jackson-only Cell #1
batch path** only — not used by `orchestrate`.

**Dashboard** (`make dashboard` → http://127.0.0.1:8765): every auto step has a
"Run" button; the Day-2, Day-3 (findings + gates), and Day-4 steps each carry
"▶▶ … (parallel)" batch buttons (Day-3 gates has both reproducers and fixes).
Per-launch bearer token + host allowlist.

**REST** (same server): `POST /api/run/{step_id}`, `/api/subagent/{explore,
backtest/{n},backtest/batch,hunt/{angle}/pass{n},hunt/batch,repro/{id},repro/batch,
fix/{id},fix/batch}`, `/api/suggest-gates`, and `/api/orchestrate` (the
self-correcting loop). Batch bodies accept `{issue_nums|passes|finding_ids?,
max_parallel?}`; orchestrate accepts `{finding_ids?, max_fix_attempts?, model?,
effort?, worktree?, network?}`.

**MCP** (`tool/mcp_server.py`, stdio): 18 `bug_hunter.*` tools mirroring the above
(`status`, `run_step`, `list/read/write_artifact`, `list_backtest_entries`,
`run_backtest_subagent`, `run_backtest_batch`, `label_backtest_subagent`,
`run_explore_subagent`, `run_hunt_subagent`, `run_hunt_batch`,
`run_repro_subagent`, `run_repro_batch`, `run_fix_subagent`, `run_fix_batch`,
`suggest_gates`, `orchestrate`).

---

## Troubleshooting

**`make: *** No rule to make target ...`** — Make wants a file that should exist by now. Run `make status` to see which step the pipeline thinks you're at; create or fix the missing artifact.

**Recon script bails on a missing tool** — Day 1 requires `git jq curl mvn java`. Install via your package manager. Semgrep + SpotBugs are optional (warns and continues).

**Backtest gate keeps failing** — Read `cell-1/cell-1-backtest.md` § "Gate decision". The gate is deterministic (post-P0-1 fix): `file_coverage@3` measures whether the agent's top findings point at the same files the historical fix touched. Genuine low file-coverage means the prompt isn't even pointing at the right modules — that's the kill criterion. Don't tune to pass; that defeats Phase 0's purpose.

**Gate says BASELINES_MISSING** — neither Semgrep nor SpotBugs ran successfully. Install both (`pipx install semgrep` + see https://spotbugs.readthedocs.io for spotbugs) and then `make reset-backtest && make` to re-run from recon with baselines populated. Without baselines, the "novel signal over free tools" gate is unfalsifiable.

**Dataset has 3.x file paths but recon pinned 2.x** — should not happen post-P0-4 fix; `day2-build-dataset.py` now filters fix commits to those reachable from the pinned tag via `git merge-base --is-ancestor`. If you somehow see this, `make reset-backtest && make` regenerates a clean dataset.

**Agent returns slop** — Strengthen the prompt's "bet money" line, tighten out-of-scope clauses, or pick a smaller shortlist. Phase 0 partly exists to discover whether the prompts work.

**Self-consistency dropped a finding you believe is real** — Edit `cell-1/hunt/validation/<id>.yaml` and manually set `self_consistency.survived: true`. Re-run `make final-report`. The auto-matcher is coarse (same file path + ≥2 keyword overlap).

**Stale worktree references** — If `cell-1/backtest/worktrees/` was removed without using `make`, run:
```bash
git -C targets/jackson-databind worktree prune
```

**"Clock skew detected" from make** — Cosmetic. Codespace timestamps are slightly ahead of the system clock. Ignore.

---

## Project layout

```
oss-bug-hunter/
  README.md                        ← this file
  Makefile                         ← orchestration entry point (Make is the documented path)
  REVIEW.md                        ← multi-agent code review output (latest pass: 2026-05-19)
  oss-bug-hunter-research.md       ← landscape + architecture brief
  phase-0-scope.md                 ← Phase 0 scope, success/kill criteria
  scripts/
    _lib.py                        ← shared utils (keyword extraction)
    _check.py                      ← content gates for Makefile
    _status.sh                     ← status display
    day1-recon.sh                  ← Day 1 driver (clone + baseline scanners + inventory)
    explore-prompt.md              ← Day 1 Explore subagent prompt
    day2-build-dataset.py          ← Day 2 candidate ranking (filters to pinned branch)
    day2-backtest.py               ← Day 2 prepare + score (deterministic file-coverage gate)
    day3-novel-hunt-prompts.md     ← Day 3 runbook + canonical prompts
    day3-hunt.py                   ← Day 3 prepare + dedup + validate + repro/fix prompts + run-repros/run-fixes
    repro-builder-prompt.md        ← canonical reproducer-builder prompt (loaded at runtime)
    run-repro.sh                   ← non-AI reproducer executor (Docker JUnit sandbox)
    fix-builder-prompt.md          ← canonical fix-builder prompt (loaded at runtime)
    run-fix.sh                     ← non-AI fix validator (apply patch + re-run reproducer in Docker)
    day4-finalize.py               ← Day 4 prepare + report (2-of-3 self-consistency)
  tool/                            ← the multi-language engine + visual app (CLI / /api / MCP / /app); Make drives only the jackson-only Cell #1
    pipeline.py                    ← Cell #1 orchestration, state, subagent runners + batch fan-out + CLI (`python tool/pipeline.py <cmd>`)
    run_harness.py                 ← the CONVERGED multi-language engine: validate-repro/fix + orchestrate (`--lang java|python|go|rust|javascript`)
    adapters.py                    ← per-language HarnessAdapters (Python/Go/Rust/JS) + the shared Outcome/TestVerdict contract
    exec_backend.py                ← execution backend: docker→podman→local autodetect, trust-gated (untrusted never runs local)
    run_store.py                   ← job/run model + SSE seam (SQLite WAL + per-run pub/sub + replay buffer)
    findings.py / targets.py / pr.py  ← findings API · add-target-by-URL · read-only PR preview (identity-gated; never pushes)
    llm_fix_provider.py            ← LLM fix-builder wired as the orchestrate retry hook (AI proposes, non-AI validators dispose)
    server.py                      ← FastAPI: dashboard + REST/SSE API + the React app at /app (http://127.0.0.1:8765)
    mcp_server.py                  ← MCP server (18 tools over stdio)
    claude_driver.py               ← headless `claude -p` driver (retry + parallel batch; haiku/low default)
    webapp/                        ← React + Vite + Mantine SPA (built to dist/, served at /app; no Node at runtime)
    web/                           ← legacy U0 dashboard page (index.html + app.js + style.css; + runs.html SSE demo)
    requirements.txt
  tests/                           ← pytest cases (322 green; toolchain e2e tests skip-guard where a compiler is absent)
    conftest.py                    ← loads dash-named scripts via importlib
    test_spike_harness.py          ← the engine: adapters, backends, orchestrate, multi-language e2e (Java/Python/Go/Rust/JS)
    test_endpoints.py              ← FastAPI TestClient smoke suite (auth, findings, targets, SSE, /api/orchestrate)
    test_orchestrator.py           ← the converged self-correcting orchestrate control flow
    test_classify.py, test_score.py, …  ← Cell #1 script unit tests (and more)
  cell-1/                          ← generated (see Makefile rules for what lands where)
  targets/jackson-databind/        ← cloned target (created on first `make`)
  .venv/                           ← project venv (created by `uv venv`)
```

**Running the tests:**

```bash
.venv/bin/python -m pytest tests/        # full suite (322; toolchain e2e tests skip-guard)
.venv/bin/python -m pytest tests/ -v     # verbose
.venv/bin/python -m pytest tests/test_find_match.py  # single file
```

These tests pin the load-bearing scoring functions (`score()`, `classify()`, `find_match()`, `extract_yaml_block()`, `_stub_findings()`, `_finding_file()`, `_lib` keyword utilities), the reproducer-builder (`tests/test_repro_builder.py`), the fix-builder (`tests/test_fix_builder.py`), the self-correcting orchestrator (`tests/test_orchestrator.py`), the dedup/CWE auto-suggest (`tests/test_suggest_gates.py`), the review-fix regressions (`tests/test_review_fixes.py`, `tests/test_p1_fixes.py`), and the headless retry/parallel-batch + CLI layer (`tests/test_claude_driver_retry.py`, `tests/test_backtest_batch.py`, `tests/test_hunt_repro_cli.py`).

---

## After Phase 0 Cell #1

The final report's recommendation drives the next move:

- **Proceed to Cell #2** (Drools × correctness) — run the same loop against a different target. See `phase-0-scope.md` §1 for the 4×4 matrix.
- **Phase 0 retry** — try a different target/angle pair with the same week-1 budget per scope §6.
- **Kill Phase 0** — write post-mortem at `cell-1/post-mortem.md`. The loop isn't producing signal over free tools.

Phase 1 (internal risk assessment across the organization's SBOM) only starts after Phase 0 produces at least one passing cell.
