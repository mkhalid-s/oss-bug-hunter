# Code Review: Documentation-accuracy pass (oss-bug-hunter)

**Date:** 2026-06-08
**Reviewers:** 12 perspectives (local Claude agents): security, architect, chief-architect, ops, code-quality, devils-advocate, testability, simplifier, user-advocate, api-designer, critic, requirements-analyst
**Scope:** the 6 files changed in the docs-accuracy pass — `README.md`, `docs/MULTI-LANGUAGE-VISION.md`, `CHANGELOG.md`, `scripts/run-repro.sh`, `scripts/run-fix.sh`, `scripts/day3-hunt.py`. Not a git repo; reviewed against file state + verified claims against `tool/`.

## Summary

The numeric corrections (152→228 tests, 13→18 MCP tools, 5 languages, "needs Docker"→local/Docker) are **all verified true**, the LEGACY markers are **accurate and delete-safe** (confirmed the Makefile never wires them and the converged orchestrator never shells out to them), and `pr.py` read-only / trust-gating / convergence claims hold. **No P0s.** The headline finding is ironic: the new §11.17 "proof-status" table — the artifact meant to *stop* overstatement — contains a now-false environment claim. `cargo 1.95.0` was installed in this devcontainer today (2026-06-08), so "cargo absent / Rust host-only" is wrong; Rust in fact reproduces→fix→**validates end-to-end locally** (verified by running `rs-1`). The other cluster is README **internal inconsistency**: the "Project layout" tree and the "dashboard" wording still describe the pre-convergence, jackson-only shape that the pass's own new top-note contradicts.

---

## P0 — MUST FIX (0 issues)
None. Nothing is broken or dangerous; no secret leakage; trust-gating and read-only PR posture verified accurate.

---

## P1 — SHOULD FIX (3 issues)

