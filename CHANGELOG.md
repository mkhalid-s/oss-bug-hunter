# Changelog

All notable changes to this project.

## [Unreleased] — multi-language engine + visual app (M0 → 5 languages)

**Dates:** 2026-06-06 → 2026-06-08 (M0 walking-skeleton through the 5-language engine, the React visual app, orchestrator convergence, and the docs-accuracy pass)
**Summary:** Built and empirically proved the riskiest parts of the
multi-language + visual-app plan (docs/MULTI-LANGUAGE-VISION.md) as a thin
vertical slice — in the devcontainer with **no Docker daemon**. Along the way the
spike validated ec-1 end-to-end and caught three real defects pure reasoning
missed.

- `tool/exec_backend.py` — execution-backend abstraction (plan §3.1): auto-detect
  `docker → podman(rootless, --userns=keep-id) → local`. `local` is trust-gated
  (`supports_untrusted()` → False); untrusted target + no engine → no backend
  (fail-closed, verified). On a hardened devcontainer where `unshare --net` is
  forbidden, `local` degrades to offline-runner mode.
- `tool/run_harness.py` — generic validator core (plan §3.3): `Outcome` enum
  (`PASSED|FAILED|NO_TESTS|BUILD_ERROR|DEP_ERROR|TOOL_ERROR`), portable
  `fcntl.flock` per-WORKTREE lock (G1-aligned), pristine reset, FQCN-derived
  reproducer placement, run-repro.sh exit-code parity. JVM memory bounded via
  `MAVEN_OPTS -Xmx` (NOT `prlimit --as`, which kills JVM startup).
- **ec-1 VALIDATED end-to-end** via the `local` backend (no daemon): reproducer
  reproduces on HEAD 1c38a7d8 (raw NPE in
  `CollectionDeserializer._deserializeWithObjectId:488`); the fix patch makes the
  isolated test pass. Recorded in `cell-1/hunt/validation/ec-1.yaml` (both gates
  = pass). Note: ec-1 still failed self-consistency (1/3) — that heuristic
  dropped a true positive.
- **3 real defects surfaced by the spike** (now in plan §11): (1) jackson pins
  `<test>PrimarySuite</test>` so `mvn -Dtest=<class>` runs the whole 3790-test
  suite, not the single test (false PASSED) — isolated runs need the JUnit
  Platform Console Launcher; (2) reproducer filename must match the public class
  (`Repro_ec_1.java`); (3) `unshare --net` forbidden in the hardened devcontainer.
