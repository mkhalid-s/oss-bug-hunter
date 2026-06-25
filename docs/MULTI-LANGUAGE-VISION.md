# OSS Bug Hunter — Final Plan v3: Multi-Language Engine + Visual Orchestration App

**Status:** v3 (hardened after 4-perspective red-team) · **Date:** 2026-06-06 · **Owner:** maintainer@example.com

> v3 supersedes v2. It incorporates a 4-perspective red-team (portability,
> frontend, backend/concurrency, adapter design) that scored v2 at 30/55/58/38
> and surfaced 5 day-one blockers. Every blocker below has a committed
> resolution. **Decisions are LOCKED** (see §0).

## 0. Locked decisions (2026-06-06)

| Decision | Choice | Rationale |
|---|---|---|
| Frontend stack | React + TS + Vite + **Mantine** + TanStack Query + **CodeMirror 6 + diff2html** + SSE | Real product app; CodeMirror (not Monaco) keeps the laptop bundle small for a read-mostly viewer; Monaco lazy-added later only if in-app editing is needed. |
| **Execution backend** | **Auto-detect `docker → podman(rootless) → local`**; `local` is **trust-gated** (untrusted targets can never use it) | The only design that genuinely runs in the devcontainer (no Docker daemon) AND on the laptop, without losing isolation for untrusted code. |
| Primary environment | **Portable-first** — validate on **macOS host AND devcontainer at every milestone** | "Runs everywhere" is a hard requirement, not aspirational. |
| Backend runtime | In-process `ThreadPoolExecutor` worker + **SQLite (WAL)** + per-run pub/sub | Single-user laptop app; no broker (no Redis/Celery), no async rewrite. |

## 1. Vision

Point the hunter at **any** OSS repo (Java, Python, Go, Rust, JS/TS), have it hunt
**correctness bugs** (later: **missing features**), produce a **runnable
reproducer + minimal fix** validated by **non-AI validators in a sandbox**, and
let an engineer **drive and triage it visually**, ending in a **pull request** —
running efficiently on a laptop and inside a devcontainer alike.

## 2. Architecture: two tracks + one seam

```
   WS1 ENGINE (Python)                         WS2 VISUAL APP (React SPA)
   adapters · exec backends · hunt             targets · runs · findings board
   gates · sandbox validators · recon          CodeMirror/diff2html · live logs
              │            ▲                              │     ▲
              │   ┌────────┴───────────────── REST ───────┘     │
              │   │                                              │
              ▼   │                              ┌─── SSE ───────┘
      ┌───────────┴──────┐              ┌────────┴─────────┐
      │ EXEC BACKEND     │              │ THE SEAM         │  job/run model +
      │ docker/podman/   │              │ Job/Run API +SSE │  SSE streaming is the
      │ local (gated)    │              └──────────────────┘  contract both build to
      └──────────────────┘
```

**Two seams, built first, shared by both tracks:** (a) the **execution-backend
abstraction** (engine side) and (b) the **job/run + SSE API** (app side). Freeze
both contracts in M0, then the tracks proceed in parallel.

---

## 3. WS1 — Multi-language engine

### 3.1 Execution-backend abstraction (B1 fix — the portability keystone)

```python
# tool/exec_backend.py
class ExecBackend:
    name: str                                   # "docker" | "podman" | "local"
    @staticmethod
    def detect() -> bool                        # is this backend usable here?
    def supports_untrusted() -> bool            # local -> False
    def build_image(spec) -> str                # docker/podman; local -> no-op
    def run(argv, *, cwd, network, mem_limit, name, log_sink) -> int
    def kill(name) -> None                      # cancellation (see B4)
```

- **Auto-select order:** `docker` (if `docker info` ok) → `podman` (rootless, the
  devcontainer fit, no daemon/socket/path-translation) → `local`.
- **`local` is trust-gated:** refuses untrusted targets; Linux uses
  `unshare --net` (≈`--network none`) + `prlimit`/`ulimit` + a scratch HOME so
  caches aren't poisoned; macOS prints a loud "reduced isolation" warning and
  runs only for `trusted: true` targets. Mirrors the existing
  `REPRO_ALLOW_HOST_NET=1` opt-in pattern.
- **The chosen backend is recorded into every run** so the UI/audit shows how a
  finding was validated.
- **Backend matrix** (env × backend × trust → isolation):

  | Env | Untrusted target | Trusted target |
  |---|---|---|
  | Linux / CI | docker or rootless podman (full) | same, or local |
  | devcontainer (no daemon) | **rootless podman** (full) | podman, or **local** |
  | macOS laptop | docker/podman = Linux VM (full) | docker, or local-`ulimit` (reduced) |
  | remote/CI sandbox | strongest isolation, but minutes/run — batch only, not interactive |

### 3.2 Harness Adapter (B5 fix — interface that holds across 5 languages)

```python
# tool/adapters/base.py
from enum import Enum
class Outcome(Enum):
    PASSED = "passed"; FAILED = "failed"; NO_TESTS = "no_tests"
    BUILD_ERROR = "build_error"; DEP_ERROR = "dep_error"; TOOL_ERROR = "tool_error"

@dataclass
class TestVerdict:                 # outcome ENUM, not raw Surefire counts
    outcome: Outcome
    tests_run: int; failures: int; errors: int; skipped: int
    raw_summary: str

@dataclass
class RunnableTestId: ...          # what test_argv consumes (path + selector)

class HarnessAdapter:
    language: str
    def detect(repo_path) -> float
    def base_image() -> str                                   # toolchain image (default; per-target override allowed)
    def setup_argv(repo_path) -> list[str] | None             # venv/install/toolchain-select (per TARGET, not just lang)
    def prefetch_argv(repo_path) -> list[str] | None          # warm caches (bridged net), then freeze offline
    def cache_dirs() -> list[str]                             # .m2 / pip / GOMODCACHE / CARGO_HOME / npm — persisted, SHARED
    def build_argv(repo_path) -> list[str] | None             # compile/typecheck -> BUILD_ERROR distinct from FAILED
    def place_reproducer(repo_path, finding_location, body) -> RunnableTestId   # file-vs-edit, computes pkg/module, returns id
    def test_argv(test_id: RunnableTestId) -> list[str]
    def parse_result(stdout) -> TestVerdict                   # per-lang; emits Outcome enum
    def patch_allowed_globs() -> list[str]                    # source layout (allow)
    def patch_denied_globs() -> list[str]                     # manifests/lockfiles/CI — block supply-chain injection
    def baseline_scanners() -> list[ScannerSpec]
    def repro_block_lang() -> str                             # fenced-code tag for extraction
    def prompt_templates() -> dict                            # {hunt, repro, fix} per-language prompt assets
```

Key red-team-driven changes vs v2:
- **`TestVerdict.outcome` is an enum.** Most languages have no single
  "Tests run: N" line; the reproduce/fix inversion logic keys off `outcome`, so
  compile-error vs test-fail vs no-test-collected stops being re-derived from
  counts in every adapter. *(keystone)*
- **Cache/setup lifecycle added** (`setup/prefetch/cache_dirs/build`): the
  generic runner does **warm(bridged) → freeze(offline) → build → test**. Every
  ecosystem needs this; v2 had no method for it.
- **`place_reproducer()` replaces `repro_relpath()`**: returns the runnable id;
  owns file-vs-edit choice + package/module derivation (Go puts `*_test.go` beside
  source; Rust may *edit* a `src/*.rs` to add `#[cfg(test)] mod`).
- **`patch_denied_globs()`** added: globs alone can't say "add a test anywhere but
  never touch `pyproject.toml`/`go.mod`/`package.json`." The language-agnostic
  hunk-shape rejection (symlink/mode/rename/`.git`) stays in the generic runner.

### 3.3 The generic runner (B-back-compat fix)
`tool/run_harness.py` keeps the hardened sandbox logic (network allowlist,
**portable Python `fcntl.flock`** instead of the macOS-absent `flock(1)`, pristine
reset, patch containment, host-path resolution via `/proc/self/mountinfo` not a
hard-coded `/workspaces` prefix). **Phase A is wrapper-preserving:**
`run-repro.sh`/`run-fix.sh` become thin wrappers that exec `run_harness.py`,
keeping their CLI + env + **exact exit codes**; we prove **ec-1 parity (exit code
+ verdict diff)** before deleting anything. No flag day with the 17-step Make
pipeline. *(Side benefit: kills the macOS `bash 3.2` blocker.)*

### 3.4 Reused vs new — corrected honesty
| Genuinely reused | New / per-language greenfield (was mislabeled "reused" in v2) |
|---|---|
| Loop, gates (file-coverage, self-consistency, novel-over-baseline) | Per-language **prompts** (hunt/repro/fix are deeply Java/Jackson-specific today) |
| Recon (git churn), dedup (OSV/GitHub) | **Baseline scanners** — Semgrep is multi-lang BUT **not actually wired in code yet**; greenfield |
| Patch *format* (unified diff) | Per-lang scanner invocation + output parsing (SARIF/JSON/regex/XML) |

### 3.5 Resource model (Portability MAJOR fix)
- **Shared, read-mostly package caches** mounted across worktrees (not per-worktree
  `.m2`) — avoids N cold dependency trees.
- **Concurrency default ≤ 2–3** (not 10): the container engine is itself a 2–4 GB
  VM on macOS/devcontainer; Maven `-Xmx2g` × parallel runs OOMs.
- **Per-validator `--memory` limit + lower `-Xmx`**; OOM (exit 137/125) is
  reported **as OOM**, not as a generic tooling error.
- **Global semaphore** caps total concurrent `claude -p` + sandbox procs across
  all runs (today each run can fan out to 10 with no global ceiling).

### 3.6 Rollout order
**Java (done) → Python → Go → Rust → JS/TS.** Python/Go first (most uniform test
tooling + clearest `Outcome` mapping); JS last (jest/vitest/mocha heterogeneity).

### 3.7 Engine phases
- **A — Adapter + backend abstraction** (refactor): `exec_backend.py`,
  `adapters/base.py` + `JavaMavenAdapter`, `run_harness.py` (wrapper-preserving),
  ec-1 parity. **Exit:** ec-1 validates identically **in BOTH macOS-docker AND
  devcontainer-podman**; bash blocker gone.
- **B — Python**: `PythonPytestAdapter` + a real Python target. **Exit:** one
  Python bug reproduced + fixed.
- **C — Front door**: `tool/targets.py` (clone @ sha, `target.yaml`, `trusted`
  flag) + `adapters/registry.py` (detect/override). **Exit:** add repo URL →
  detect → scaffold a run.
- **D — More adapters**: Go, Rust, JS/TS.
- **E — Feature-gap mode + PR automation** (`gh pr create`, identity-gated).

---

## 4. WS2 — Visual orchestration app

### 4.1 Stack — LOCKED
React + TS + Vite + **Mantine** + TanStack Query + **CodeMirror 6** (code) +
**diff2html** (patches) + **SSE**. Monaco intentionally dropped (overweight for a
read-mostly viewer); can be lazy-loaded later if in-app editing is added.

### 4.2 Backend seam (the job/run + SSE contract) — hardened
- **Concurrency (B-conc):** in-process `ThreadPoolExecutor` (created at FastAPI
  startup, `max_workers=2` default). `POST /api/runs` submits → returns `run_id`
  immediately. Job bodies are the *existing* `pipeline.py` functions. Subprocess
  calls move from `communicate()` to a **reader thread** (`for line in
  proc.stdout`) that pushes lines to a per-run queue; the async SSE endpoint
  drains via `loop.call_soon_threadsafe`. Never `await` in the worker thread.