### P1-1: §11.17 "proof status" + CHANGELOG falsely claim "cargo absent / Rust host-only"; cargo is present and Rust validates end-to-end here
**Consensus:** 3/12 explicitly (code-quality, testability, devils-advocate); independently reproduced during synthesis.
**Flagged by:** code-quality, testability, devils-advocate
**Files:** `docs/MULTI-LANGUAGE-VISION.md:716,725-730`; `CHANGELOG.md:203-205`; `cell-1/hunt/validation/rs-1.yaml:13-17`; `tests/test_spike_harness.py:562` (stale comment + no Rust e2e test)
**What's wrong:** `cargo 1.95.0` is installed (`/home/node/.cargo/bin/cargo`, symlink dated 2026-06-08 08:30 — added *after* the Rust entry was written). The table says "podman + cargo absent" and grades Rust "unit-tested here … `cargo test` run validated on a Rust host — no cargo in this container." Running the documented command now yields `status=validated reproduced=True fixed=True attempts=1` — a real cargo compile + test, daemonless, in this devcontainer. Meanwhile `rs-1.yaml` still says `not-attempted`/`pending`/"awaiting cargo host", and `CHANGELOG.md:205` repeats "cargo is absent … validated on a Rust host" (an unverified host claim). Three internally-disagreeing statements about the same fact.
**Why it matters:** This is the exact overstatement the pass existed to remove, relocated from "needs Docker" to "validated on host." It also *under*-sells the engine (Rust looks unprovable-here when it's now provable).
**Fix:** Make it true. Update `rs-1.yaml` gates → pass/validated (notes: validated locally via cargo 1.95.0, 2026-06-08); promote Rust in §11.17 to "end-to-end, local, in this devcontainer"; correct the environment line ("cargo present"); fix `CHANGELOG.md:203-205`; add a `shutil.which("cargo")`-guarded `test_rust_adapter_validates_synthetic_target` (so Rust e2e is test-backed like Java/Python/Go/JS); fix the stale `test_spike_harness.py:562` "cargo absent here" comment.

### P1-2: README:380-382 stale executor claim — attributes orchestrate validation to the legacy `run-repros`/`run-fixes` Docker sandbox
**Consensus:** 2/12 (critic P1, devils-advocate adjacent)
**Flagged by:** critic, devils-advocate
**File:** `README.md:380-382`
**What's wrong:** "the non-AI executors `day3-hunt.py run-repros` / `run-fixes` (Docker sandbox) decide the gate and restore the target worktree afterward." For the converged orchestrate path this is now false — `pipeline._orchestrate_finding` delegates to `run_harness.orchestrate`, whose own `pristine()` restores the worktree; `run-repros`/`run-fixes` are the legacy Cell-#1 batch executors. This stale claim sits directly under the block this pass edited.
**Fix:** Scope the sentence to the Cell-#1 batch path, or reword to "the non-AI validators in `run_harness.orchestrate` (via `tool/exec_backend.py`) decide each gate and restore the worktree (`pristine()`)."

### P1-3: "reached by the CLI" + "the dashboard" mislead — the documented CLI can't run non-Java, and "dashboard" conflates the legacy UI with the new React app
**Consensus:** 2/12 (user-advocate P1 x2, api-designer P2)
**Flagged by:** user-advocate, api-designer
**Files:** `README.md:8-10, 373-376, 384-388`
**What's wrong:** (a) The only CLI the README documents is `tool/pipeline.py orchestrate`, which has **no `--lang`** (it derives language per-finding from the scaffold); the multi-language CLI (`tool/run_harness.py orchestrate … --lang <lang>`) is never named as an entry point. So "one converged orchestrator over Java·Python·Go·Rust·JS, reached by the CLI" is, for a CLI user, only true for Java. (b) Line 10 says the app is "reached via … the dashboard," but the only thing the README calls "the dashboard" is the legacy vanilla-JS UI at `/`; the new React app is at `/app`. Both `tool/web/` and `tool/webapp/` exist.
**Fix:** Document `run_harness.py orchestrate … --lang` (lift the §11.15 example into the README's CLI section) and note `pipeline.py orchestrate` is scaffold-driven (no `--lang`); in line 10 say the visual app is at `/app` and stop using "dashboard" for it.

---

## P2 — RECOMMENDED (6 issues)

### P2-1: README "Project layout" tree omits the entire converged engine + React app (highest consensus)
**Consensus:** 7/12 (architect, devils-advocate, user-advocate, requirements-analyst as P2; chief-architect, ops, critic as P3)
**Flagged by:** architect, chief-architect, ops, devils-advocate, user-advocate, critic, requirements-analyst
**File:** `README.md:454-460`
**What's wrong:** The tree under `tool/` lists only `pipeline.py`, `server.py`, `mcp_server.py`, `claude_driver.py`, `web/` — and none of `adapters.py`, `run_harness.py`, `exec_backend.py`, `run_store.py`, `findings.py`, `targets.py`, `pr.py`, `llm_fix_provider.py`, `tool/webapp/` (all exist on disk). It still calls `web/` "the dashboard frontend" with no mention `tool/webapp/` is the React app. The one place a reader goes for the file-map shows the Cell-#1 shape the new top-note contradicts.
**Fix:** Add the engine + app modules to the tree; distinguish `web/` (legacy U0 page) from `webapp/` (React SPA served at `/app`).

### P2-2: tests/ subtree lists 7 of 21 files but the same line was updated to "(228 passing)"
**Consensus:** 1/12. **Flagged by:** requirements-analyst. **File:** `README.md:461-469`
**Fix:** Complete the list or replace the enumeration with "… and N more (see prose below)."

### P2-3: README:373 one-liner drops the untrusted-Java case
**Consensus:** 1/12. **Flagged by:** devils-advocate. **File:** `README.md:373`
**What's wrong:** "validates locally (trusted) or in Docker (untrusted non-Java)" omits untrusted-Java, which returns `TOOL_ERROR` (not local, not Docker — container-Java is unwired). The longer prose at 330-334 is correct.
**Fix:** "… (untrusted Java not yet supported — fail-closed)."

### P2-4: JS runner story inconsistent — plan commits to jest/vitest/mocha; the shipped adapter uses `node --test`
**Consensus:** 1/12. **Flagged by:** chief-architect. **Files:** `docs/MULTI-LANGUAGE-VISION.md:163-164,282,589,678`
**Fix:** Note in §11.16 (or amend §3.6/§7) that `JsNodeTestAdapter` chose the stdlib `node --test`; jest/vitest/mocha detection is a follow-on (so the "remaining risk" and "done" claims agree).

### P2-5: hardcoded counts (228 tests, 18 tools) are a rot trap
**Consensus:** 2/12 (critic, simplifier). **Files:** `README.md:396,457,461,478`
**What's wrong:** Current-state counts repeated in 3-4 README spots must all move together on any test/tool change. (The dated §11/CHANGELOG snapshots are fine as history.)
**Fix (judgment):** optional — soften the README assertions ("the full suite under `tests/`", "all `bug_hunter.*` tools") or keep exact numbers but accept the maintenance cost.

### P2-6: §11.16 closing paragraph re-narrates the doc-sweep that CHANGELOG already lists (triplication)
**Consensus:** 1/12. **Flagged by:** simplifier. **File:** `docs/MULTI-LANGUAGE-VISION.md:706-712`
**Fix:** Trim to one sentence pointing at CHANGELOG `[Unreleased]` + §11.17; don't restate the 152→228 / 13→18 deltas a third time.

---

## P3 — MINOR (6 issues)
- **§11.15↔§11.16 adjacency:** §11.15 ends "JS is the remaining language" immediately before §11.16 "spans all five." Add a forward-pointer to §11.15. (chief-architect)
- **CHANGELOG `[Unreleased]` header dated 2026-06-06** but contains 06-07/06-08 bullets. Bump the date or split a subsection. (devils-advocate)
- **`tool/mcp_server.py:18-29` module docstring lists 10 of 18 tools** — out of the 6-file scope but the same "18 tools" contract; replace with "see TOOLS" to stop re-drift. (api-designer)
- **Java first run fetches the JUnit console jar** via `mvn` (`network="bridge"`) on a cold `.m2` — "no Docker" is true but not fully offline on first run. (ops)
- **`/app` requires `vite build`**; README never documents the build step and `make dashboard` serves the legacy UI. (user-advocate, devils-advocate)
- **README blockquote omits "no Node at runtime"** (FastAPI serves the prebuilt bundle) — present in CHANGELOG, missing in README. (devils-advocate)

---

## Positive Observations
- **The numeric/accuracy corrections are real and verified:** 228 tests actually pass, 18 `bug_hunter.*` tools exist, 5 languages are wired (`_ADAPTERS` + Java), and the "needs Docker → local (trusted) / Docker (untrusted non-Java)" rewrite matches `_select_direct_backend` / `_select_adapter_backend` / `LocalBackend.supports_untrusted()==False`.
- **The LEGACY markers are exemplary:** accurate ("not used by the converged orchestrator", not the dangerous "unused"), delete-safe (Makefile doesn't wire them; converged path never shells out — verified), and applied at the operator's point-of-use (both shell headers + both `cmd_run_*` docstrings + both `add_parser` help strings).
- **§11.17 is the right instinct** — an authoritative, environment-bounded proof matrix that subordinates the scattered "BUILT/PROVEN" tags. Its only defect is the stale cargo row (P1-1); the container/podman/SSE/macOS host-only rows are honestly graded.
- **Security posture documented accurately:** `pr.py` read-only / never-pushes, the personal-vs-enterprise identity gate, trust-gating, and `network=none` defaults all match code; no secret leakage; no guidance toward unsafe actions.