- `tool/run_store.py` + `tool/server.py` — the **job/run + SSE seam** (plan §4.2):
  SQLite(WAL) runs+logs with a single serialized writer, `ThreadPoolExecutor`
  worker, per-run pub/sub with the log table as replay buffer, startup
  reconciliation of zombie runs. Endpoints `POST /api/runs`, `GET /api/runs`,
  `GET /api/runs/{id}`, `GET /api/runs/{id}/stream` (SSE). SSE auth via `?token=`
  (EventSource can't send headers) with constant-time compare; transport sets
  `X-Accel-Buffering: no` + 15s heartbeat + `id:` for resume. Proved by curl:
  live stream, replay for late subscribers, two concurrent runs without deadlock.
- `tool/web/runs.html` + `GET /runs` — U0 spike page (vanilla EventSource)
  rendering the live log in a browser. Production UI stays React+Vite+Mantine.
- **Isolated single-test execution + fix gate** (follow-up, same day): `run_harness`
  now compiles then runs the reproducer via the **JUnit Platform Console Launcher**
  (`--select-class`), fixing the suite-override false-PASSED (defect #1) — verified
  it runs exactly 1 test (not jackson's 3790). Added `validate_fix` (R4 patch
  containment → `git apply` → recompile → isolated re-run; PASSED = fix works) and
  a `validate-fix` CLI subcommand + SSE job kind, completing the reproduce→fix loop
  in one tool. Shared compile+run extracted to `_compile_and_run_isolated`.
  Hardened `check_patch_containment` to abspath the patch (relative + `git -C`
  footgun). Migrated the startup hook to FastAPI `lifespan` (kills the deprecation
  warning). Added `tests/test_spike_harness.py` (19 tests: Outcome parsing,
  containment, fail-closed backend selection, run_store CRUD/broker/worker/
  reconciliation). Verified end-to-end: ec-1 reproduces (exit 1) and the fix
  passes (exit 0) through the isolated path; **171 tests green**.
- **`orchestrate` — the self-correcting loop** (follow-up, same day): `run_harness.orchestrate`
  chains reproduce → fix → (retry-with-feedback) on the validated primitives. The
  load-bearing invariant holds — an optional `fix_provider(feedback, attempt)`
  only *proposes* revised patches; the non-AI validators decide pass/fail.
  Outcomes: `validated` / `not-reproduced` / `fix-failed` / `inconclusive` (exit
  0/2/1/3). Wired as a CLI subcommand + SSE job kind + an "Orchestrate ec-1"
  button on the U0 page. 6 unit tests (incl. the retry-then-validated and
  retry-exhausted loops, stubbed validators). Real ec-1 run end-to-end:
  **status=validated, reproduced=True, fixed=True, attempts=1, exit 0**, no
  daemon. **Full suite: 177 green.**
- **LLM fix-builder wired into `orchestrate`** (follow-up): `tool/llm_fix_provider.py`
  adapts the existing fix-builder (`build_fix_prompt` w/ feedback → `claude -p`
  opus/high → `extract_diff_block` → write `<fid>-retry<N>.patch`) to the
  `orchestrate` `fix_provider` hook, so a failed fix triggers an LLM-regenerated
  patch from the failure feedback. Invariant preserved: the LLM proposes, the
  non-AI `validate_fix` disposes. Exposed via `orchestrate --finding <yaml>
  --max-retries N` (CLI) + a `finding_yaml` SSE param. 4 tests incl. an end-to-end
  one proving orchestrate drives the provider (fix fails → LLM regenerates → retry
  validated; LLM + validators stubbed). **Full suite: 181 green.** (A live opus/high
  run isn't exercised — ec-1's first patch already passes, so retry never fires.)
- **Review hardening (2-agent red-team of the spike code)**, fixes:
  - **BLOCKER**: `run_harness` was silently local-only — on a Docker host
    `select_backend` returned the docker backend and `_compile_and_run_isolated`
    set no image → always `TOOL_ERROR`. Now selects `prefer="local"` (the
    compile+console-launcher flow is direct-execution) and, if a container
    backend is chosen for an untrusted target, returns a CLEAR error instead of
    the opaque "requires spec.image"; container execution is a documented TODO.
  - **Token leak**: SSE carries `?token=`, so the uvicorn access log wrote the
    live token on every connect → `access_log=False` (verified: 0 `token=` in log).
  - **SSE lost-tail race**: final log lines could be dropped right at completion;
    now drain persisted logs (`get_logs(after=replayed)`) before every `done`,
    and track `replayed` in the live loop. `get_event_loop`→`get_running_loop`.
  - `_find_console_jar` now honors the pinned version (a cache-hit no longer runs
    a stale/incompatible console jar); unique + auto-cleaned classpath temp file
    (no `/tmp` leak or same-basename collision); validators catch `OSError`
    (mvn-not-on-PATH) and guard a missing worktree; `run_store.init_db`
    close-before-reopen. 5 new tests. **Full suite: 186 green**; real orchestrate
    ec-1 still **validated**.
- **React U0 visual app** (`tool/webapp/`, plan §4): the locked stack —
  React 18 + TypeScript + Vite 5 + **Mantine 7** + TanStack Query + SSE. App
  shell with a polling **runs list**, a **live-log panel** (EventSource on the
  run's SSE stream, replays history for late subscribers), and run triggers
  (demo / reproduce ec-1 / orchestrate ec-1). Builds to `tool/webapp/dist/`
  (JS 86 KB gzip); **FastAPI serves it at `/app`** with the token injected + the
  hashed assets mounted at `/app/assets` + SPA fallback (no Node at runtime).
  `.npmrc` points at public npm (the default Artifactory needs auth);
  `node_modules`/`dist` gitignored. Verified: `/app` 200 with `#root` + token,
  assets 200, and the bundle wired every `/api/*` path. The vanilla `/runs` page
  remains as a fallback.
- **U2 — findings board + detail**: `tool/findings.py` + `GET /api/findings[/{id}]`
  (kanban column derived from the GATES — proposed→reproduced→fixed→pr-ready, the
  validators decide placement; path-safe id). React: Runs/Findings switch, a
  kanban **board**, and a **finding-detail modal** with gate badges, evidence, the
  reproducer in **CodeMirror** (Java) and the fix in **diff2html**, plus
  Reproduce/Orchestrate buttons that launch a run for the finding and jump to its
  live log. `FindingDetail` is lazy-loaded → initial bundle **94 KB gzip** (was
  261), viewer chunk 167 KB gzip on demand. tsc clean; 186 Python tests green.
  ec-1 sits in **Fixed** (reproduced+fixed but failed self-consistency).
- **U3 — targets / add-by-URL**: `tool/targets.py` (language detection from build
  markers; `list_targets`/`get_target`/`add_target`; metadata in a SIDECAR
  `targets/_meta/<name>.yaml` so the validators' `git clean` can't wipe it; trust
  **fail-closed**; path-safe name + `git clone -- <url>` arg-injection guard) +
  `GET /api/targets[/{name}]` + `POST /api/targets` (clone runs as a streamed
  **add-target** job). React: a third **Targets** tab — target cards
  (language/sha/trusted badges) + an add-by-URL form that streams the clone.
  jackson-databind backfilled (java, trusted). 5 tests (detect + local-clone add +
  fail-closed + flag-URL/duplicate rejects). tsc clean; **191 Python tests green**;
  initial UI bundle 100 KB gzip (FindingDetail still lazy).
- **U4 — Open-PR preview + identity gate** (`tool/pr.py`, read-only): assembles
  the PR (upstream `owner/repo`, fork, branch, title, full body with
  summary/evidence/reproducer/fix/validation) and the gates that must pass — a
  **keeper check** (fix=pass + not dupe + survived self-consistency) and a
  **GitHub identity gate** (active account, `is_personal`==`mkhalid-s`,
  `GH_TOKEN`-enterprise-pin detection) — plus exact manual `gh` steps. It **never
  pushes / never runs `gh pr create`** (hard gate per
  confirm-gh-account-before-commit.md). `GET /api/findings/{id}/pr-preview`; UI:
  an "Open PR" button expands the preview + a red/green gate. ec-1 is correctly
  **blocked** (non-keeper + enterprise account + GH_TOKEN). 4 tests; **195 green**.
  Completes the U0–U4 visual app: Targets → Runs (live) → Findings → detail →
  Open-PR preview.
- **M2 — Python HarnessAdapter (second language)**: `tool/adapters.py` now hosts
  the shared `Outcome`/`TestVerdict` contract + a `PythonPytestAdapter` (place
  reproducer → `pytest` → parse → Outcome; `.py`-allowed / manifest-denied patch
  globs). `run_harness` gained a `lang` param routing `python` through the adapter
  (Java keeps its console-launcher path); generic `_contained_generic`; CLI
  `--lang java|python`; server job kinds pass `lang`. **Proven end-to-end** on a
  synthetic Python target (`targets/pybug-demo`, `running_max([])` IndexError):
  validate-repro→FAILED, validate-fix→PASSED, orchestrate→**validated**,
  daemonless. Fixed a real `__main__`-vs-import double-`Outcome`-enum identity bug
  (moved the contract into `adapters.py`). 3 M2 tests; **198 Python tests green**;
  Java path re-verified. The engine is now genuinely **multi-language (Java +
  Python)** behind one adapter interface.
- **Go HarnessAdapter (third language)**: `adapters.GoTestAdapter` (place
  `repro_*_test.go`, extract the `Test…` func as selector, `go test -run ^Sel$
  -count=1 -v ./...`, parse `--- PASS/FAIL`/`[build failed]` → Outcome; `.go`
  allowed / `go.mod`,`go.sum`,`.github/` denied). `--lang go`. **Proven** (Go
  1.26) on a synthetic target (`targets/gobug-demo`, `RunningMax([])` index panic):
  validate-repro→FAILED (panic), validate-fix→PASSED, orchestrate→**validated**,
  daemonless. 4 Go tests; **202 Python tests green**. The engine now spans
  **Java + Python + Go** behind one adapter interface — multi-language thesis
  demonstrated across three ecosystems with no changes to the loop/gates/seam/UI.
- **Review + refine pass 3** (3-agent red-team of adapters/findings/targets/pr/webapp):
  fixed a **path-traversal** in `targets` (`..` read the repo root) — now
  name-validated + containment-checked; added a **URL transport allowlist**
  (`ext::`/`fd::` refused; https/ssh/file kept) + `GIT_ALLOW_PROTOCOL` + clone
  cleanup on failure; `pr.gh_identity` now parses the **Active** gh account (not
  the first) and `_owner_repo` is anchored; **findings carry `language` + `target`**
  (board is now multi-language: ec-1 java / py-1 python / go-1 go) with
  ext-by-language + guarded YAML load; Go **panic-outside-a-test → FAILED** (was
  silent NO_TESTS); Python patch-deny includes `conftest.py`; web `api.ts` guards
  `r.ok`/non-JSON; **`findingRunParams` is language-aware** (was hardcoding Java
  for every finding); finding/PR panels show error states; PR identity badge red
  on `GH_TOKEN`/non-personal. **207 Python tests green**, tsc clean, build OK.
- **Container-execution path (untrusted targets)**: the adapter (non-Java) path
  now runs in a container for untrusted targets — `run_harness._adapter_run`
  routes **trusted → local** (proven), **untrusted → docker/podman**, building the
  per-language sandbox image (new `tool/repro-py` + `tool/repro-go` Dockerfiles,
  UID/GID-matched non-root), mounting the worktree at `/work`, and running the
  adapter's **container argv** (Python uses the image's `python`, not the harness's
  `sys.executable`; Go's `go test` is identical). Added `exec_backend._host_bind`
  host↔container path translation (docker-outside-of-docker; no-op natively).
  Fail-closed preserved (untrusted + no engine → TOOL_ERROR). Spec-assembly +
  fail-closed **unit-tested** with a fake backend; **local path re-proven**
  end-to-end (Go/Python orchestrate → validated, Java ec-1 reproduces). **209
  Python tests green.** The actual container *run* needs a daemon (none in this
  devcontainer) → validated on the macOS host; Java in-container (host-classpath
  console flow) stays a documented follow-on (Java runs local for trusted targets).
- **Orchestrators converged**: the `/api/orchestrate` + MCP **product path**
  (`pipeline._orchestrate_finding`) was a *separate, Java-only* orchestrator
  (shelling to run-repro.sh/run-fix.sh); it now **delegates the reproduce→fix→retry
  loop to the single multi-language `run_harness.orchestrate(lang=…)`**, reading
  the finding's `language`/`target` from the scaffold, keeping the Java LLM
  builders as pre-steps + `llm_fix_provider` as the retry provider, and requiring
  a pre-existing reproducer+patch for non-Java (no per-language builder yet).
  Outcome vocabulary preserved. **Proven:** `pipeline.orchestrate` *and* `POST
  /api/orchestrate` resolve py-1 (python) + go-1 (go) → `fixed`/validated.
  `test_orchestrator.py` + 2 R5 tests rewritten to the converged design.
  **210 Python tests green**; server + MCP import clean. One orchestration engine
  now powers CLI, the SSE job, `/api/orchestrate`, and the MCP tool.
- **Rust HarnessAdapter (fourth language)**: `adapters.RustCargoAdapter` —
  reproducer placed as an integration test `tests/repro_<name>.rs`, `cargo test
  --test <stem>`, parse `test result: ok/FAILED` + `error[E…]`/`could not compile`
  → Outcome; `.rs` allowed / `Cargo.toml`,`Cargo.lock` denied; container image
  `oss-bug-hunter-rust` (`tool/repro-rust`). `--lang rust` everywhere; synthetic
  `targets/rustbug-demo` (`running_max(&[])` slice panic) + reproducer + patch.
  Built + unit-tested (parse/place/containment, 4 tests). **214 Python tests
  green.** The engine now spans **Java + Python + Go + Rust** behind one adapter
  interface (JS is the remaining language). *(Note: when this bullet was written
  `cargo` was absent so Rust was host-only; cargo 1.95.0 was installed
  2026-06-08 and Rust now validates end-to-end locally — see the docs-accuracy
  bullet below and `docs/MULTI-LANGUAGE-VISION.md` §11.15/§11.17.)*
- **JS HarnessAdapter (fifth language)**: `adapters.JsNodeTestAdapter` —
  reproducer placed as `repro_<name>.test.js`, runs built-in `node --test
  --test-reporter=tap`, parses TAP `# pass`/`# fail` → Outcome (`Cannot find
  module`/`SyntaxError`/`ERR_MODULE_NOT_FOUND` → BUILD_ERROR); `.js/.cjs/.mjs/.jsx/
  .ts/.tsx` allowed, `package.json`+lockfiles+`.github/` denied; container image
  `oss-bug-hunter-js` (`tool/repro-js`). `--lang javascript` everywhere; synthetic
  `targets/jsbug-demo` (`chunk([], n)` infinite-loop) + reproducer (`js-1`) + patch.
  **Proven end-to-end locally** (Node 24 is in this devcontainer). **The engine now
  spans all five planned languages — Java · Python · Go · Rust · JS.**
- **Review pass 4 (engine + system) — fixes pinned by regression tests**: Rust
  panic/abort with no summary line → `FAILED` (was inconclusive); Python verdict
  anchored to pytest's summary line (a stray `"3 errors"` in test output can no
  longer flip it) + `errors?` plural; Go scoped to the reproducer's package (`.`,
  not `./...`) so an unrelated build break can't mask the verdict; `fix-failed`
  added to `_CONCLUSIVE_OUTCOMES` (reproduced-but-unfixed counts as validated);
  `pristine()` no longer clobbers the worktree lock / `.m2`.
- **Split-brain orchestrate converged**: the React UI's Orchestrate now routes
  through `pipeline.orchestrate_finding` via `finding_id` (scaffold-driven,
  self-correcting) — the same engine as `/api/orchestrate` + MCP; previously it
  called `run_harness.orchestrate` directly (degraded, no retry). `server`'s
  `_job_orchestrate` streams the loop via `log=`; missing explicit params now
  raise `ValueError` instead of silently degrading.
- **`tests/test_endpoints.py`** — FastAPI TestClient smoke suite (auth, findings
  envelope + path-traversal guard, targets, pr-preview block, demo run + SSE
  replay, unknown-kind 400, `/api/orchestrate` multilang). **228 tests green.**
- **Docs accuracy sweep**: README stale claims fixed (test count 152→228, MCP
  tool count 13→18, "needs Docker" → "validates locally (trusted) / Docker
  (untrusted non-Java)", added a top-of-README multi-language-engine + visual-app
  status note); vision doc §11.16 (review pass 4) + §11.17 proof-status legend
  (proven-here vs host-only); `LEGACY` headers on `scripts/run-repro.sh`,
  `scripts/run-fix.sh`, and `day3-hunt.py run-repros`/`run-fixes` (not used by the
  converged orchestrator).
- **Review pass 5 (12 perspectives) + Rust now end-to-end local**: a 12-agent
  review of the docs pass (0 P0 / 3 P1 / 6 P2 / 6 P3) caught that §11.17 had gone
  stale — `cargo 1.95.0` was installed 2026-06-08, so "cargo absent / Rust
  host-only" was false. **Made it true**: ran `rs-1` → reproduces → fix →
  **validated** end-to-end locally; flipped `rs-1.yaml` to `validated`; corrected
  §11.17/§11.15 + this changelog; added a cargo-guarded
  `test_rust_adapter_validates_synthetic_target` (**suite 229** with cargo, 228
  without — e2e tests skip-guard, so README count claims softened). **All 5
  languages now proven end-to-end in this devcontainer.** Also fixed: README
  "Project layout" tree (added the engine + React-app modules; 7/12 consensus),
  the stale "orchestrate validates via `run-repros`/`run-fixes`" claim (it's
  `run_harness.orchestrate` + `pristine()`), the undocumented `run_harness.py …
  --lang` CLI + `/app`-vs-legacy-dashboard conflation, the untrusted-Java gap in
  the one-line backend note, and the JS `node --test` framing. See §11.18.
- **§12 Autonomy roadmap (PROPOSED, docs-only)**: added the design for unattended
  OSS hunting — an L1→L4 autonomy ladder (L1 assisted-single-target = today), the
  four missing components (discovery/selection, per-target env-bootstrap, the outer
  loop/scheduler, gated-PR draft), a human-in-the-loop control plane, governance,
  and phasing (M5 env-bootstrap is the gate). Hard principle: **autonomous up to a
  reviewable PR draft; the push to a public upstream stays a human, personal-
  identity decision — never automated.** Nothing in §12 is built yet.
- **Real-repo pilot — `chopratejas/headroom` (M5 start)**: first pilot on a real
  external repo (polyglot monorepo: Rust workspace + Python + TS). The engine
  enumerated the workspace, fetched deps from crates.io, and compiled 100+ crates
  via `cargo test -p headroom-core` — then hit an **environmental** wall (a
  transitive `ort-sys`/ONNX `build.rs` downloads binaries over rustls, which
  rejects the corporate proxy CA → `UnknownIssuer`). headroom-core doesn't build
  in this devcontainer; a full hunt is host/container work. The pilot surfaced 4
  M5 requirements (per-component detection, Rust-workspace `-p` selection,
  build-time-download handling, resource reality). **Shipped from it:**
  `targets.detect_components()` — monorepo-aware per-component language detection
  (headroom → 4 rust + 3 js + 1 python; prunes `node_modules`/`target`); +2 tests
  (**suite → 231**). See §11.19.
- **Adopted Anthropic's reference skills (Phase 1)**: after reviewing Anthropic's
  Apache-2.0 `defending-code-reference-harness` + the "Using LLMs to Secure Source
  Code" blog (we independently rebuilt their architecture), **vendored their
  skills** into `vendor/anthropic-skills/` (`threat-model`/`vuln-scan`/`triage`/
  `patch`/`quickstart`/`customize`, with LICENSE + NOTICE) rather than reinvent the
  find/triage/patch reasoning. Their read/write-only skills need no Docker/gVisor;
  their C/C++-only `vp-sandboxed` pipeline we deliberately do NOT adopt (our
  multi-language daemonless verifier replaces it). `docs/ADOPTION.md` maps their
  pipeline + `VULN-FINDINGS.json` artifact onto our engine. Positioning: **the
  autonomous outer loop + portable multi-language execution-verification wrapping
  their per-target skills.** Docs-only; suite still 231. See §11.20.
- **Adopt Phase 2 — ingest (`tool/ingest.py`)**: maps Anthropic's
  `VULN-FINDINGS.json`/`TRIAGE.json` → our finding scaffolds (`proposed` column;
  carries first-class `severity` + `source` + `reproducer_hint`; skips triage-
  rejected findings; traversal-safe clamped `vs-*` ids). `findings._summary` now
  surfaces `severity`/`source`. +5 ingest tests incl. the **real vendored canary
  fixture** → **suite 236**. An independent review pass found (correctly) that the
  loop is **closed for Java only** — ingested non-Java findings sit in `proposed`
  until a per-language reproducer-builder exists (the open gate, task #54). Rather
  than overclaim, `ingest.py` + `docs/ADOPTION.md` step 3 now say so plainly. See §11.21.
- **#54 — per-language reproducer-builder + Verify stage (closes ingest→Verify for
  non-Java)**: `tool/llm_repro_provider.py` asks the LLM for a FAILING test from a
  finding's hint/location/evidence (AI proposes; per-language pytest/go-test/#[test]/
  node:test). `pipeline.verify_finding` ensures a reproducer (Java via
  `run_repro_subagent`, others via the new builder), runs `run_harness.validate_repro`,
  and writes the reproducer gate (non-AI disposes) → reproduced | does-not-reproduce |
  no-reproducer-built | inconclusive. Wired into orchestrate step 1 (non-Java) + a
  `pipeline.py verify <id>` CLI. **Proven end-to-end on the live engine**
  (`verify_finding('py-1')` → real daemonless pytest fails with the bug → 'reproduced').
  +9 hermetic tests (builder extraction / skip-java / failure; verify reproduced /
  does-not-reproduce / builds-nonjava / no-build) → **suite 245**. Self-review caught a
  real regex bug (python tag `py(thon)?` was a capturing group hijacking group(1) →
  non-capturing). The non-Java **fix-builder** (to reach 'fixed') is the next gate (#55). See §11.22.
- **#55 — per-language fix-builder (closes the FULL loop for non-Java)**:
  `tool/llm_fix_builder.py` asks the LLM for a MINIMAL unified-diff patch (AI proposes
  the "smallest change that fixes the root cause"; per-language patch-containment rules
  mirroring the adapters; reuses day3's diff extractor) + a retry provider with failure
  feedback. Wired into orchestrate steps 2 (initial fix) + 3 (retry hook), language-aware.
  **Proven end-to-end on the live engine**: `orchestrate_finding('py-1')` → reproduce →
  fix → validate → **fixed** (daemonless, attempts=1). +6 tests (build_fix extract/
  no-diff/skip-java; prompt feedback+rule; provider retry; non-Java builds-fix-when-
  missing) → **suite 251** (a stale test asserting "no python builder" was correctly
  flipped). **The reproduce→fix→retry loop now works for all five languages**; the
  Anthropic-skills funnel is closed: ingest → reproduced → fixed (builders' LLM calls
  are live demos, mocked in tests). See §11.23.
- **Phase 3 started — §12.6 gated-PR draft queue (`tool/pr_draft.py`)**: promotes
  `pr.py`'s read-only preview into a persisted, reviewable DRAFT — a validated keeper
  finding is parked in an approval queue (`cell-1/hunt/pr-drafts/<id>.yaml`, status
  `pending-review`), a human approve/reject decision is recorded (rejections feed
  back), and re-queue preserves the decision. Like `pr.py` it **NEVER pushes** — an
  approved draft is pushed by a human via the draft's identity-gated `manual_steps`.
  Traversal-guarded ids. **Now fully WIRED**: CLI (`pipeline.py pr-draft` /
  `pr-drafts` / `pr-decide`), REST (`GET/POST /api/pr-drafts`, `GET /{id}`,
  `POST /{id}/decide` — 409 surfaces keeper blockers), and a React **Review** tab
  (queue-by-id, Approve/Reject, expandable identity-gated push steps; built + `tsc`
  clean). +5 unit + 2 endpoint tests; fixed two pre-existing tests that encoded the
  old "no non-Java builder" limitation → **suite 258**. CLI gate verified live (ec-1
  refused with blockers). Local branch-assembly deliberately deferred — kept the
  read-only posture (the human runs the draft's `manual_steps`). Next: §12.3
  discovery + §12.5 scheduler.

## [Unreleased] — Reproducer-sandbox Dockerfile UID/GID fix

**Date:** 2026-06-06
**Summary:** Fixed a reproducer-sandbox build failure surfaced by the first
real host execution of `run-repro.sh`. The Docker path had never actually run
before (the in-container daemon was always down), so this only appeared once
the validators ran against a live host daemon.

- `tool/repro/Dockerfile` — `groupadd -g 1000 repro` failed with
  `groupadd: GID '1000' already exists` (build exit 4 → `run-repro.sh` exit 2,
  a tooling error mis-readable as "no reproduction"). Root cause: the
  `maven:3.9-eclipse-temurin-17` base now sits on Ubuntu 24.04, which ships a
  default `ubuntu` user/group at UID/GID **1000** — the same id the validators
  pass via `--build-arg UID/GID` (the host user) so bind-mounted `/work` is
  writable. Fix: before creating `repro`, drop any pre-existing owner of the
  requested UID/GID (`userdel -r` / `groupdel`, guarded by `getent`), making the
  build robust to whatever the base image bundles. Affects both `run-repro.sh`
  and `run-fix.sh` (shared image).

## [Unreleased] — Automation hardening + reproducer-builder

**Date:** 2026-06-03
**Summary:** Hardened the headless subagent driver (parallel fan-out + bounded
retry), built the reproducer-builder (the Phase-0/Phase-1 "critical gap": an
agent that writes a JUnit reproducer per finding) and wired `run-repro.sh` into
the Day-3 reproducer gate as a non-AI validator. Fixed two Makefile defects and
a reproducer-sandbox integrity bug. Drove Cell #1 from 9/17 → 15/17 steps.

### Automation — headless driver (WS2)

- `claude_driver.run_claude_with_retry` — bounded exponential-backoff retry on
  **transient** failures only (`is_retriable`: timeout / 429 / 503 / overloaded
  / network); terminal errors (auth, validation, unknown model) fail fast. User
  Ctrl-C is never auto-retried.
- `claude_driver.run_claude_batch` — concurrent fan-out (ThreadPoolExecutor,
  bounded `max_parallel`), order-preserving, per-job kwargs override common.
- **Wired into the dashboard surface**: `pipeline.run_backtest_batch` fans out
  every prepared backtest entry through `run_claude_batch` (shared
  `_write_backtest_result` keeps single + batch paths identical). Exposed as
  `POST /api/subagent/backtest/batch` (declared before `/{issue_num}` so it
  isn't shadowed; body `{issue_nums?, max_parallel?}`), the MCP tool
  `bug_hunter.run_backtest_batch`, and a "Run all backtest agents (parallel)"
  button on the Day-2 step in the web UI. 5 tests in `tests/test_backtest_batch.py`
  (incl. a route-ordering guard).
- **Hunt + repro batches** — `run_hunt_batch` (default = the four Day-4
  self-consistency passes; each a fresh process, which is exactly what
  self-consistency needs) and `run_repro_batch` (default = every validation
  scaffold still missing a `.java`), both via `run_claude_batch` with shared
  `_write_hunt_result` / `_write_repro_result` helpers. Exposed on FastAPI
  (`/api/subagent/hunt/batch`, `/api/subagent/repro/batch` + single
  `/api/subagent/repro/{finding_id}`), MCP (`run_hunt_batch`,
  `run_repro_subagent`, `run_repro_batch`), and web-UI buttons on the Day-3
  findings, Day-3 gates, and Day-4 passes steps. 9 tests in
  `tests/test_hunt_repro_cli.py`.
- **Full CLI parity** — `python tool/pipeline.py <cmd>` exposes every headless
  op the dashboard/MCP can drive: `status`, `run-step`, `list/read/write-artifact`,
  `list-backtest`, `run-backtest[-batch]`, `label-backtest`, `run-explore`,
  `run-hunt[-batch]`, `run-repro[-batch]`. JSON to stdout; exit code reflects
  the op's `ok`. Batch `--parallel` is bounded to 10 (matching the API).
- All four `pipeline.py` subagent runners (`run_backtest_subagent`,
  `run_hunt_subagent`, `run_explore_subagent`, `label_backtest_subagent`) now
  dispatch through `run_claude_with_retry`.
- 12 pytest cases in `tests/test_claude_driver_retry.py`.

### Automation — reproducer-builder (WS3)

- `scripts/repro-builder-prompt.md` — canonical prompt (loaded at runtime, P1-14
  pattern) instructing an agent to author a minimal JUnit test that FAILS on
  buggy HEAD (encoding the post-fix expected behaviour).
- `day3-hunt.py`: `build_repro_prompt`, `extract_java_block`, `repro_class_name`
  / `repro_fqcn`, and two subcommands — `repro-prompts` (session/Make path) and
  `run-repros` (the non-AI executor).
- `pipeline.run_repro_subagent` — headless equivalent (build → dispatch → extract
  `.java`), for the dashboard/MCP surface.
- **`run-repros` is a non-AI validator**: it executes `run-repro.sh` and maps the
  exit code deterministically via `repro_status_from_exit` — exit 1 (JUnit FAILED
  on HEAD) → gate `pass` (bug reproduces); exit 0 → `fail`; tooling error →
  `not-attempted`. `set_reproducer_gate` does a surgical, comment-preserving edit
  of the scaffold's reproducer gate.
- 6 pytest cases in `tests/test_repro_builder.py`.

### Automation — fix-builder (#4)

The structural twin of the reproducer-builder: an agent proposes a patch; a
non-AI validator decides whether it makes the reproducer flip green.

- `scripts/fix-builder-prompt.md` — canonical prompt instructing a MINIMAL
  root-cause patch (unified diff) that turns the embedded reproducer FAIL→PASS.
- `day3-hunt.py`: `build_fix_prompt` (embeds the reproducer source),
  `extract_diff_block`, `fix_status_from_exit`, `set_fix_gate`, and two
  subcommands — `fix-prompts` (session/Make path) and `run-fixes` (executor).
  `set_reproducer_gate`/`set_fix_gate` now share a generic `_set_gate_block`.
- `pipeline.run_fix_subagent` / `run_fix_batch` — headless build → dispatch →
  extract `.patch`; exposed on FastAPI (`/api/subagent/fix/batch` +
  `/{finding_id}`), MCP (`run_fix_subagent`, `run_fix_batch`), the CLI
  (`run-fix`, `run-fix-batch`), and a "Build all fixes (parallel)" dashboard
  button on the Day-3 gates step.
- **`run-fixes` is a non-AI validator**: `run-fix.sh` applies the patch to the
  worktree, re-runs the reproducer in the Docker sandbox, and decides from the
  Surefire summary (PASS = fix works → exit 0 → gate `pass`; still failing →
  `fail`; patch didn't apply → `fail`; no test ran → `fail`; docker →
  `not-attempted`). It **restores the worktree** afterward (`git checkout`) so
  the target clone isn't left modified.
- 9 pytest cases in `tests/test_fix_builder.py`; run-fix.sh decision logic
  verified against canned Surefire outputs.
- **Demonstrated end-to-end on ec-1**: the fix-builder produced a minimal patch
  routing the leftover null through `_tryToAddNull` (the sibling-path mechanism
  the evidence named); `git apply --check` confirms it applies cleanly to
  pristine source. (Full validation needs Docker — `not-attempted` in-sandbox.)

### Automation — self-correcting orchestrator

The capstone loop that ties the builders + non-AI validators together with
feedback (the SWE-agent / OpenHands pattern, but on this project's own
primitives — no extra platform, no API key, gates stay non-AI).

- `pipeline.orchestrate_finding(id, max_fix_attempts=2)`: ensure reproducer
  (build if missing) → validate with `run-repro.sh` (the bug MUST reproduce,
  else stop — likely a false positive) → build a fix → validate with
  `run-fix.sh` (reproducer must flip GREEN) → **on failure, feed the failure
  note back to the fix-builder and retry** up to N. `orchestrate(ids)` runs it
  across findings with an outcome tally. Outcomes: `fixed` |
  `does-not-reproduce` | `fix-failed-after-retries` | `repro-not-attempted` |
  `fix-not-attempted` | `no-reproducer-built` | `no-fix-built`.
- The self-correction signal: `build_fix_prompt` / `run_fix_subagent` gained a
  `feedback` param; a failed `run-fix.sh` note is appended to the next prompt.
- **Orchestrator builders run at opus/high** (`ORCHESTRATOR_MODEL`/`EFFORT`,
  1800s timeout) — quality is decisive when a fix must flip a test green and
  survive retries — while the standalone/batch runners keep haiku/low for
  throughput. `run_repro_subagent`/`run_fix_subagent` took `model`/`effort`/
  `timeout_s` overrides; `orchestrate[_finding]`, the CLI (`--model`/`--effort`),
  REST (`model`/`effort` body), and MCP all thread them.
- Refactored the per-finding non-AI validators out of the loop commands:
  `day3-hunt.py` `validate_one_repro` / `validate_one_fix` (single-finding
  run-repro/run-fix + gate write); `cmd_run_repros` / `cmd_run_fixes` now loop
  over them.
- Exposed everywhere: CLI `orchestrate` (`--ids`, `--max-fix-attempts`,
  `--worktree`, `--network`), `POST /api/orchestrate`, MCP `bug_hunter.orchestrate`,
  and a "🔁 Orchestrate (reproduce→fix→retry)" dashboard button on Day-3 gates.
- 9 pytest cases in `tests/test_orchestrator.py` (happy path, retry-with-feedback,
  does-not-reproduce skip, retry exhaustion, build-when-missing, tally).
- **Why not adopt SWE-agent / OpenHands:** they're full agent *platforms* that
  bring their own harness + LLM config and own the loop end-to-end — they'd
  replace the pipeline and break its auditable-state + non-AI-validator +
  `claude -p` design. The pattern they validate is reused; the platform is not.

### Automation — dedup/CWE gate auto-suggest (#5)

Deterministic, non-AI advisory population of the two judgment-light Day-3 gates,
filling ONLY blanks:

- `suggest_cwe(type)` — a curated finding-type → CWE map (npe→476, off-by-one→193,
  race→362, integer-overflow→190, recursion→674, etc.); unmapped types get no
  suggestion. `suggest_dedup(scaffold)` — lists the OSV/GitHub candidates the
  auto-dedup already found as `references` (factual). `apply_gate_suggestions`
  fills `cwe` (+ CVSS N/A) and `dedup.references`/notes via the surgical
  `set_cwe_gate`/`set_dedup_suggestion` (`_set_gate_block` gained a `raw_fields`
  option so `references` is written as a real YAML list, not a quoted string).
- **Safety:** it never sets `dedup.is_duplicate` (a gate-relevant judgment) or
  `final_status`, so it cannot complete the `day3-gates` step on its own.
- Surfaces: `day3-hunt.py suggest-gates`, `pipeline.suggest_gates`, CLI
  `suggest-gates`, `POST /api/suggest-gates`, MCP `bug_hunter.suggest_gates`,
  and an advisory "Suggest dedup/CWE" dashboard button. 7 tests in
  `tests/test_suggest_gates.py`.

### Fix — Day-4 scaffold round-trip corrupted surgical-edited gates

`day4-finalize.py` rewrites each scaffold via `yaml.safe_dump`. With the old
`width=120` (and default ASCII escaping) it **wrapped a long em-dash `notes`
into a multi-line `\`-continuation scalar** and escaped `—`→`—`. The
single-line surgical gate editors (`set_*_gate`) then mangled that wrapped value
if a gate was (re)validated after Day-4 — producing invalid YAML (observed on
`ec-1` after running the orchestrator out of normal order). Fixes:
`safe_dump(..., width=10**9, allow_unicode=True)` keeps every field on one line;
the scaffold-loading loops now skip a malformed file with a warning instead of
crashing the batch (`_safe_load_scaffold`).

### Cell #1 — Days 3–4 driven to completion (2026-06-05): pipeline 17/17

With the gate legitimately at PROCEED, drove the rest to a clean finish:
- Set `ec-1` `final_status: failed-self-consistency` (appeared in 1 of 3 fresh
  contexts; honest terminal status — independent of the Docker-blocked reproducer,
  and not a fabricated reproduction verdict). This cleared the last human gate
  (`day3-gates`) → **pipeline 17/17**.
- Regenerated the pass-1 and final reports; filled the final report's
  Cost / Lessons / Recommendation sections honestly.
- **Cell #1 outcome:** calibration **PROCEED** (2-scanner baseline), but **0
  validated findings** — the loop's *validation* half never ran (Docker down).
  Recommendation: complete Cell #1's validation in a Docker-capable env before
  the Cell #2-vs-kill decision (it's an environment block, not a method failure).
- NOTE: re-running `day4-finalize.py report` regenerates the final report with
  blank HUMAN placeholders — it will overwrite the filled Cost/Lessons/Recommendation.

### Cell #1 — real Day-2 verdict unblocked (2026-06-05): BASELINES_MISSING → PROCEED

The Day-2 calibration gate was stuck at `BASELINES_MISSING` because
`cell-1/recon/scanners/` had no scanner output. Root cause was **not** network:
system `curl`/`git`/`mvn` reach the internet fine — only Python 3.13/OpenSSL 3.x
rejects the corporate proxy CA (`Basic Constraints … not marked critical`), which
is why semgrep's in-process registry fetch failed. Worked around by fetching the
rules with `git` and running Semgrep against LOCAL rule files:

- `git clone --depth 1 github.com/semgrep/semgrep-rules` → ran `semgrep --config
  <repo>/java --metrics off --disable-version-check` against an ABSOLUTE
  `targets/jackson-databind/src/main/java` path (so `load_baseline_files` strips
  the TARGET_DIR prefix). 113 real rules, 482 files, 28 findings across 11 files →
  `cell-1/recon/scanners/semgrep.json`.
- Re-ran `day2-backtest.py score` → **Decision: PROCEED to Day 3**. All three
  deterministic criteria pass: file_coverage@3=80% (≥30%), file_match_precision@5=
  100% (≥20%), and novel-over-baseline (8/10 entries hit a fix-file Semgrep did
  NOT flag; baseline overlap 0%, dupe count 0).
- **SpotBugs baseline added (2026-06-05)** — the correctness-oriented complement,
  built WITHOUT Docker: `mvn compile` of jackson-databind 2.21.3 (Java 21; P0-8
  `OSS_BUG_HUNTER_ALLOW_MVN=1` opt-in + `-Dmaven.wagon.http.ssl.insecure/allowall`
  for the proxy CA), SpotBugs 4.8.6 downloaded via curl, run on `target/classes`
  → 420 bug instances across 199 source files → `cell-1/recon/scanners/spotbugs.xml`.
- **Re-scored with BOTH scanners — verdict holds but tightens honestly:** baseline
  coverage 0% → **70%** (SpotBugs flags 7/10 fix-files), novel-over-baseline 8/10
  → **3/10**, decision still **PROCEED** (file_coverage@3=80%, precision@5=100%,
  novel ≥1). So against a *correctness* scanner the agent overlaps at the file
  level 70% of the time yet still clears the gate with a real 3/10 novel margin —
  the "novel signal over free tools" question, now backed by a security AND a
  correctness baseline, not Semgrep alone.

### Fixes — 2026-06-04 deep-review P1 cluster (R6–R14)

Followed the P0 cluster (below); tests in `tests/test_p1_fixes.py` (suite now 152).

- **R6 — `REPRO_NETWORK` allowlist.** `run-repro.sh`/`run-fix.sh` now validate the
  network mode: `none`/`bridge` allowed; `host` is REFUSED unless the operator
  sets `REPRO_ALLOW_HOST_NET=1` (loud supply-chain warning) — closing the silent
  re-arm of the poisoned-pom plugin-execution / metadata-endpoint RCE. (The
  Dockerized `mvn test` under `--network none` remains the supply-chain control;
  the allowlist is its enforcement.)
- **R7 — cross-process worktree lock.** Both validators acquire an exclusive
  `flock` on `.repro-sandbox.lock` (`repro_acquire_lock`) for their lifetime, so
  concurrent orchestrator/`run-fixes`/`make` runs can't interleave apply/checkout
  on the shared target clone and produce a wrong verdict.
- **R9 — orchestrator outcome.** `validate_one_repro` returning `status=None`
  (no `.java` to run) is now `no-reproducer-built`, not the misleading
  `does-not-reproduce`.
- **R10 — `suggest-gates` idempotency.** The dedup branch now also requires the
  notes field to be blank, so the no-candidate advisory note is written once, not
  rewritten on every run.
- **R11 — robustness + tests.** Batch scaffold loads go through `_safe_load_scaffold`
  (a malformed scaffold is reported `ok:False`, not a batch-aborting exception);
  added tests for the shell Surefire oracle (with R2), `build_fix_prompt` feedback
  rendering, the malformed-scaffold skip, and a REST `TestClient` check.
- **R12 — pristine-before-run.** Both validators `git reset --hard && git clean
  -fdq -e .m2` the worktree BEFORE running, so a prior crash, a leftover
  reproducer, or a session-driven agent edit can't contaminate the verdict.
- **R14 — input validation.** Malformed `passes` on `/api/subagent/hunt/batch`
  now returns 400 (was an uncaught 500); the CLI `_parse_pass_token` is tolerant
  of bad tokens (returns pass_num -1 → rejected downstream, no crash).
- Deferred: **R13** (reuse `claude_driver._terminate` in `_run_step_impl`) — a
  refactor of working process-teardown code; lower value, left for later. The
  `re`-import hoist part of R13 was done with R3.

### Fixes — 2026-06-04 deep-review P0 cluster (R1–R5)

The 12-perspective review (see `REVIEW.md`) found that several P0s were this
session's own regressions; fixed with tests (`tests/test_review_fixes.py`,
`tests/test_no_stale_paths.py` → suite now 140):

- **R1 — `_set_gate_block` corrupted bare/multi-word/escaped-quote values.** The
  value regex `(\"[^\"]*\"|\S*)` matched only the first word of an unquoted
  scalar (what `yaml.safe_dump` emits) and appended the old tail — the live root
  cause of the `ec-1.yaml` corruption (the file was hand-repaired; this is the
  actual fix). Now matches a quoted/single-quoted/flow-list/bare value to EOL
  with an optional preserved trailing comment. Verified stable across a
  suggest→day4-roundtrip→suggest gauntlet.
- **R2 — `run-repro.sh`/`run-fix.sh` aborted with exit 1 on a missing Surefire
  summary.** Under `set -euo pipefail`, the `grep | tail` substitution killed the
  script (exit 1 = false reproduction / false fix-rejection) on any compile/dep/OOM
  failure. Extracted a shared, abort-safe parser `scripts/_repro_decide.sh`
  (`surefire_counts`, `|| true`-guarded) sourced by both scripts — also removes
  the duplicated parse block the review flagged.
- **R3 — `finding_id`/issue path traversal.** Batch bodies fed `finding_ids`
  straight into `cell-1/...` write paths. Added `_safe_id` (`^[A-Za-z0-9_-]+$`)
  enforced in `_write_{backtest,repro,fix}_result`, `_build_fix_prompt_for`, and
  `run_repro_subagent` — rejects before any write.
- **R4 — agent patch `git apply`'d on the host with no containment.** `run-fix.sh`
  now pre-scans the diff (before the Docker preflight, so it's cheap + testable):
  rejects symlink/mode/rename/`.git` hunks and any path outside `src/{main,test}/java`,
  and restores with `git reset --hard && git clean -fdq -e .m2` so patch-created
  files can't persist across runs.
- **R8 — skipped tests mis-scored.** `surefire_counts` now also returns `Skipped`;
  a skipped-only run (`tests_run - skipped <= 0`) is treated as "no test ran"
  (not-attempted / compile-error) rather than a pass/fail signal.
- **R5 — `not-attempted` looked like success.** `orchestrate_finding` now tags
  each result `validated` (true only for `fixed`/`does-not-reproduce`);
  `orchestrate` reports `all_validated`/`inconclusive`; and the CLI exits non-zero
  (2) on an inconclusive run, so automation polling the exit code / `ok` can't
  mistake a Docker-down run for success.
- Incidental (R13): hoisted `re` to the top of `pipeline.py` (dropped the
  bottom-of-file import).

### Fix — stale `/workspaces/GW/AI` relocation paths

The project was relocated from `/workspaces/GW/AI/...` to
`/workspaces/GW/OpenSource/...`; stale absolute paths lingered. Cleaned up:

- **Day-3 prompt template** now uses a `{{TARGET_DIR}}` placeholder (substituted
  to the real target at `prepare` time) instead of the old magic-string
  `.replace("/workspaces/GW/AI/oss-bug-hunter", PROJECT_ROOT)` — proper
  relocatability, matching the repro/fix-prompt token convention.
- **`explore-prompt.md`** switched to relative paths (`targets/jackson-databind/`),
  which are location-independent.
- **Docs** (`README.md`, `phase-0-scope.md`) + the generated recon report
  corrected to the actual path.
- **Git worktrees**: `git worktree repair` + fixed the worktree `.git` links so
  all 10 backtest worktrees resolve again (was `fatal: not a git repository`).
- Guard test `tests/test_no_stale_paths.py` fails if any `scripts/`/`tool/`
  file reintroduces the old path. (`REVIEW.md` is left as a historical artifact.)

### Known gap — session-driven builders can mutate the target

The headless path (`claude_driver`) runs subagents read-only
(`--allowedTools "Read Glob Grep"`, P0-9). But when a builder is driven via the
Claude Code Agent tool with a full-tool agent, it CAN edit the target clone
directly (observed: the ec-1 fix agent applied its own change to
`CollectionDeserializer.java`). Mitigation for now: revert the target
(`git -C targets/jackson-databind checkout -- .`) after a session-driven build;
the builders should only *propose* (emit a diff/`.java`), never mutate.

### Fixes

- **run-repro.sh false-reproduction bug** — a missing/unreachable Docker daemon
  made `docker build` fail with exit 1 under `set -e`, and exit 1 is the script's
  code for "test failed / bug reproduces" — so a broken sandbox reported a FALSE
  reproduction. Added a `docker info` preflight + guarded build that exit 2
  (tooling error) before any test runs. Caught by running the wiring end-to-end.
- **run-repro.sh decides from Surefire summary, not the ambiguous mvn exit code.**
  `mvn` exit 1 = test failure OR compile error; exit 0 = passed OR *no test
  matched* (`-DfailIfNoTests=false`). The script now parses the last
  `Tests run: N, Failures: F, Errors: E` line: `N==0` → exit 2 (not-attempted);
  `F+E>0` → exit 1 (reproduces); else exit 0 (no repro). Closes a false-NEGATIVE
  (mis-named test class runs nothing → was scored "no repro") and a
  false-POSITIVE (compile error → exit 1 → was scored "reproduces"). Decision
  logic verified against 5 canned Surefire outputs.
- **day3-scaffolds state sentinel** — `cell-1/hunt/validation/.scaffolds-generated`
  was created only by the Makefile rule, so generating scaffolds via the
  script/CLI/dashboard left the `day3-scaffolds` step permanently "incomplete"
  (non-monotonic status). `day3-hunt.py validate` now touches the sentinel
  itself.
- **claude_driver.check_cli thundering herd** — the one-time CLI probe is now
  behind a `threading.Lock` with double-checked caching, so a `run_claude_batch`
  fan-out no longer spawns N concurrent `claude --help` subprocesses.
- **batch write-phase under pipeline_lock** — `run_{backtest,hunt,repro}_batch`
  now hold `pipeline_lock` around the serial file-write phase (not the parallel
  dispatch), so a batch can't interleave writes with a concurrent `make`/dashboard.
- **Makefile bare `python3`** — every `_check.py` gate died with
  `ModuleNotFoundError: yaml` unless the venv was on PATH. Added a venv-first
  `PYTHON` variable (mirrors the pattern already in `_status.sh`); replaced all
  13 `python3 $(SCRIPTS)` call sites.
- **Makefile labels gate** — `.runs.ok` required `backtest-labels-populated`,
  contradicting the README's P0-1 "labels are advisory, never gate" design.
  Dropped it; `.runs.ok` now gates on findings only.

### Cell #1 progress

- Day 2 backtest scored (10/10 findings + advisory labels): file_coverage@3=80%,
  file_match_precision@5=100%; verdict **BASELINES_MISSING** (Semgrep registry
  SSL-blocked + no Docker in sandbox — environmental, not a signal failure).
- Day 3 hunt (2 fresh agents): 1 edge-case finding (`ec-1`, CollectionDeserializer
  object-id null path); reproducer-builder produced a real JUnit test for it.
- Day 4 self-consistency (4 fresh passes): `ec-1` agreement **1/3, survived=False**
  (two passes evaluated it and omitted it). 0 validated findings; final report's
  Cost/Lessons/Recommendation remain human-fill.

## [Unreleased] — Multi-agent review remediation

**Date:** 2026-05-19
**Summary:** Closed out all 11 P0 and 18 P1 findings from the 12-perspective
review in `REVIEW.md`. 63 pytest cases added to pin load-bearing scoring math.
Hardened the security surface, made paths relocatable, unified the step DAG
and error contracts, added Docker reproducer infrastructure.

### Security

- **P0-7** — Dashboard now requires per-launch random bearer token (printed
  to stderr at startup, auto-injected into served HTML as `window.AUTH_TOKEN`).
  Host header allowlist enforced (env-overridable via
  `OSS_BUG_HUNTER_HOST_ALLOWLIST`). Defeats DNS-rebinding + cross-origin
  fetch attacks against the localhost-bound server.
- **P0-8** — `mvn package` gated behind `OSS_BUG_HUNTER_ALLOW_MVN=1` opt-in.
  Default: SKIP with a loud supply-chain warning. Closes the
  fetch-and-execute-upstream-plugin RCE primitive.
- **P0-9** — `claude -p` subagent invocations now default to
  `--allowedTools "Read Glob Grep"` plus an `--append-system-prompt`
  isolation preamble. Prompt injection from GitHub issue text can no longer
  drive Bash/Edit/Write on the dev box.
- **P1-17** — `tool/repro/Dockerfile` + `scripts/run-repro.sh` provide a
  sandboxed JUnit runner: non-root user, `--network none` by default,
  private `/work/.m2` cache. Doesn't auto-wire into Day-3 gates yet
  (Phase 1 work) — primitive available.

### Correctness — gate semantics (the headline)

- **P0-1** — LLM-judges-LLM auto-labeler **demoted to advisory only**.
  Gate input is now **deterministic file-coverage scoring**:
  `file_coverage@K` measures whether the agent's top-K findings point at
  files the historical fix actually touched. Auto-labeler still callable
  for triage; never affects PROCEED/KILL.
- **P0-3** — Precision metric split into `precision_matches@K`
  (matches_known only — gate input) and `precision_anyTP@K`
  (matches_known + unrelated_tp — informational). Closes the loophole
  where a charitable labeler trivially passed the gate.
- **P0-4** — `find_fix_commit` now filters candidates via
  `git merge-base --is-ancestor <sha> <pinned-tag>`. Stops 3.x fixes
  landing in a 2.x backtest with file paths that don't exist in the
  pinned tree.
- **P0-6** — Score function emits `BASELINES_MISSING` decision when
  Semgrep/SpotBugs output is absent. The "novel signal over baseline"
  gate is unfalsifiable without them; refuse to report PROCEED/KILL.
- **P0-10** — Added `tests/` directory: **63 pytest cases** pinning
  `classify()`, `score()`, `find_match()`, `extract_yaml_block()`,
  `_stub_findings()`, `_lib` keyword utilities, the new `_finding_file()`,
  `_load_prompt_templates()` (P1-14), and ISO date comparison (P1-6).
  Caught a real bug in the regex: `NullPointer\b` failed to match
  `NullPointerException`; fixed by appending `\w*` to identifier-like
  correctness keywords.

### Process safety

- **P0-11** — `claude_driver.run_claude` and `pipeline.run_step` rewritten
  with `Popen + start_new_session=True` and finally-block process-group
  cleanup (SIGTERM + 5s grace + SIGKILL). Multi-hour `claude -p` calls
  can no longer orphan on Ctrl-C. Default 1h timeout on `run_step`,
  15min on `run_claude`.
- **P1-8** — `claude_driver.check_cli()` smoke-tests `--model`, `--effort`,
  `--allowedTools`, `--append-system-prompt`, and `-p` flags against
  `claude --help` at startup. Anthropic CLI rename now fails fast with
  an actionable RuntimeError, not corrupted output.
- **P1-11** — Long-running FastAPI handlers (`run_step`,
  `run_*_subagent`, `label_*`) wrapped with `asyncio.to_thread`.
  `/api/status` polls no longer block during a multi-minute subagent run.
- **P1-12** — `pipeline_lock()` context manager uses `fcntl.flock` on
  `cell-1/.pipeline.lock`. Holds across `run_step` and `write_file`;
  serializes concurrent dashboard/MCP mutations. Documented limitation:
  `make` + dashboard simultaneous runs still race (Make isn't in the lock).

### Structure

- **P0-2** — All `PROJECT_ROOT = Path("/workspaces/...")` hardcoded
  paths replaced with `Path(__file__).resolve().parents[1]`. Bash
  scripts use `BASH_SOURCE`. Makefile derives from `MAKEFILE_LIST`.
  Project is now relocatable; the `cell-1/` and `jackson-databind`
  constants remain (Cell-#1 identity, parameterized when Cell #2 ships).
- **P0-5 + P1-10** — Single source of truth for the 17-step DAG:
  `tool/pipeline.py::PIPELINE`. `scripts/_status.sh` collapsed from
  ~100 lines to 17 (thin shim that calls `pipeline.status_lines()`).
  Makefile adds canonical aliases: `make day1-recon` ≡ `make recon`,
  `make day2-build` ≡ `make backtest-candidates`, etc. `make help`
  shows both names side-by-side.
- **P1-9** — Unified error envelope `{ok, error: {code, message, status}}`
  across FastAPI + MCP. Global FastAPI `HTTPException` handler;
  MCP `_text()` auto-wraps payloads. Auth middleware's 401/403
  responses upgraded to the envelope.
- **P1-14** — Day-3 hunt prompts loaded at runtime from
  `scripts/day3-novel-hunt-prompts.md` (canonical) instead of
  duplicated as Python string constants. Loader handles nested
  ```yaml example fences; legacy constants kept as
  `_LEGACY_*_DOC` for grep-discoverability but no longer used.

### Observability

- **P1-13** — `$(date -u)` literal in generated markdown reports
  replaced with `datetime.now(timezone.utc).strftime(...)`.
- **P1-15** — Self-consistency matcher diagnostic. `find_match_with_diag`
  returns `(match, reason)` where reason ∈ `{match, no_target_file,
  no_target_keywords, no_same_file_candidate, same_file_overlap_le_1:<N>}`.
  `cell-1-report.md` now shows a drop-reason breakdown so humans can
  tell "matcher threshold too strict" from "agent found different bugs."
- **P1-16** — `day2-backtest.py prepare` honors
  `OSS_BUG_HUNTER_WORKTREE_MAX` (default 10) with a disk-space warning
  about ~500MB per worktree.
- **P1-18** — Backtest dataset entries now carry `gt_2h_unaided: bool|null`
  field (default null). `cell-1-report.md`'s gate-(d) row reads this
  field and reports PASS / PARTIAL / FAIL with counts, replacing the
  prior `_HUMAN JUDGMENT REQUIRED_` placeholder.

### Reliability — script-level fixes

- **P0-12** (incidental, during P1-3) — Caught a regex bug in
  `CORRECTNESS_KEYWORDS`: `NullPointer\b` failed on
  `NullPointerException` because of the trailing `\b`. Fix: append
  `\w*` to identifier-like keywords. Discovered via the P0-10 pytest
  suite — exactly the kind of thing tests are supposed to catch.
- **P1-3** — `extract_yaml_block` Pattern-3 fallback removed. Agent
  MUST fence its output; unfenced returns `None` (caller saves raw
  output for human extraction) instead of silently slicing at
  `\n# NOTE:` or `\n**Bold:`. 3 regression tests added.
- **P1-4** — `_stub_findings` no longer text-sniffs. Detection is
  sentinel-based: `# AGENT-OUTPUT-NOT-YET-PASTED` line marks unfilled
  stubs; human deletes it on paste. Legacy `Paste the agent`/`Paste
  the YAML` markers still recognized for backward-compat with files
  written before the fix. Closes the "agent legitimately returned
  `findings: []`" false-positive.
- **P1-5** — Operator-precedence bug at `pipeline.py:270`
  (`labels_populated` computation). Rewrote to mirror
  `_check.py::backtest_labels_populated` logic exactly. Dashboard
  and `make status` now agree on per-entry populated state.
- **P1-6** — `find_fix_commit` ISO timestamp comparison now parses to
  `datetime` via `fromisoformat(s.replace("Z", "+00:00"))` before
  comparing. Fixes the case where `Z`-suffix timestamps vs
  `+00:00`-suffix timestamps lexically misordered.
- **P1-7** — `gh_api` curl fallback captures HTTP status code
  explicitly via `curl -w '%{http_code}'`. On 403/429: warns
  loudly + returns 1 + prints body excerpt. Callers stop pagination
  on first failure (was: silent `[]` echo).

### Infrastructure added

- **`tests/`** — 63 pytest cases (~330 LOC), runnable via
  `.venv/bin/python -m pytest tests/`. `conftest.py` loads
  dash-named scripts via `importlib`.
- **`tool/repro/`** — Dockerfile + `scripts/run-repro.sh` for
  sandboxed JUnit reproducer execution.
- **`CHANGELOG.md`** — this file.

### Deliberately deferred

- **Wiring `run-repro.sh` into Day-3 validation gates** — Phase 1.
- **Per-cell config schema** (`cells/<name>.yaml`) replacing the
  remaining `cell-1`/`jackson-databind` constants — waits for
  Cell #2 actually being scheduled. Refactor at consumer time, not
  on speculation.
- **Auto-labeler removal** — kept as advisory-only; not gate input.

### Tests

```
$ .venv/bin/python -m pytest tests/ -q
.................................................................     [100%]
63 passed in 0.03s
```

| Suite | Cases | Pins |
|---|---|---|
| `test_check.py` | 7 | `_stub_findings` sentinel + backward compat |
| `test_classify.py` | 6 | `classify()` correctness/feature/security keywords |
| `test_day3_prompt_loader.py` | 8 | runtime `.md` template loading + nested fence handling |
| `test_extract_yaml.py` | 11 | fenced-only extraction + 2 regression tests for the dropped Pattern-3 |
| `test_find_match.py` | 7 | self-consistency matcher threshold + file-path discipline |
| `test_iso_date.py` | 4 | `Z` vs `+00:00` equivalence |
| `test_lib.py` | 8 | `extract_keywords` + `extract_keyword_set_ci` |
| `test_score.py` | 6 | candidate-ranking weight boundaries pinned (+3/-5/-2/+2/+1.5) |
| `test_score_finding_file.py` | 6 | `_finding_file()` edge cases |