- **Persistence (B-persist):** **SQLite (WAL)** `runs.db` —
  `runs(id,kind,params,status,step,exit,started,finished)` +
  `run_logs(run_id,seq,stream,line)`. Findings stay file-backed (`cell-1/`).
  **Startup reconciliation:** rows left `running`/`queued` at boot → `interrupted`
  (no zombie "live" runs). `schema_version` guard for U1→U4 migrations.
- **Log streaming (B-stream):** per-run pub/sub with an **append-only replay
  buffer** — on `GET /stream`, **replay buffer first, then subscribe live** (late
  subscribers are the #1 "SSE looks broken" cause). Bounded per-subscriber queues
  (drop+mark-lagged on overflow; durable copy in SQLite). Terminal
  `event: done|error` then close (else EventSource reconnects forever).
- **Cancellation/cleanup (B4 fix):** store the live `Popen` pgid on the run record;
  `POST /api/runs/{id}/cancel` does `killpg(pgid, SIGTERM→SIGKILL)`. Sandbox
  scripts run containers as **`--name oss-bh-<run_id>`** so `backend.kill()` can
  `docker/podman kill` them (today `--rm` w/o `--name` leaves them alive + leaks
  the flock 900s). Per-run **wall-clock budget**. Group-kill (not
  `proc.terminate()`) so the flock fd closes and the next run isn't deadlocked.
- **SSE auth (B2 fix):** `EventSource` **cannot** send `Authorization`. The stream
  route accepts the token via **query param** (`?token=…`) validated with
  `secrets.compare_digest`; server stays bound to `127.0.0.1`. (Alt:
  `fetch`+ReadableStream.)
- **SSE transport contract:** `Content-Type: text/event-stream`,
  `Cache-Control: no-cache`, `Connection: keep-alive`, **`X-Accel-Buffering: no`**;
  heartbeat comment every ~15s; `id:` + client reconnect with `Last-Event-ID`.
- **Endpoints:** `POST /api/runs` · `GET /api/runs[/{id}]` ·
  `GET /api/runs/{id}/stream` (SSE) · `POST /api/runs/{id}/cancel` ·
  `GET/POST /api/targets` · `GET /api/findings[/{id}]` · `POST .../transition` ·
  `POST /api/findings/{id}/pr` (identity-gated).

### 4.3 Frontend mechanics (B3 + frontend MAJORs)
- **Dev auth (B3):** all browser→API traffic goes through the **Vite proxy**
  (`server.proxy` for `/api` incl. `/stream`, no buffering), so the origin is Vite
  and FastAPI still sees loopback; the SPA gets the token via a loopback-only
  `GET /api/_devtoken` (or Vite `define`).
- **Prod serving:** `vite build` → `tool/web/dist/`; **FastAPI serves `dist/`**
  with `StaticFiles(html=True)` SPA fallback + keeps `</head>` token injection.
  **No Node at runtime** (the key "runs everywhere" guarantee).
- **Vite devcontainer config:** `server.host: true` (0.0.0.0 so VS Code forwards
  it); `server.hmr` tuned for forwarded ports (Codespaces `clientPort: 443` /
  `wss`); `node_modules` on a **named volume** (not a macOS bind mount — slow).
  Node is **dev-only**; pin Node + commit lockfile.
- **Budgets (testable "efficient"):** initial JS < ~350 KB gzip; SSE reconnect <
  5s; in-container HMR < 1s.

### 4.4 Screens / UX flow
Screens: **Targets · Run view (live timeline + streaming log) · Findings board
(kanban) · Finding detail (CodeMirror reproducer + diff2html fix + validator
output + gates + Open PR) · Activity · Settings (model/budget/backend/PR
identity).**
Flow: `add repo → detect language + trust → recon → review hot-spots → hunt
(live) → triage on board → open finding → run reproducer+fix in chosen backend
(live log, "preparing sandbox/warming caches" state) → accept → Open PR
(identity-confirmed).`

### 4.5 UI phases
- **U0 — Scaffold + seam:** Vite/React shell + job/run API + SSE (full transport
  contract) + live-log panel + **the "warming caches" run state**.
- **U1 — Run view** · **U2 — Findings board + detail (CodeMirror/diff2html)** ·
  **U3 — Targets + add-by-URL + detection** · **U4 — Open-PR + feature-gap views**.

---

## 5. Combined roadmap (parallel; validated on BOTH envs each milestone)

| Milestone | WS1 Engine | WS2 App | Sync / portable-first gate |
|---|---|---|---|
| **M0 — Seams + bootstrap** | exec-backend abstraction; `make setup` | **U0** shell + job/run + SSE | contracts frozen; SSE works through forwarded port |
| **M1 — Prove it** | **A** adapter refactor + ec-1 parity | **U1** live run view | ec-1 validates live **on macOS-docker AND devcontainer-podman** |
| **M2 — Second lang** | **B** Python adapter + finding | **U2** board + detail | Java+Python findings on the board |
| **M3 — Any repo** | **C** front door + detect + trust flag | **U3** targets + add-by-URL | add a repo in the UI, both envs |
| **M4 — Breadth** | **D** Go/Rust/JS | per-lang badges/polish | one validated finding/lang |
| **M5 — Ship loop** | **E** feature-gap + PR | **U4** Open-PR | finding → opened PR from UI |

## 6. Bootstrap & "runs everywhere" (Portability MAJOR fix)
- **One command:** `make setup` → venv + Python deps + Node + `vite build` +
  **pre-pull/build the validator image + warm language caches**.
- **Per-environment prerequisite matrix** documented (container engine *or*
  trusted-local opt-in; JDK; Node for dev only).
- First-run cache-warming surfaces as a **"preparing sandbox"** run state so the UI
  never looks hung.

## 7. Risks & residual unknowns (the last ~5–10%)
These can't be reasoned to certainty — they need the M0/M1 spike to prove:
- **Podman rootless UID/userns mapping** — resolution is now an explicit M0 task
  (`--userns=keep-id`, see §10 G2), but it must be *proven* on a real rootless
  podman, not just specified.
- **SSE through Codespaces/VS Code tunnels** (idle drops, buffering) — verify with
  a test that opens the stream *through the forwarded port*, not just localhost.
- **Go/Rust/JS adapter surprises** (no summary line; placement; JS runner detect).
- **Feature-gap validation has no clean oracle** — leans on real GitHub-issue
  demand + human triage; do not oversell as automated.
- **PR identity** — public-repo PRs MUST use the personal GitHub identity, never
  enterprise; the Open-PR action enforces an account/identity confirmation gate.

## 8. Confidence
- **v2 (4-perspective red-team): 30/55/58/38.** **v3 (verification pass): 84/100**
  — all 5 day-one blockers confirmed closed against real code, but 5 new gaps
  (G1–G5) surfaced. **v3.1 (this revision) closes G1–G5** (see §10), removing the
  hard `pipeline_lock` contradiction and promoting the podman fix onto the M0
  critical path. **Reasoned plan confidence now: ~91/100.**
- **Spike partially run (§11, 2026-06-06):** the engine half is PROVEN — backend
  auto-detect + trust-gating + the full ec-1 reproduce→fix loop ran in the
  devcontainer with **no Docker daemon** (via the `local` backend). That retires
  the biggest unknowns (B1 substrate, the Outcome enum, ec-1 itself).
  **Engine-path confidence now ~95%.** Remaining to hit 95% overall: SSE through
  the forwarded port, per-worktree lock sharding + concurrent-run proof (G1), and
  the podman `--userns=keep-id` path on the macOS host.

## 9. First moves (M0, parallel, this week)
0. **Decide the lock strategy FIRST (G1):** replace the process-global
   `pipeline_lock` with **per-worktree + per-target-state locks** so two runs on
   different targets/worktrees don't serialize. This is a prerequisite for items 3.
1. `tool/exec_backend.py` — docker/podman/local detect + run + kill; backend
   matrix; **podman uses `--userns=keep-id` (G2)**; **default trust = untrusted /
   fail-closed; jackson-databind explicitly marked `trusted: true` for M0 (G3)**.
2. `tool/adapters/base.py` — interface above (Outcome enum, lifecycle, place_reproducer).
3. Job/run model: SQLite(WAL, **single serialized writer / `BEGIN IMMEDIATE`
   retry, G5**) + `ThreadPoolExecutor` worker (over per-worktree-locked jobs) +
   `POST /api/runs` + `GET /api/runs/{id}/stream` (full SSE contract) + cancel.
4. **UI U0** — Vite/React shell rendering a live SSE log through the Vite proxy.
5. `make setup` bootstrap + per-env prereq matrix.
6. **Walking-skeleton gate:** validate ec-1 via `run_harness.py` on **macOS-docker
   AND devcontainer-podman**, watched live in U0, with **two concurrent runs that
   don't deadlock (G1)**. Green → declare 95%.

---

## 10. Verification-pass fixes (v3.1, 2026-06-06)

The v3 verification pass scored 84/100 and found 5 new gaps the v3 changes
introduced. Resolutions:

- **G1 (HIGH) — global `pipeline_lock` contradicts `ThreadPoolExecutor(max_workers=2)`.**
  Today `pipeline.py:55-93` holds an exclusive `fcntl.flock` on
  `cell-1/.pipeline.lock` for a step's entire (up-to-3600s) lifetime with a 30s
  acquire window — so a second worker would `RuntimeError` after 30s, making the
  stated concurrency impossible. **Fix:** shard the global lock into
  **per-worktree + per-target-state locks**; a run locks only its own
  worktree/target dir, not all pipeline state. Concurrency ≤2-3 then means
  *different targets/worktrees in parallel* (the realistic case). Decide this in
  M0 item 0 — it's a prerequisite, not a later cleanup.

- **G2 (MED, on the M0 critical path) — podman rootless UID/userns vs the
  Dockerfile `UID/GID` + `:rw` bind.** Rootless podman maps in-container UID 1000
  to a high host UID via `/etc/subuid`, so `chown repro:repro /work` + a `:rw`
  bind mismatch and `mvn` writes fail. **Fix:** podman backend runs with
  **`--userns=keep-id`** (maps the container user to the invoking host user, so
  bind ownership lines up); the exec-backend normalizes this so adapters/scripts
  don't special-case it. Promoted from "residual unknown" to an explicit M0 task
  and M0 exit-gate item.

- **G3 (MED) — trust default before `target.yaml` exists.** The `trusted` flag
  lives in `target.yaml` (Phase C/M3) but `local` backend exists at M0. **Fix:**
  **default = untrusted, fail-closed** — `local` is refused unless a target is
  explicitly `trusted: true`. For M0/M1, jackson-databind is operator-vetted, so
  mark it `trusted: true` via a minimal pre-M3 trust source (a one-line config),
  enabling `local` where no container engine exists without opening a hole.

- **G4 (LOW) — concurrent cold-cache writes.** Shared caches + ≤2-3 concurrency
  means two first-runs of the same language can write a cold shared
  `.m2`/`GOMODCACHE`/`CARGO_HOME` at once (not all concurrent-write-safe).
  **Fix:** `prefetch` holds a **per-language cache lock** (warm-once); subsequent
  runs read-mostly.

- **G5 (LOW) — SQLite WAL multi-writer.** Two worker threads writing `run_logs`
  plus the SSE drain reading. **Fix:** a **single serialized DB writer** (a write
  queue) or `BEGIN IMMEDIATE` + retry-on-`SQLITE_BUSY`.

**Result:** G1 (the only HIGH) and G2 (critical-path) are resolved at the plan
level; G3–G5 have standard, specified fixes. Reasoned confidence ~91/100; the
residual is the empirical M0 spike (§7, §8).

---

## 11. M0 walking-skeleton spike — RESULTS (2026-06-06)

Built `tool/exec_backend.py` + `tool/run_harness.py` and ran them in the
**devcontainer with no Docker daemon**. Proven empirically:

- **Backend auto-detect + trust-gating works.** docker unreachable, podman absent
  → selects `local`; an *untrusted* target correctly gets *no backend* (fail-closed).
- **End-to-end reproduce→fix loop PROVEN with no daemon** (the session-long
  blocker, gone): ec-1 **reproduces** on HEAD 1c38a7d8 (raw NPE in
  `CollectionDeserializer._deserializeWithObjectId:488`), and **the fix patch
  makes the isolated test pass**. Recorded in `cell-1/hunt/validation/ec-1.yaml`
  (both gates = pass). ec-1 is a *real, reproducible, fixable* bug — though it
  still failed the self-consistency heuristic (1/3), i.e. that gate dropped a
  true positive (worth revisiting).
- **The `Outcome` enum earned its place**: it correctly classified a `BUILD_ERROR`
  (filename mismatch) as tooling, NOT a false reproduction.

**Three real defects pure reasoning missed (now drive the design):**
1. **`-Dtest` is ignored on suite-pinned targets.** jackson's pom hard-codes
   `<test>PrimarySuite</test>`, so `mvn test -Dtest=Repro_ec_1` ran the whole
   3790-test suite and the single test never ran (a *false PASSED*). **Fix:** run
   isolated via the **JUnit Platform Console Launcher** (`--select-class`). The
   adapter `test_argv`/`place_reproducer` must own per-target single-test
   invocation — this is the predicted "test discovery" gap, made concrete.
2. **Reproducer filename must match the public class** (`Repro_ec_1.java`, not
   `ec-1.java`) or it won't compile. `place_reproducer()` derives it from the
   FQCN. (The old `run-repro.sh` copied by basename and would have failed
   identically — never caught because the Docker path never ran.)
3. **Hardened devcontainer forbids `unshare --net`** (even userns) → `local`
   network isolation degrades to **offline-runner mode** (`mvn -o` + warm cache);
   and **JVM memory must be bounded via `MAVEN_OPTS -Xmx`, not `prlimit --as`**
   (the JVM reserves multi-GB virtual upfront, so `--as` kills startup).

### 11.1 The SSE seam — BUILT + PROVEN locally (2026-06-06)
Built `tool/run_store.py` (SQLite-WAL store + `ThreadPoolExecutor` worker +
per-run pub/sub) and wired it into `tool/server.py` (`POST /api/runs`,
`GET /api/runs[/{id}]`, `GET /api/runs/{id}/stream`). Verified by curl against the
running server:
- **Live SSE stream** of a job: `id:`/`event: log`/`data: {...}` frames in real
  time, terminating with `event: done` (status+exit). ✔
- **Query-token auth (B2)**: `/stream` 401s without `?token=`, streams with it;
  constant-time `compare_digest`. All other `/api/*` still require the bearer. ✔
- **Replay buffer (late subscriber)**: re-streaming an already-finished run
  replays all frames + `done` (SQLite log table = replay buffer). ✔
- **Concurrency**: two runs launched together both reach `done`, no deadlock
  (worker pool = 2). ✔
- **SSE transport contract**: `text/event-stream` + `Cache-Control: no-cache` +
  `X-Accel-Buffering: no` + 15s heartbeat + `id:` for `Last-Event-ID` resume. ✔
- **Startup reconciliation**: `running`/`queued` rows at boot → `interrupted`. ✔
- **U0 spike page** at `GET /runs` (vanilla EventSource) renders the live log;
  the production UI stays React+Vite+Mantine (§4).

### 11.2 Still to prove (needs the macOS host / your browser)
- **SSE through the VS Code forwarded port** (proven on localhost; tunnels add
  idle-drop/buffering — the heartbeat + `X-Accel-Buffering: no` are in place to
  survive it).
- **podman `--userns=keep-id`** path (devcontainer has no podman; macOS host).
- **`pipeline_lock` sharding (G1)** is only needed once job bodies call the
  `pipeline.py` step functions; the current seam jobs don't (the `validate-repro`
  job uses `run_harness`'s **per-worktree** lock, not the global one), so the
  contradiction isn't triggered yet. Shard before wrapping pipeline steps as jobs
  (≈ M3, multi-target).

### 11.3 Console-launcher isolation + fix gate — IMPLEMENTED (2026-06-06)
Defect #1 (suite-override) is now fixed in code, not just diagnosed:
- `run_harness.validate_repro` compiles, then runs **only** the target class via
  the **JUnit Platform Console Launcher** (`--select-class`) — verified it runs
  exactly 1 test (not jackson's 3790) and reports `FAILED → exit 1` (reproduces).
- `run_harness.validate_fix` added: R4 patch containment (reject
  symlink/mode/rename/.git + anything outside `src/{main,test}/java`) → `git
  apply` → recompile → isolated re-run; `PASSED → exit 0` = fix works. Verified on
  ec-1 (fix passes), worktree restored to pristine.
- Both wired as SSE job kinds (`validate-repro`, `validate-fix`); shared
  compile+run in `_compile_and_run_isolated`.
- `tests/test_spike_harness.py` (19 tests) + FastAPI `lifespan` migration; full
  suite **171 green**, no warnings.

**Engine + seam are now demonstrably working software, not a plan.** Remaining for
full M0 sign-off stays as §11.2 (forwarded-port SSE, podman on macOS).

### 11.4 Self-correcting `orchestrate` loop — IMPLEMENTED (2026-06-06)
`run_harness.orchestrate` chains the validated primitives into the agentic loop:
**reproduce → fix → (retry-with-feedback)**. The load-bearing invariant is
preserved — an optional `fix_provider(feedback, attempt)` only *proposes* revised
patches; the non-AI validators (`validate_repro`/`validate_fix`) decide pass/fail.
Outcomes `validated | not-reproduced | fix-failed | inconclusive` (exit 0/2/1/3),
exposed as a CLI subcommand, an SSE job kind, and a U0 button. 6 unit tests cover
the branches incl. retry-then-validated and retry-exhausted. Real ec-1 run:
**validated (reproduces + fix works, attempt 1, exit 0)** with no daemon. Full
suite **177 green**. The real LLM fix-builder (scripts/day3-hunt.py + claude_driver)
plugs in as the `fix_provider` — **now implemented, see §11.5.**

### 11.5 LLM fix-builder wired into `orchestrate` — IMPLEMENTED (2026-06-07)
`tool/llm_fix_provider.py` adapts the existing LLM fix-builder to the
`orchestrate` `fix_provider` hook: on a failed fix it calls
`build_fix_prompt(scaffold, repro_src, feedback)` → `claude -p` (opus/high via
`run_claude_with_retry`) → `extract_diff_block` → writes `<fid>-retry<N>.patch`
→ returns its path; `orchestrate` then applies it and re-runs the isolated
reproducer. **Invariant preserved**: the LLM only *proposes* the patch; the non-AI
`validate_fix` decides pass/fail. Exposed via `orchestrate --finding <yaml>
--max-retries N` (CLI) and a `finding_yaml` param on the SSE `orchestrate` job.
4 tests (provider writes patch / no-diff → None / claude-failure → None / and an
end-to-end test that `orchestrate` actually drives the provider: fix fails → LLM
regenerates → retry validated). Full suite **181 green**. A real LLM run wasn't
exercised here (ec-1's first patch already passes, so retry never triggers, and a
live opus/high run is slow/token-heavy) — the wiring is proven by the integration
test; a live demo needs a finding whose first patch fails.

### 11.6 React U0 visual app — BUILT (2026-06-07)
The locked WS2 stack is now real: `tool/webapp/` is a React 18 + TS + Vite 5 +
**Mantine 7** + TanStack Query SPA. U0 scope delivered: an AppShell with a polling
**runs list**, a **live-log panel** (EventSource on `/api/runs/{id}/stream`,
relying on the server's replay buffer for late subscribers), and run triggers
(demo / reproduce ec-1 / orchestrate ec-1). It **builds to `tool/webapp/dist/`**
(JS ~86 KB gzip) and **FastAPI serves it at `/app`** — token injected into
index.html, hashed assets mounted at `/app/assets`, SPA fallback, **no Node at
runtime** (the key portability guarantee). Dev mode (`npm run dev`, Vite proxy +
`server.host:true`) is configured for the forwarded port but the served-build path
is the default. `.npmrc` uses public npm (default Artifactory needs auth);
`node_modules`/`dist` are gitignored.

**Next U-phases** (U1–U4): finding detail with CodeMirror + diff2html viewers, the
findings kanban board, the targets/add-by-URL screen, and the Open-PR action.

### 11.7 U2 — findings board + detail — BUILT (2026-06-07)
Backend: `tool/findings.py` reads the validation scaffolds + reproducer/patch and
derives the kanban **column from the GATES** (proposed → reproduced → fixed →
pr-ready; pr-ready only if the fix passed AND not a dupe AND it survived
self-consistency) — the non-AI validators decide placement, never an LLM label.
Endpoints `GET /api/findings` (list) and `GET /api/findings/{id}` (detail +
reproducer source + patch text; path-safe id, traversal → 404).
Frontend: a Runs/Findings **SegmentedControl**; the **findings board** (4 columns,
cards with angle/summary/location/final-status); a **finding detail modal** with
gate badges, evidence, the reproducer in **CodeMirror** (Java) and the fix in
**diff2html**, plus Reproduce/Orchestrate buttons that kick a run for that finding
and jump to its live log. `FindingDetail` is **lazy-loaded** so CodeMirror +
diff2html ship in a separate chunk — initial bundle **94 KB gzip** (was 261),
viewer chunk 167 KB gzip on demand. tsc clean. ec-1 shows in the **Fixed** column
(reproduced+fixed but failed self-consistency, so not pr-ready — honest).

### 11.8 U3 — targets / add-by-URL — BUILT (2026-06-07)
Engine front-door `tool/targets.py`: `detect_language` (pom/build.gradle→java,
pyproject/setup→python, go.mod→go, Cargo.toml→rust, package.json→js),
`list_targets`/`get_target` (scan `targets/*` + a metadata SIDECAR under
`targets/_meta/<name>.yaml` — NOT inside the clone, which the validators'
`git clean` would wipe — plus the live git sha), and `add_target` (clone by URL
→ detect → write meta). Hardened: path-safe name, `git clone --progress -- <url>`
arg-injection guard, **trust fail-closed** (`trusted:false` by default, so `local`
refuses it). Endpoints `GET /api/targets[/{name}]` and `POST /api/targets`
(validates the URL, runs the clone as a streamed **add-target** job). UI: a third
**Targets** segment listing target cards (language/sha/trusted badges) + an
add-by-URL form (URL + optional sha + trusted checkbox) that streams the clone as
a run. jackson-databind backfilled (java, trusted). 5 tests (detect + local-clone
add + fail-closed + flag-URL reject + duplicate reject). tsc clean; 191 tests green.

**Remaining UI:** U4 — Open-PR (identity-gated to the personal GitHub for public
repos). Engine adapters (Python/Go/Rust/JS HarnessAdapter classes) + the
container-execution path remain the multi-language follow-ons.

### 11.9 U4 — Open-PR preview + identity gate — BUILT (2026-06-07)
`tool/pr.py` assembles, **read-only**, everything a PR would contain — upstream
`owner/repo` (from the target's repo URL), fork (`mkhalid-s/<repo>`), branch,
title, and a full PR body (summary / evidence / reproducer / fix diff /
validation) — plus the GATES that must pass first: a **keeper check** (fix
gate=pass AND not a dupe AND survived self-consistency) and a **GitHub identity
gate** (active gh account, `is_personal` == `mkhalid-s`, `GH_TOKEN`-pins-enterprise
detection). It returns `ready`, `blockers`, and the exact `manual_steps` (unset
GH_TOKEN → switch account → branch/apply/commit/fork/push → `gh pr create`).
It NEVER pushes or runs `gh pr create` — per
`.claude/rules/confirm-gh-account-before-commit.md` that is a hard gate requiring
explicit human confirmation. `GET /api/findings/{id}/pr-preview`; UI: an "Open PR"
button in the finding detail expands the preview + a loud red/green gate.

**Proof the gate works:** ec-1's preview is **blocked** for three correct, distinct
reasons — not a keeper (failed self-consistency), `GH_TOKEN` set (enterprise pin),
and the active account is `enterprise_account` (enterprise, EMU-blocked on public repos)
not `mkhalid-s`. 4 tests; 195 Python tests green.

**The visual app now spans the whole loop: Targets → Runs (live) → Findings board
→ finding detail (reproducer + fix + gates) → Open-PR preview.** Remaining product
work is the multi-language engine (Python/Go/Rust/JS `HarnessAdapter` classes +
the container-execution path) — the UI/seam are language-agnostic and ready.

### 11.10 M2 — Python HarnessAdapter (second language) — BUILT (2026-06-07)
`tool/adapters.py` now hosts the shared verdict contract (`Outcome`/`TestVerdict`)
+ a `PythonPytestAdapter` (place reproducer → `pytest <selector>` → parse
passed/failed/errors → Outcome; allowed `.py` / denied manifest globs).
`run_harness` gained a `lang` param: `lang="python"` routes through the adapter
(place → run pytest → parse), Java keeps its console-launcher path. Generic
per-language patch containment (`_contained_generic`). CLI `--lang java|python`;
server job kinds pass `lang` from params.

**Proven end-to-end** on a synthetic Python target (`targets/pybug-demo`, a
`running_max([])` IndexError bug): `validate-repro` → FAILED (reproduces),
`validate-fix` → PASSED (fix works), `orchestrate` → **validated**, all daemonless
via the local backend. Fixed a real bug found doing it: running `run_harness.py`
as `__main__` while `adapters` imported it by name created two `Outcome` enum
copies, breaking orchestrate's `is`-comparisons — resolved by moving the contract
into `adapters.py` (single identity; `rh.Outcome is adapters.Outcome`). 3 M2 tests;
**198 Python tests green**; Java path re-verified (ec-1 still reproduces).

**The engine is now genuinely multi-language** (Java + Python) behind one adapter
interface. Remaining: Go/Rust/JS adapters (same shape; each needs per-target env
setup — venv/go-mod/cargo) + the container-execution path for untrusted targets +
UI wiring so a Python finding's run buttons pass `lang=python`.

### 11.11 Go HarnessAdapter (third language) — BUILT (2026-06-07)
`adapters.GoTestAdapter`: places the reproducer as `repro_<name>_test.go`,
extracts the `Test…` func name as the selector, runs
`go test -run ^Sel$ -count=1 -v ./...`, and parses `--- PASS:`/`--- FAIL:`/
`[build failed]` → Outcome (`.go` allowed, `go.mod`/`go.sum`/`.github/` denied).
`--lang go` on the CLI; the same `lang` param flows through validate/orchestrate
and the server jobs.

**Proven end-to-end** (Go 1.26) on a synthetic target (`targets/gobug-demo`, a
`RunningMax([])` index-out-of-range panic): validate-repro → FAILED (panic
reproduces), validate-fix → PASSED, orchestrate → **validated**, daemonless via
the local backend. 4 Go tests; **202 Python tests green**.

**The engine now spans Java + Python + Go behind one adapter interface** — the
multi-language thesis is demonstrated across three ecosystems with no changes to
the loop/gates/seam/UI. Remaining adapters (Rust `cargo test`, JS jest/vitest) are
the same shape; the open structural items stay the container-execution path (for
untrusted targets) and per-target env setup (venv / go mod / cargo).

### 11.12 Review + refine pass 3 (2026-06-07)
A 3-agent red-team of the not-yet-reviewed code (adapters, findings, targets, pr,
the React app) found a handful of real issues — all fixed:
- **Security**: `targets` allowed `..` → **path traversal** (HIGH; `get_target("..")`
  read the repo root) — now name-validated + containment-checked; URL **transport
  allowlist** (https/ssh/git/file; `ext::`/`fd::` refused — command-exec) +
  `GIT_ALLOW_PROTOCOL`; failed clone now cleans up its half-written dir.
- **Identity gate**: `pr.gh_identity` parsed the *first* account, not the **Active**
  one (could falsely read personal) — now selects the `Active account: true` block;
  `_owner_repo` anchored (was matching `github.com` anywhere → injection/lookalike).
- **Adapters**: Go **panic-outside-a-test** → FAILED (was silent NO_TESTS); Python
  patch-deny now includes `conftest.py` (test-collection tamper); stale
  `from enum import Enum` removed.
- **Findings carry `language` + `target`**, and `get_finding` picks the reproducer
  extension by language + guards the YAML load. The board is now genuinely
  multi-language: **ec-1 (java/Fixed), py-1 (python/PR-ready), go-1 (go/PR-ready)**.
- **Web app**: `api.ts` now guards `r.ok`/non-JSON; **`findingRunParams` is
  language-aware** (was hardcoding a Java FQCN + jackson worktree for every
  finding → would mis-run Python/Go); finding/PR panels show error states; the PR
  identity badge goes red whenever `GH_TOKEN` is set or the account isn't personal.

207 Python tests green, tsc clean, build OK. Documented limitations (per-target
env setup for src-layout Python / multi-package Go; `pipeline.py`'s separate
Java-only orchestrator) remain noted follow-ons, not regressions.

### 11.13 Container-execution path (untrusted targets) — WIRED (2026-06-08)
The adapter (non-Java) path now runs in a **container** for untrusted targets:
`run_harness._adapter_run` selects the backend by trust — **trusted → local**
(fast, proven), **untrusted → docker/podman** — and for a container backend it
builds the per-language sandbox image (`tool/repro-py`, `tool/repro-go`
Dockerfiles, UID/GID-matched non-root), mounts the worktree at `/work`, and runs
the adapter's **container argv** there (Python uses the image's `python`, not the
harness's `sys.executable`; Go's `go test` is identical). `_host_bind` adds
host↔container path translation for docker-outside-of-docker (no-op natively).
Fail-closed preserved: untrusted + no engine → clear TOOL_ERROR (not local).

**Verified here:** the container **spec assembly** is unit-tested with a fake
backend (image=`oss-bug-hunter-go:latest`, cwd=`/work`, worktree mounted, container
argv, UID/GID build-args) and the fail-closed path; the **local** path is
re-proven end-to-end (Go + Python orchestrate → validated; Java ec-1 reproduces).
209 tests green. **The actual container RUN needs a daemon (docker/podman) and is
validated on the macOS host** — none is present in this devcontainer. Java
in-container (the host-classpath console-launcher flow) remains the documented
follow-on; Java stays local (works for trusted targets).

### 11.14 Orchestrators converged (2026-06-08)
There were two orchestrators: the multi-language `run_harness.orchestrate` (used by
the CLI + the SSE job) and a **Java-only** `pipeline._orchestrate_finding` (the
`/api/orchestrate` + MCP **product path**, shelling to run-repro.sh/run-fix.sh).
Converged onto ONE engine: `pipeline._orchestrate_finding` now reads the finding's
`language`/`target` from the scaffold, keeps the Java LLM builders as pre-steps
(reproducer/initial patch) + wires `llm_fix_provider` as the retry provider, and
**delegates the reproduce→fix→retry loop to `run_harness.orchestrate(lang=…)`**.
Non-Java findings require a pre-existing reproducer + patch (no per-language
builder yet) and skip straight to validation. The outcome vocabulary is preserved
(`fixed`/`does-not-reproduce`/`fix-failed-after-retries`/`inconclusive`).

**Proven:** `pipeline.orchestrate` AND `POST /api/orchestrate` now resolve py-1
(python) and go-1 (go) to `fixed`/validated — the product/MCP path is
multi-language on a single engine. `test_orchestrator.py` + the 2 R5 tests
rewritten to the converged design; **210 Python tests green**; server + MCP import
clean. The old run-repro.sh/run-fix.sh are now only the thin wrappers; the
orchestrator no longer calls them. (Java still runs local via the console
launcher; Java-in-container remains the one open execution item.)

### 11.15 Rust HarnessAdapter (fourth language) — BUILT (2026-06-08)
`adapters.RustCargoAdapter`: places the reproducer as an integration test
`tests/repro_<name>.rs` (uses the crate's public API), selector = the test-binary
stem, runs `cargo test --test <stem>`, parses `test result: ok/FAILED` +
`error[E…]`/`could not compile` → Outcome (`.rs` allowed; `Cargo.toml`/`Cargo.lock`
denied). Container image `oss-bug-hunter-rust` (`tool/repro-rust`). `--lang rust`
on the CLI; flows through validate/orchestrate/server/findings/pipeline like the
others. Synthetic target `targets/rustbug-demo` (`running_max(&[])` slice panic) +
reproducer + patch created.

**Honest caveat (as written 2026-06-08 AM):** `cargo` was NOT installed in this
devcontainer, so — unlike Java/Python/Go — the Rust adapter was **built +
unit-tested** (parse/place/containment, 4 tests; **214 total green**) with the
actual `cargo test` run intended to be **validated on a Rust host**.
**Update (2026-06-08, same day):** `cargo 1.95.0` was then installed here, and
Rust now validates **end-to-end locally** — same tier as Java/Python/Go/JS; the
"host-only" caveat above no longer applies (see the §11.17 table). The run:
```
python tool/run_harness.py orchestrate targets/rustbug-demo rs-1 \
  cell-1/hunt/repros/rs-1.rs cell-1/hunt/patches/rs-1.patch --trusted --network none --lang rust
# expect: reproduces (panic) -> fix passes -> validated
```
The engine now spans **Java + Python + Go + Rust** behind one adapter interface.
JS was the remaining language at this point — **delivered next in §11.16**, via
the stdlib `node --test` (not jest/vitest; third-party-runner detection is a
follow-on).

### 11.16 JS HarnessAdapter (fifth language) + review pass 4 (2026-06-08)
`adapters.JsNodeTestAdapter`: places the reproducer as `repro_<name>.test.js`,
runs the built-in `node --test --test-reporter=tap`, parses TAP `# pass N` /
`# fail N` → Outcome (`Cannot find module`/`SyntaxError`/`ERR_MODULE_NOT_FOUND`
→ BUILD_ERROR); `.js/.cjs/.mjs/.jsx/.ts/.tsx` allowed, `package.json` /
lockfiles / `.github/` denied. Container image `oss-bug-hunter-js`
(`tool/repro-js`). `--lang javascript` on the CLI; flows through
validate/orchestrate/server/findings/pipeline like the others. Synthetic target
`targets/jsbug-demo` (`chunk([], n)` infinite-loop / off-by-one) + reproducer
(`js-1`) + patch. **Proven end-to-end locally** (Node 24 is in this
devcontainer): reproduces → fix → validated. **The engine now spans all five
planned languages — Java · Python · Go · Rust · JS — behind one interface.**

Review pass 4 (engine + system, 2 agents) closed out alongside the JS work. The
**code-correctness findings were fixed and pinned by regression tests**: Rust
panic/abort with no summary line → FAILED (was inconclusive); Python verdict now
anchored to pytest's summary line so a stray `"3 errors"` in test output can't
flip it (+ `errors?` plural); Go scoped to the reproducer's package (`.` not
`./...`) so an unrelated build break can't mask the verdict; `fix-failed` added
to `_CONCLUSIVE_OUTCOMES` (a reproduced-but-unfixed bug IS validated, distinct
from an inconclusive env failure); `pristine()` no longer clobbers the worktree
lock/.m2. The **split-brain orchestrate was converged**: the React UI's
Orchestrate now routes through `pipeline.orchestrate_finding` (scaffold-driven,
self-correcting) via `finding_id`, the same engine as `/api/orchestrate` + MCP —
previously it called `run_harness.orchestrate` directly (degraded, no retry).
Added the `tests/test_endpoints.py` TestClient smoke suite. **228 tests green** at
the time of this entry (later 229, once a cargo-guarded Rust e2e was added — see §11.18).
The remaining review items were **documentation-accuracy** (this pass): the
stale-claims sweep of README (test count 152→228, MCP tool count 13→18,
"needs Docker" → "validates locally (trusted) / Docker (untrusted non-Java)",
a top-of-README multi-language+app status note), this §11.16 + the proof-status
legend below, and `LEGACY` headers on `scripts/run-repro.sh`, `scripts/run-fix.sh`,
and the `day3-hunt.py run-repros`/`run-fixes` subcommands (not used by the
converged orchestrator).

### 11.17 Proof status — what is proven HOW (read this before trusting a ✓)
The devcontainer reality bounds what could be proven here: **Docker daemon
unreachable, podman absent, `unshare --net` forbidden; available: Node 24,
Go 1.26, JDK 21, mvn 3.9.9, pytest (.venv), and cargo 1.95.0 (added 2026-06-08).** So:

| Capability | Proof level |
|---|---|
| Java reproduce→fix→orchestrate (ec-1) | **end-to-end, local, in this devcontainer** |
| Python (py-1) | **end-to-end, local, in this devcontainer** |
| Go (go-1) | **end-to-end, local, in this devcontainer** |
| JS (js-1) | **end-to-end, local, in this devcontainer** |
| Rust (rs-1) | **end-to-end, local, in this devcontainer** (cargo 1.95.0 arrived 2026-06-08; `run_harness orchestrate … --lang rust` → reproduces → fix → validated) |
| Container backend (untrusted → docker/podman) | **unit-tested here** (backend select, path-bind, spec assembly); a real daemon run is **host-only** — no reachable daemon here |
| SSE seam (run_store, replay, two concurrent runs) | **proven locally**; forwarded-port `EventSource` in a real browser is **host-only** |
| podman rootless `--userns=keep-id`, macOS host paths | **host-only** (not exercised here) |

"✓ / BUILT / PROVEN" elsewhere in §11 means **proven by automated test in this
devcontainer** unless the entry says *host* / *host-only* / *manual*, in which
case it was exercised on the macOS host or is still pending a host run. When in
doubt, this table is authoritative.

### 11.18 Review pass 5 (12 perspectives) + Rust now end-to-end local (2026-06-08)
A 12-perspective review of the §11.16 docs-accuracy pass (run as local agents —
Bedrock creds were expired). **0 P0, 3 P1, 6 P2, 6 P3.** The numeric corrections,
LEGACY markers, and convergence/security claims all verified TRUE. The headline
finding was ironic: **§11.17 itself had gone stale** — `cargo 1.95.0` was installed
in the devcontainer on 2026-06-08 (after §11.15/§11.17 were written), so "cargo
absent / Rust host-only / validated on a Rust host" was false on all three counts.

Resolved by **making it true rather than rewording**: ran the Rust orchestrate
(`rs-1`) → reproduces → fix → **validated** end-to-end locally (real cargo compile
+ test, daemonless); flipped `cell-1/hunt/validation/rs-1.yaml` gates to pass /
`final_status: validated`; corrected the §11.17 environment line + Rust row + the
§11.15 caveat + the CHANGELOG bullet; and added a `shutil.which("cargo")`-guarded
`test_rust_adapter_validates_synthetic_target` so Rust e2e is now **test-backed**
like Java/Python/Go/JS (suite → **229** where cargo is present, **228** where it
is not — the e2e tests skip-guard, so test-count claims were softened from a hard
number). **All five languages are now proven end-to-end *in this devcontainer*.**

Also fixed from the review: README "Project layout" tree (7/12 consensus — it
omitted the entire engine + React app; now lists `run_harness`/`adapters`/
`exec_backend`/`run_store`/`findings`/`targets`/`pr`/`llm_fix_provider`/`webapp/`
and distinguishes `web/` legacy from `webapp/`); the stale "orchestrate validates
via `day3-hunt.py run-repros`/`run-fixes`" claim (README — it's `run_harness.
orchestrate` + `pristine()`); the undocumented multi-language CLI (`run_harness.py
… --lang`) + the `/app`-vs-legacy-dashboard conflation; the untrusted-Java gap in
the one-line backend note; the JS jest/vitest→`node --test` framing; and the
CHANGELOG `[Unreleased]` date span. Deferred (judgment/minor): the broader
hardcoded-count rot (P2-5, partially mitigated), the `mcp_server.py` module
docstring tool list (10→18), and the `/app` build-step doc.

---

### 11.19 Real-repo pilot — `chopratejas/headroom` (2026-06-08)
First pilot on a real external repo (operator-trusted for the run; no container
here, so it ran locally). **headroom** — "the context compression layer for AI
agents", Apache-2.0, ~3.1k files — is a **polyglot monorepo**: a Rust Cargo
workspace (`headroom-core`/`-proxy`/`-parity`/`-py`), a root Python package, and
a TypeScript SDK + docs site.

**What worked:** `cargo metadata` enumerated the workspace; `cargo fetch` pulled
the dep tree from crates.io through the proxy; workspace-scoped `cargo test -p
headroom-core --test cache_control` **compiled 100+ crates** correctly. The engine
machinery is sound on a real repo.

**Where it stopped (environmental, not an engine bug):** a transitive dep
`ort-sys` (ONNX Runtime, pulled non-optionally via `fastembed`) has a `build.rs`
that **downloads prebuilt binaries at compile time** from `cdn.pyke.io` using
**rustls + bundled webpki roots**, which do not trust the corporate proxy CA (the
same wall that blocks Python's TLS here) → `invalid peer certificate:
UnknownIssuer`. Combined with a heavy native tree (ONNX, AV1 codecs), **headroom-
core does not build in this devcontainer**; a clean baseline needs a host/container
with unrestricted network (or the proxy CA in rustls' path). This is the §11.17
"host-only" reality, concretely demonstrated.

**Four M5 requirements the pilot surfaced** (a synthetic target would not have):
1. **Per-component language detection** — `detect_language` returned `python` for a
   repo whose tractable surface is Rust. **SHIPPED:** `targets.detect_components()`
   (monorepo-aware: headroom → 4 rust + 3 js + 1 python; `node_modules`/`target`
   pruned; +2 tests, suite → 231).
2. **Workspace package selection** — the Rust adapter needs `-p <package>` (+ cwd);
   done by hand in the pilot, not yet in `RustCargoAdapter`. *(TODO)*
3. **Build-time network downloads** — `build.rs`/native-lib fetches bypass `cargo
   fetch` and need open network, the proxy CA in the build tool's trust store, or
   native-lib strategy env (`ORT_STRATEGY=system`, `ORT_LIB_LOCATION`). The
   env-bootstrap (§12.4) must handle this class. *(TODO)*
4. **Resource reality** — real Rust ML repos pull huge native trees → heavy
   compile; reinforces "untrusted/real target → container on a capable host."

Net: the pilot validated the *engine* and handed §12/M5 concrete, real-world
requirements; a full headroom hunt is **host/container work**, tracked under §12.

### 11.20 Adopted Anthropic's reference skills (2026-06-09)
Build-vs-adopt decision after reviewing Anthropic's Apache-2.0
`defending-code-reference-harness` + the "Using LLMs to Secure Source Code" blog:
**we had independently rebuilt their architecture**, so rather than reinvent the
find/triage/patch reasoning we **vendored their skills** into
`vendor/anthropic-skills/` (`threat-model`/`vuln-scan`/`triage`/`patch`/
`quickstart`/`customize`; Apache-2.0 + LICENSE + NOTICE) and **keep our engine**
for what they can't do portably. Their skills are read/write-only (no Docker/
gVisor); their autonomous `vp-sandboxed` pipeline (Linux+Docker+gVisor+KVM, C/C++
+ ASAN only, "not maintained, a reference not a product") we deliberately do NOT
adopt — our multi-language daemonless verifier (`run_harness`/`adapters`/
`exec_backend`) is the execution-verification layer their static scan explicitly
lacks outside C/C++. Their stated gaps (no discovery — "add an outer loop
yourself"; "autonomous triage & patching still open"; "patch files only / not
upstreamable") ARE our §12 autonomy layer. `docs/ADOPTION.md` carries the pipeline
+ artifact (`VULN-FINDINGS.json` ↔ our finding) mappings. **Positioning: OSS Bug
Hunter = the autonomous outer loop + portable multi-language execution-verification
wrapping Anthropic's per-target skills.** Next: Phase 2 (ingest `VULN-FINDINGS.json`
/`TRIAGE.json` → `run_harness` as the Verify stage), then Phase 3 (discovery →
scheduler → gated-PR). Suite unchanged at 231 (vendoring is prompt/doc files only).

### 11.21 Adopt Phase 2 — ingest + honest closure status (2026-06-09)
`tool/ingest.py` bridges Anthropic's static artifacts (`VULN-FINDINGS.json` from
`/vuln-scan`, `TRIAGE.json` from `/triage`) into our finding scaffolds: tolerant
parsing (top-level list / `findings` / `triaged` / `results` / `bugs`), field
mapping (`{id,file,line,category,severity,title,description}` → our schema),
traversal-safe clamped `vs-*` ids, triage-rejected findings (false_positive/
duplicate) skipped, and first-class `severity` + `source` + `reproducer_hint`.
`findings._summary` now surfaces severity/provenance. +5 tests (incl. the real
vendored canary fixture) → **236**.

**Independent review + honest correction.** The review's P0 was right and worth
heeding: ingest only **opens the funnel**. `orchestrate_finding` auto-builds a
reproducer for **Java only**, so an ingested Python/Go/Rust/JS finding has no PoC
and stays in `proposed` — the ingest→Verify loop is *not* closed for the very
languages this path serves. Rather than overclaim (the trap earlier reviews
flagged), `ingest.py`'s docstring + `docs/ADOPTION.md` step 3 now state this
plainly, and the **per-language reproducer-builder** is filed as the explicit gate
(task #54). Also fixed from the review: the canary test no longer presents
"5 written" (incl. planted false positives) as *good* — it documents that raw-scan
ingest is intentionally UNFILTERED (dedup/FP-removal is `/triage`'s job); the
`total` count no longer re-parses; and `severity` is surfaced rather than written-
then-ignored. Next: task #54 (reproducer-builder) closes Verify for non-Java.

### 11.22 #54 — reproducer-builder + Verify stage (2026-06-09)
Closes the Phase-2 review's P0 (ingest dead-ended at 'proposed' for non-Java).
`tool/llm_repro_provider.py` is the non-Java reproducer-builder: a per-language
prompt (pytest / `go test` / `#[test]` / `node:test`) asks the LLM for a test that
FAILS on HEAD (AI proposes), with fenced-code extraction. `pipeline.verify_finding`
is the Anthropic-style **Verify** stage: ensure a reproducer (build if missing — Java
via `run_repro_subagent`, others via the builder), run `run_harness.validate_repro`,
write the reproducer gate, return reproduced | does-not-reproduce | no-reproducer-
built | inconclusive — the non-AI validator decides. Wired into orchestrate step 1
for non-Java + a `pipeline.py verify <id>` CLI; this is what moves an ingested
'proposed' finding to 'reproduced' WITHOUT needing a fix.

**Proven end-to-end on the live engine:** `verify_finding('py-1')` placed the
reproducer, ran real pytest (daemonless), the test failed with the actual bug
(`IndexError` on an empty list), and it returned 'reproduced' + wrote the gate. The
builder's LLM call is mocked in tests (like the Java builders; a real-LLM build is a
live demo); control flow, extraction, skip-java, and failure paths are unit-tested.
+9 tests → **245**. A self-review caught + fixed a real regex bug (the python tag
`py(thon)?` was a capturing group hijacking `group(1)` → made non-capturing). Honest
remaining gap: the non-Java **fix-builder** (to reach 'fixed') is **#55**; until then
ingested non-Java findings reach 'reproduced', not 'fixed'.

### 11.23 #55 — fix-builder: the full loop closes for all five languages (2026-06-09)
The symmetric sibling of #54. `tool/llm_fix_builder.py` is the non-Java fix-builder: a
language-aware prompt asks the LLM for the SMALLEST unified-diff patch that fixes the
root cause (no refactoring / symptom-masking; per-language patch-containment rules
mirroring the adapters), reusing day3's proven diff extractor. It provides both the
initial `build_fix` and a `make_provider` retry hook (corrected patch from failure
feedback). Wired into `_orchestrate_finding` steps 2 + 3, language-aware (Java keeps
`run_fix_subagent` + `llm_fix_provider`; the others use `llm_fix_builder`).

**Proven end-to-end on the live engine:** `orchestrate_finding('py-1')` ran the full
reproduce → fix → validate loop daemonlessly → **fixed** (validated, attempts=1). The
builders' LLM calls are mocked in tests (a real-LLM build is a live demo); +6 tests →
**251** (a pre-existing test that encoded the old limitation — "no python builder",
`fix_provider is None` — was correctly flipped). **The converged reproduce→fix→retry
loop now works for all five languages** (Java · Python · Go · Rust · JS), and the
Anthropic-skills funnel is closed end-to-end: ingest → verify (reproduced) → fix
(fixed). Next: Phase 3 — the outer loop (discovery → scheduler → gated-PR, §12).

### 11.24 Phase 3 — outer-loop seams: §12.6 gated-PR draft + §12.3 discovery (2026-06-09)
Two outer-loop seams built, reviewed, and committed (`aef6700` init; `923c138` §12.6).
**§12.6 gated-PR draft (DONE):** `tool/pr_draft.py` + REST (`/api/pr-drafts[/{id}[/decide]]`)
+ CLI (`pr-draft`/`pr-drafts`/`pr-decide`) + a React **Review** tab — a `fixed` keeper
becomes a persisted, reviewable draft; human approve/reject; **never pushes** (the human
runs the draft's identity-gated `manual_steps`). **§12.3 discovery (DONE):**
`tool/discovery.py` — pluggable sources (hermetic `JsonSource`; injectable
`GitHubSearchSource`) → dedup → filter → **hard eligibility gate** (adapter-supported
language + not native-heavy/oversize/archived — the headroom lesson, enforced as a gate
not a rescuable score term) → transparent non-AI score → rank → cap → `enqueue` a
scheduler-ready queue; CLI `discover`; never clones/runs/pushes. Each was agent-reviewed
+ reiterated (§12.6: honest closure correction; §12.3: P0 hard-gate, P1 cross-transport
dedup + the `GH_TOKEN`/enterprise public-search hazard, P2 robustness). **269 tests.**

The per-finding chain is now **ingest → reproduced → fixed → reviewable draft**, and
discovery proposes the INPUT — but **nothing yet *consumes* the discovery queue**: the
**§12.5 scheduler** (discover → clone via `targets.add_target` → hunt → fix → draft,
budgeted + audited) is the next piece. Deferred follow-ons: GitHub enrichment of
`has_tests`/`native_heavy` (a contents/code-search probe) and per-source rate-limiting.

### 11.25 Phase 3 §12.5 — scheduler / outer loop (2026-06-09)
`tool/scheduler.py` is the loop-closer: consume `discovery-queue.yaml` → per candidate:
clone → (bootstrap) → hunt → verify → fix → gated-PR draft. The loop STRUCTURE is built
+ tested: `Budget` (max-targets/attempts), idempotent per-repo state (skip terminal on
re-run), a KILL-SWITCH (the `cell-1/hunt/STOP` file or an injected callable), per-
candidate error isolation, an audit trail, and a safe `plan()` DRY-RUN. Steps are an
injectable `Steps` protocol (FakeSteps in tests → fully hermetic); `EngineSteps` wires
the real components (`targets.add_target` trust=False → `verify_finding` →
`orchestrate_finding` → `pr_draft.queue_draft`). CLI `pipeline.py schedule` (dry-run
default; `--run` gated). It **never pushes** — it stops at a reviewable draft. +8 tests
→ **277**.

**HONEST GAP (the last mile to autonomy):** two steps are unwired — `bootstrap` (M5
env-bootstrap, #46-49) and `hunt` (running the vendored Anthropic `/vuln-scan` skill on a
real repo — a Claude Code skill invocation, not a pure Python call). So `--run` today
clones a candidate and stops at `hunt`. The outer loop's skeleton is complete + proven;
closing it needs the hunt step wired + M5 + a host with open network — then the full
chain runs: **discover → clone → bootstrap → hunt → verify → fix → draft**, budgeted +
audited, ending at a human-approved push (§12.6). That is L3→L4 on the §12.2 ladder.

### 11.26 §12.5 last mile — hunt wired; the autonomous loop is structurally complete (2026-06-09)
`tool/hunt.py` wires the scheduler's `hunt`: `vuln_scan(target_dir, language, …)` runs a
headless `claude -p` static scan (the automatable analogue of the vendored Anthropic
`/vuln-scan` skill — richer multi-agent results still come from running the interactive
skill in Claude Code), emits the `VULN-FINDINGS.json` schema, and bridges it via
`ingest.py` → finding scaffolds. `EngineSteps.hunt` calls it with the target's detected
language. The LLM call is injectable (hermetic in tests; +5 → **282**); a real scan is a
live demo like the repro/fix builders.

**The §12 autonomous loop is now structurally COMPLETE end-to-end:** discover (§12.3) →
clone → bootstrap → **hunt (#61)** → verify (#54) → fix (#55) → gated-PR draft (§12.6) →
human-approved push — every step wired, budgeted, idempotent, audited, kill-switchable,
never auto-pushing. **Remaining gates to a live run are ENVIRONMENTAL, not structural:**
M5 env-bootstrap (#46-49) for multi-dep repos (single-dep/already-resolvable run today
with the no-op bootstrap); the LLM calls (hunt scan + fix builder) need the model + a
host; heavy/native repos need a capable host (the headroom lesson). On such a host,
`pipeline.py schedule --run` executes the full chain — L3→L4, reachable now.

### 11.27 M5 mechanism — env-bootstrap runner + wiring, proven firing (2026-06-09)
`tool/bootstrap.py` (#47) runs an adapter's `bootstrap_steps` once, idempotently (a
`.oss-bootstrap.json` marker keyed on the manifest hash skips an unchanged target), via
an injectable `run_step` (default local subprocess), bridge network policy. Wired (#48)
as a `_maybe_bootstrap` pre-step in the adapter validate paths — after `pristine()`,
which now PRESERVES `.oss-venv`/`node_modules`/the marker across runs; a failed bootstrap
returns a `DEP_ERROR` verdict. Adapter interface (#46): `MANIFESTS` + `bootstrap_steps`
per language; Python uses a per-target `uv` venv + venv-aware `test_argv`.

**Proven firing on the live engine:** the existing go/rust/js synthetic e2e tests now run
the REAL bootstrap (`go mod download` / `cargo fetch` / `npm install`) — markers written
`status: ok`, daemonless, no regression (**294 tests**). These are no-dep targets so
bootstrap is a fast near-no-op; the LOAD-BEARING proof (a Python src-layout package where
`uv pip install -e .` is REQUIRED for the reproducer to import) is #49. With M5, the
§12.5 scheduler's `bootstrap` step is real for the four adapter languages — the last
structural gate to multi-dep autonomous runs (modulo environmental network/host realities,
e.g. the proxy-CA wall the headroom pilot hit).

### 11.28 M5 #49 — load-bearing proof + review fixes; multi-dep loop works (2026-06-09)
`targets/pysrc-demo` is a Python **src-layout** package (`widget`) importable ONLY after
`uv pip install -e .` — so M5 bootstrap is genuinely load-bearing here. **Proven on the
live engine:** `validate_repro pysrc-1` → FAILED (reproduces a ZeroDivisionError via the
bootstrapped venv); without bootstrap it's BUILD_ERROR; `orchestrate_finding('pysrc-1')`
→ **fixed** (reproduce → fix → validate), driven by REAL `uv venv` + editable install +
pytest. The proof caught two real bugs: the venv lacked pytest (the test runner), and the
venv path double-nested (worktree-relative vs cwd=worktree → now absolute).

A background **agent review** of the M5 mechanism found + fixed: **P0** — a trust-gate
bypass where bootstrap ran install commands (npm pre/postinstall, pip/PEP517 backends —
all executing target code) on the HOST even for untrusted targets; `_maybe_bootstrap` now
**fails closed** for a container backend (in-container bootstrap is the follow-on), never
running installs on the host. **P1** — lockfiles (`go.sum`/`Cargo.lock`/`poetry.lock`/
`Pipfile.lock`/`yarn.lock`/`pnpm-lock.yaml`) added to the idempotency hash so a deps bump
invalidates the cache. **P2** — a bootstrap exception (e.g. `uv` absent) → `DEP_ERROR`,
not a silent skip. **P3** — atomic marker write. **296 tests.**

**M5 is complete + proven:** the bootstrap step is real for all four adapter languages,
closing the §12.5 outer loop's last *structural* gate to multi-dep autonomous runs. The
honest residuals are: in-container bootstrap for UNTRUSTED targets (today they fail closed),
and the environmental network/host realities (the proxy-CA wall, capable hosts).

### 11.29 #62 — in-container bootstrap for untrusted targets (2026-06-09)
Closes the M5 review's P0 properly. `bootstrap_steps` now use CWD-relative paths
(`.oss-venv`, not absolute) — correct under `cwd=worktree` locally AND `cwd=/work` in a
container (and a cleaner fix for the #49 double-nest); Python's `container_argv` is
venv-aware (the in-container `/work/.oss-venv`). `_maybe_bootstrap` is trust-routed:
**trusted** → host bootstrap (local backend, proven); **untrusted + in-worktree deps**
(Python `.oss-venv` / JS `node_modules`, shared with the test container via the `/work`
bind mount) → run bootstrap **inside the container** (`_container_run_step`: build image +
each step at `/work`, `network=bridge`) so install commands that execute target code
never touch the host; **untrusted + cache-based langs** (go/rust — module caches live in
`~/.cache`/`~/.cargo`, outside the worktree, so they don't survive between the bootstrap
and test containers) → **fail closed** until a shared cache mount is wired (#63).

Wired + unit-tested (mock backend: routing + the container run_step's RunSpec + venv-aware
container_argv); **the real container run is host-only** (no Docker daemon here — like the
whole container path, §11.17). #49 re-verified locally with the relative paths. **297
tests.** With this, untrusted targets never run installs on the host: Python/JS hunt in a
container; go/rust are safely refused pending the cache mount.

### 11.30 #63 — go/rust shared dep-cache, yarn/pnpm, pristine guard (2026-06-09)
The #62 residual. Untrusted go/rust no longer fail closed at bootstrap.

**Cache redirected into the worktree.** go/rust caches normally live outside the tree
(`~/go`, `~/.cargo`), so they would not survive between the bootstrap container and the
test container. Rather than mount the host cache (a poisoning risk) we point the caches
INTO the worktree — `GOMODCACHE`/`GOCACHE` → `/work/.oss-go/{mod,build}`, `CARGO_HOME` →
`/work/.oss-cargo` — via a new `adapter.container_cache_env(work)`. Both the bootstrap and
test containers mount the same worktree at `/work`, so the populated cache is shared for
free; it is per-target, gitignored, and pristine-preserved. go/rust thus join Python/JS as
`bootstrap_in_worktree=True`; the fail-closed branch now only guards a hypothetical adapter
that keeps its deps elsewhere.

**Allowlisted container env.** The cache vars reach the container through a new
`RunSpec.container_env` → `-e KEY=VALUE` allowlist, kept strictly separate from `env` (the
local subprocess env). The container never receives host `os.environ`, so a secret like
`GH_TOKEN` in the harness env cannot leak into untrusted code.

**yarn/pnpm.** JS `bootstrap_steps` matches the lockfile to its package manager —
`corepack pnpm|yarn` (corepack ships with node ≥16.10) for pnpm-lock/yarn.lock, else
`npm ci`/`npm install`.

**pristine guard (review P1).** `pristine()` now refuses — returning a TOOL_ERROR string
instead of running `git reset`/`clean` — when the worktree is not a git work tree, or when
a `clean` dry-run shows it would delete an untracked SOURCE-OF-TRUTH manifest
(Cargo.toml/go.mod/pyproject.toml/package.json/...). Derived lockfiles (Cargo.lock, go.sum,
`*-lock.*`) are still cleaned — bootstrap regenerates them — and the `.oss-*` caches are
preserved. This stops the engine silently nuking a target's manifest and then bootstrapping
as if there were none. (It immediately caught the rustbug-demo case: Cargo.toml committed,
Cargo.lock untracked → the guard correctly ignores the lockfile.)

Unit-tested incl. the `-e` allowlist emission and the guard against a real git repo; the
real in-container go/rust run remains host-only (no Docker daemon here, §11.17). **302 tests.**

### 11.31 #56 — portable synthetic demo targets (2026-06-09)
The 5 synthetic demo targets (pybug/gobug/jsbug/rustbug/pysrc-demo) are the fixtures the
per-language adapter e2e tests run against. They used to exist only as gitignored working
copies with a nested `.git`, which meant a fresh clone of this repo had no targets — so every
adapter e2e test skipped — and, after #63, pointing the engine at an uncommitted manifest would
hard-fail the new pristine guard.

The tension: the engine needs each target to BE a git repo (for `git apply` + `pristine`), but
committing a nested `.git` as content/gitlinks is broken. Resolution: track the SOURCE, not the
repo. Each target's committed tree is extracted (`git archive HEAD`) into a tracked
`targets/_src/<name>/` (plain files, no `.git`). `tool/demo_targets.py` (`make targets`)
materializes a gitignored working copy `targets/<name>/` on demand — copy → `git init` → one
commit — idempotently (a working copy that already has a HEAD is left alone; `pristine()` keeps
it clean between runs). The throwaway commit uses a neutral identity with `gpgsign=false` so it
never trips the host's enterprise signing.

The 5 e2e tests now call `materialize(name)` instead of skipping when the target is absent; they
skip only when the language toolchain (go/cargo/node/uv) is missing. Proven end-to-end by
deleting the gobug-demo working copy (leaving only `_src`) and running the go e2e test: it
rebuilt the repo from `_src` and passed. **303 tests.**

### 11.32 #59 — GitHub candidate enrichment + rate-limiting (2026-06-10)
The §12.3 follow-on. GitHub repo-search returns stars/size/license but NOT the two strongest
selection signals: does the repo have a test bed (the blog's #1 efficacy lever), and is it a
heavy native build (the headroom lesson)? Until now those were known only for curated JsonSource
rows, so `_eligible`/`score_candidate` flew half-blind on GitHub candidates.

**Enrichment.** `GitHubSearchSource` gained an injectable `detail(candidate) -> {languages,
tree_paths}` fetcher (default: `gh api repos/<r>/languages` + `git/trees/<default_branch>?recursive=1`).
Pure, unit-tested heuristics turn that into fields: `_native_heavy_from_languages` (≥25% of bytes
in C/C++/CUDA/Fortran/ObjC → heavy) OR a CMake/autoconf/`.cc` file in the tree; `_has_tests_in_tree`
(`test/`·`tests/`·`src/test/`·`spec/`, `_test.go`, `test_*.py`/`*_test.py`, `*.test|spec.{js,ts,…}`).
`enrich_candidate` is pure + idempotent and never overwrites a value a curated source already set
(None = unknown). Crucially, enrichment runs ONLY for repos that pass the cheap, network-free gate
(supported language, not archived, within size) — so no API call is spent on a repo `discover`
would reject anyway — and it is best-effort (a failed `detail` call leaves the fields unknown,
which is False-safe in the gate). Net effect: the heaviness HARD GATE now bites GitHub results, not
just curated ones.

**Rate-limiting.** A new per-source `RateLimiter` wraps every gh call: a `min_interval` pace
(proactive, default off) plus the real protection — catch `RateLimitError` (raised when `gh api`
returns a rate-limit/403/429) and retry with the API's backoff, up to `max_retries`. Clock + sleep
are injectable, so pacing and backoff are tested instantly + deterministically. State is
per-source-instance; cross-RUN cadence stays the scheduler's Budget (§12.5).

CLI: `discover --github <q> [--no-enrich] [--rate-limit SEC]`. The live gh path is host-only here
(no public-GitHub access in the sandbox, and we drop the enterprise-pinned GH_TOKEN anyway), so it
is `# pragma: no cover`; everything else is exercised by 8 new hermetic tests. **311 tests.**

### 11.33 #51 — Rust adapter: Cargo workspace `-p` member selection (2026-06-10)
The Rust adapter placed the reproducer at `<worktree>/tests/<stem>.rs` and ran `cargo test
--test <stem>` from the root. That works for a single crate, but a Cargo WORKSPACE root is often
a VIRTUAL manifest (`[workspace]` with no `[package]`) that owns no `tests/` target — so the
reproducer compiled nothing and `cargo test --test` errored. `detect_components` (#50) already
surfaces each member as a component, but the run path can still hand the adapter the workspace ROOT.

`_resolve_crate(worktree)` now parses Cargo.toml with `tomllib`:
- root has `[package]` → `(root_pkg, root)` — single-crate OR root-package workspace; unchanged.
- virtual workspace → expand `members` (incl. globs like `crates/*`), pick the first member with a
  `src/lib.rs` (an integration test needs a lib crate to import) → `(member_pkg, member_dir)`.
- neither → `(None, root)` — degrade to a plain `cargo test`.

`place_reproducer` drops the test into that crate's `tests/` and, for a workspace member, encodes
the package in the selector as `pkg::stem`; `test_argv` decodes it to `cargo test -p <pkg> --test
<stem>`. Because `-p` resolves from the workspace root, the command runs at the engine's existing
cwd (the worktree root) — no cwd manipulation needed (the "+cwd" half of the task is satisfied by
NOT needing one). Single-crate selectors stay a bare `stem`, so rustbug-demo is byte-for-byte
unchanged.

Proven on a new portable `rustws-demo` virtual workspace (members `mathx` (buggy lib) + `util`):
the adapter resolved `mathx`, validate_repro panicked (FAILED) and validate_fix PASSED via
`cargo test -p mathx`. Plus hermetic tests for resolution / selector / glob-members. **314 tests.**

### 11.34 #25 — shard pipeline_lock into per-key locks (G1) (2026-06-10)
G1 ("concurrent runs don't deadlock") was proven in #28; #25 makes them PARALLELIZE. The engine
already locks per-worktree (`run_harness.worktree_lock`). The remaining coarse point was
pipeline.py: per-finding writes ran either under the single global `pipeline_lock` (so two
unrelated findings serialized) or — `_set_gate` — UNDER NO LOCK AT ALL, a read-modify-write on
the scaffold YAML that the 2-worker run pool could corrupt / lose-update.

`keyed_lock(key)` is a cross-process file lock scoped to a key (e.g. `finding:ec-1`, lock file
under `cell-1/.locks/`) and reentrant per-thread (a nested same-key acquire is a no-op, so leaf
writers compose without self-deadlock). The flock-with-timeout loop is factored into
`_flock_acquire`, shared with `pipeline_lock`. A `@_keyed(key_fn)` decorator wraps the
per-finding write leaves — `_set_gate` (`finding:<id>`), `_write_repro_result`/
`_write_fix_result` (`finding:<id>`), `_write_backtest_result` (`backtest:<issue>`),
`_write_hunt_result` (`hunt:<angle>:<pass>`). Same key → serialized + race-free; different keys
→ parallel, so two orchestrate runs on different findings/worktrees no longer block on one mutex.

`run_step` deliberately KEEPS the global `pipeline_lock` — a whole `make` step mutates broad
shared cell-1 state (pipeline progression, aggregate reports), so it stays the intentional coarse
boundary. Lock ordering is acyclic (the orchestrate path takes only keyed_lock; batch writers
take pipeline_lock then keyed; nothing takes them in reverse).

Proven with 5 thread tests: reentrancy, different-keys-don't-block, same-key-serializes,
pipeline_lock still works, and — the real fix — concurrent `_set_gate` on one scaffold (50×2
iterations) never loses an update or tears the YAML. **319 tests.**

### 11.35 Standard review (7 perspectives) + fixes (2026-06-10)
Ran a 7-perspective Standard review (local Claude agents — Bedrock had no creds in the sandbox)
over the session's 6 commits before pushing. Verdict: no P0; the security-critical claims
(`container_env` can't leak GH_TOKEN, untrusted installs never run on the host, lock ordering
acyclic) were independently VERIFIED correct.

**P1 (consensus 5/7) — fixed.** `pristine()`'s manifest guard (#63) matched `Path(p).name` against
`git clean -fdn` output, but git COLLAPSES a wholly-untracked directory to a single entry
(`Would remove crates/`) — so a nested `crates/<x>/Cargo.toml` slipped the guard and the real
`git clean` would delete it. That is the exact data loss the guard exists to prevent, and it
collides with the multi-crate workspace layout #51 added. Fix: the dry-run runs with
`core.quotePath=false` (parse non-ASCII names) and descends into any reported directory (pruning
build/cache dirs via `_SCAN_SKIP` to stay fast), refusing if a primary manifest lives anywhere
beneath it. Regression test added (`test_pristine_guard_refuses_manifest_in_untracked_subdir`).

**Bundled P2s — fixed.** `_NATIVE_TREE` now matches `.cpp/.cu/.hpp` (the dominant C/C++/CUDA
extensions it missed); GitHub enrichment treats a TRUNCATED git tree as unknown (leaves
`has_tests`/`native_heavy` `None`, never a false `False` from a partial listing) while the
complete languages API can still rule `native_heavy` in; `container_cache_env` is now called via
`getattr(..., lambda w: {})` so a future adapter that omits it can't `AttributeError` mid-run. The
load-bearing `_container_run_step` in-container glue gained a routing test. **322 tests.**

**Advisory (deferred — see REVIEW.md).** Rust `_resolve_crate`'s "first lib member" is a
load-bearing heuristic (a multi-lib workspace whose buggy crate isn't first picks the wrong one —
a known limit until crate selection is driven by the finding's component); the #25 batch writers
still hold the global `pipeline_lock` (the shard's win lands on the orchestrate path — coherence,
not a defect); per-key lock files accumulate under `cell-1/.locks/` (swept by `make clean`); plus
minor test/doc gaps. None block a push.

### 11.36 Deep review (12 perspectives) + P0 fix (2026-06-10)
A full 12-perspective deep re-review of the post-Standard-fix state ("review again fully"). It
caught a **P0 that the Standard review's OWN pristine fix had introduced**: the `_SCAN_SKIP`
shortcut skipped walking any reported untracked dir named `build`/`dist`/`target` — but those are
legal crate/package/module dir names, so an untracked `build/Cargo.toml` was collapsed by `git
clean -fdn` to one line, skipped, and deleted (devils-advocate reproduced it live). A symlink
variant (`is_dir()` follows a symlink → `os.walk` walks the whole filesystem) was also found.

**Fix:** `pristine()` now uses `git status --porcelain --untracked-files=all` — it lists every
untracked file INDIVIDUALLY (no dir-collapse, no symlink-follow, no `os.walk`/`_SCAN_SKIP`),
excludes the preserved caches by pathspec + a path-parts check, and uses `core.quotePath=false`.
The regression test covers the `build/`-named-dir manifest, a non-traversed symlink, and a
still-cleaned build dir. Simpler than the walk approach (the simplifier's suggestion) and closes
both holes.

**Also fixed:** `pytest.ini` (`testpaths=tests`) so a bare `pytest` no longer collects the cloned
`targets/` repos; the stale "hunt is unwired/TODO" text in the scheduler module docstring +
`schedule --run` help (hunt was wired in #61; bootstrap is a documented no-op); README count 228→322.

**Filed as follow-ons (roadmap-scope, not push-blockers):** **#64** thread a `component`
(dir/language) through the finding schema so monorepo/workspace targets resolve the right component
(retires the Rust "first lib member" heuristic, fixes all languages); **#65** wire env-bootstrap
into the scheduler loop before hunt (today `EngineSteps.bootstrap` is a no-op); **#66** a
`HarnessAdapter` Protocol/ABC + derive the pristine cache keep-set from the adapters (drop the
`getattr` defaults). The security-critical invariants were re-verified correct. Critic verdict:
**push-ready**. **322 tests.**

---

## 12. Autonomy roadmap — toward unattended OSS bug-hunting (PROPOSED)

> **STATUS: PROPOSED / NOT BUILT.** Unlike §11 (a log of shipped, tested work),
> **nothing in §12 exists yet.** This is the design for turning the current
> human-driven, one-target-at-a-time tool into a self-feeding loop that can hunt
> the OSS ecosystem unattended. Each subsection separates **what exists today**
> from **what's needed**. When a piece ships, move it into §11 with a proof level
> (§11.17) — do not let §12 claim credit for unbuilt work.

### 12.1 Thesis — "autonomous up to a reviewable PR, never past it"

The product should be able to run on its own — discover repos, hunt, reproduce,
fix, and assemble a PR — but the **push to a public upstream stays a human
decision, by policy, forever.** "Autonomous" here means *autonomous up to a
reviewable PR draft*, with a human approving identity + push. This is not a
limitation we hope to remove later; it is the design ceiling, for three reasons:
the identity rule (public-repo PRs must use the personal identity, never
enterprise — see `.claude/rules/confirm-gh-account-before-commit.md`), maintainer
trust (an auto-pushed low-quality PR burns reputation that can't be un-burned),
and the invariant that **AI proposes, non-AI validators dispose** — a human is
the final disposer at the upstream boundary.

### 12.2 The autonomy ladder

| Level | What runs unattended | Human role | Gap to reach it |
|---|---|---|---|
| **L1 — Assisted single-target (TODAY)** | One finding: reproduce→fix→validate via `run_harness.orchestrate` | Picks the repo (add-by-URL), clicks Orchestrate, reviews, opens the PR by hand | **— (shipped)** |
| **L2 — Batch over a curated queue** | All findings across a human-curated target list; produces **draft PRs** into a review queue | Curates the queue; approves/rejects drafts; pushes | env-bootstrap (§12.4) · outer loop (§12.5) · draft-PR (§12.6) |
| **L3 — Self-feeding discovery** | Discovers + ranks + enqueues new OSS repos, then L2 | Approves drafts; tunes discovery filters | discovery (§12.3) |
| **L4 — Continuous operation** | Scheduled, always-on, budget-bounded discover→hunt→fix→draft across many repos | Approves pushes; handles escalations | scheduler + governance + dashboards (§12.5, §12.7, §12.8) |

**The push stays human at every level.** An optional **L4-push** mode may
auto-push *only* to repos the operator owns (their own forks/projects), opt-in
per-target, default OFF, **never** to arbitrary public upstreams. That is the one
place the ceiling can be raised, and only for scopes the operator controls.

### 12.3 Component A — Discovery & selection (the missing input)

**Exists today:** `tool/targets.py` (`add_target(url)` clones + language-detects +
writes a trust-gated metadata sidecar). Targets are added **one URL at a time, by
a human.**

**Needed:** a `discovery.py` that proposes + ranks candidate repos and enqueues
them as targets. Sources (pluggable, each behind a rate-limited client):
- GitHub search/trending by language + activity + "good first issue"/bug labels;
- OSV / advisory feeds (known-vulnerable dependency ranges → affected repos);
- dependency graphs (hunt the libraries your own stack depends on first);
- a repo's own issue tracker (open bug reports = pre-stated reproducers).

Ranking heuristics (cheap, non-AI first): test-suite present? CI green? language
supported by an adapter? permissive licence? active maintainership? not already
in the queue / not already PR'd. Output: scored rows appended to a **target
queue** (new state in `run_store`), `trusted=False` by default (untrusted →
container execution per §3.1). **Safety:** an allowlist/denylist, a per-source
rate limit, and a hard cap on queue depth so discovery can't run away.

### 12.4 Component B — Per-target environment bootstrap (the real blocker)

**Exists today:** adapters assume dependencies are already resolvable (the
synthetic demo targets are single-file). Real repos need their toolchain set up.
This is the long-standing "per-target env setup" known gap (§11.10–§11.12, G-notes).

**Needed:** a per-language bootstrap step the orchestrator runs once per target
before the first reproduce: `python -m venv && pip install -e .` / `go mod
download` / `cargo fetch` / `npm ci`. For **untrusted** targets this runs **inside
the container** (so an install hook can't touch the host); the first run needs
network (dependency resolution), which means relaxing the `network=none` default
to a **first-run-only `bridge`, then `none`** policy (Engine review #3). Detect
the build system from the repo; cache the resolved env per target SHA.

### 12.5 Component C — The outer loop / scheduler

**Exists today:** the *inner* loop (`run_harness.orchestrate`: reproduce→fix→retry)
and the *batch* loop (`pipeline.orchestrate()` over one cell's findings), plus the
job/run + SSE seam (`run_store.py`) that already streams long-running work.

**Needed:** an outer driver that walks **discover → hunt → reproduce → fix →
validate → draft-PR → next target**, bounded by budgets. Two deployment shapes,
same core:
- **Harness-native** (Claude-Code-driven): a `CronCreate` schedule or a `/loop`
  fires the driver on a cadence; background agents fan out hunts. Best for
  operator-attended runs on a workstation.
- **Headless daemon**: a standalone scheduler process drives the same `run_store`
  jobs for server deployment (no Claude Code session needed to keep it alive).

Both reuse the existing job model. Requirements: **concurrency caps** (N targets ×
M findings in flight), **budgets** (max LLM spend / wall-clock / targets-per-day),
idempotency (don't re-hunt an unchanged SHA), and a **kill-switch** that drains
in-flight work cleanly. Per-worktree lock sharding (G1, task #25) becomes load-
bearing here — it's currently deferred precisely until this multi-target loop lands.

### 12.6 Component D — Gated-PR draft (the human seam)

**Exists today:** `tool/pr.py` is read-only — it assembles a PR **preview**
(branch, title, commit message, body, blockers) + an identity gate and **never
pushes / never runs `gh pr create`** (verified, §11.9). The React app shows the
preview behind an identity check.

**Needed:** promote the preview into a queued **draft** — same assembly, but the
loop writes the branch + commit to a local fork-clone and parks a "ready for
review" item in an approval queue. A human opens it in the UI, sees the diff +
the validation evidence (both gates green, the reproducer, the fix), and clicks
**Approve & push** — at which point the documented manual steps run under the
**personal** identity (`unset GH_TOKEN` → switch to personal → push → `gh pr
create`). `pr.py` itself stays push-free; the push is an explicit, identity-
confirmed, human action. Rejected drafts feed back as negative signal.

### 12.7 Human-in-the-loop control plane (UI)

**Needed:** an **Autonomy** tab in the React app: the discovery queue (with
scores + accept/skip), live runs (already have SSE), the **draft-PR approval
queue** (the core human surface), budget/quota gauges, a prominent **kill-switch**,
and an **audit log** (every target touched, every artifact produced, every
push approved — by whom). The control plane is what makes L4 trustworthy: a human
can always see what the loop is doing and stop it.

### 12.8 Safety & governance (non-negotiable)

- **Identity:** pushes only via the personal identity; enterprise creds never
  touch a public repo. The approval step re-confirms the account every time.
- **Maintainer respect:** rate-limit PRs per upstream; **dedup against existing
  issues/PRs** before drafting (don't re-report); a **quality bar** gate (no PR
  without a green reproducer + a minimal, contained fix) so the loop can't spam
  low-value PRs. One bad mass-PR event is an extinction-level reputation risk.
- **Trust & isolation:** discovered repos are `trusted=False` → container
  execution; bootstrap install hooks run sandboxed; secrets never enter a target
  worktree or a prompt.
- **Budgets & kill-switch:** hard caps on spend, targets/day, and concurrency;
  a one-click stop that drains cleanly.

### 12.9 Phasing

- **M5 — env-bootstrap** (§12.4): unblocks real multi-dep repos; prove L1 on 3
  real upstreams (one per language) end-to-end, by hand.
- **M6 — outer loop + draft-PR** (§12.5–§12.6): reach **L2** (batch a curated
  queue → draft PRs into the approval queue). Land G1 lock sharding here.
- **M7 — discovery** (§12.3): reach **L3** (self-feeding queue), discovery filters
  tunable from the UI.
- **M8 — control plane + governance + scheduler** (§12.7–§12.8): reach **L4**
  (continuous, budget-bounded, fully audited), push still human-approved.

### 12.10 Risks & open questions (autonomy-specific)

- **False-positive PRs at scale** harm both maintainers and this project's
  standing — the quality-bar gate + dedup are load-bearing, not optional.
- **Discovery/API limits & cost** (GitHub/OSV rate limits; LLM spend per target) —
  budgets must be real, not advisory.
- **The "interesting bug" problem:** the hunt subagents find *candidate* issues;
  at ecosystem scale, ranking *which findings are worth a maintainer's time* is an
  unsolved selection problem, distinct from validation.
- **Upstream contribution norms** (CLA/DCO, PR templates, "no AI-generated PRs"
  policies some projects have) must be detected per-repo before drafting.

### 12.11 Confidence & first move

Confidence the *architecture supports* this path: **high** — the inner loop,
backends, trust gating, job/run+SSE seam, add-by-URL, and read-only PR preview are
already the right seams, and the harness has the scheduling primitives. Confidence
on *timeline*: lower — **§12.4 (env-bootstrap) is the true gate**; without it the
loop only works on toy targets. **First move: M5** — build per-target env
bootstrap and prove L1 on three real upstream repos (one Java, one Python, one
Go/Rust/JS) end-to-end, by hand, before any scheduler is written.
