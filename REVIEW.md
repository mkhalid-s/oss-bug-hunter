# Code Review (DEEP, 12 perspectives): session `fa55add..HEAD` + review fixes

**Date:** 2026-06-10
**Reviewers:** 12 local Claude agents (Deep pack: security, architect, chief-architect, ops, chief-programmer, devils-advocate, testability, simplifier, user-advocate, api-designer, critic, requirements-analyst)
**Scope:** the current working tree vs `fa55add` — 6 commits (#62/#63/#56/#59/#51/#25) **+** the Standard-review fixes (incl. the first pristine rewrite). 31 files, +1538/−201.

## Summary

A full deep pass on the post-fix state. It caught **one P0 that the *previous* (Standard) review's
own pristine fix introduced** — now fixed and verified. The security-critical invariants
(`container_env` can't leak `GH_TOKEN`, untrusted installs never run on the host, lock ordering
acyclic, enrichment False-safe, `#56` no nested-`.git`/gitlinks) were re-verified correct by
multiple perspectives. The remaining findings are two **architectural P1s** (real, but autonomy-
roadmap scope — filed as #64/#65, not push-blockers), some **P2s fixed in this pass**, and P2/P3
advisories. **Critic verdict: push-ready** once the P0 is fixed (it is).

---

## P0 — MUST FIX (1 issue — FIXED in this pass)

### P0-1: pristine's `_SCAN_SKIP` shortcut silently deleted a real manifest in a `build`/`dist`/`target`-named dir — FIXED
**Consensus:** devils-advocate (P0, reproduced live), security-reviewer (P1 symlink variant), testability (P2), simplifier (P2 root cause)
**File:** `tool/run_harness.py` `pristine()`

The Standard-review fix walked reported untracked dirs but skipped any dir whose name was in
`_SCAN_SKIP` (`build`/`dist`/`target`/…). Those are **legal crate/package/module dir names**, so an
untracked `build/Cargo.toml` was collapsed by `git clean -fdn` to `Would remove build/`, skipped,
and then **deleted** — trading the original hole for a narrower one. A symlink variant (`is_dir()`
follows a symlink → `os.walk` walks the whole filesystem) was also found.

**Fix (applied):** replaced the `git clean -fdn` + `os.walk` + `_SCAN_SKIP` approach with `git
status --porcelain --untracked-files=all` (the simplifier's suggestion, verified) — it lists every
untracked file individually (no dir-collapse, no symlink-follow, no walk/prune), honors `.gitignore`
like the real clean, excludes the preserved caches by pathspec + a parts-check, and uses
`core.quotePath=false`. Regression test extended: a manifest under `build/` is caught, a symlinked
dir is not traversed, a pure build dir is still cleaned. **322 tests.**

---

## P1 — SHOULD FIX (2 issues — DEFERRED to follow-on tasks, not push-blockers)

### P1-1: Findings carry no `component` slot → monorepo wrong-component (filed #64)
**Flagged by:** chief-architect (lead), requirements-analyst, devils-advocate (as the Rust first-lib root cause)
A finding is `{language, target=repo-name}`; the worktree is derived as the repo **root**.
`detect_components` (#50) and Rust `_resolve_crate` (#51) know the real component but can't pass it,
so Rust falls back to "first lib member" (wrong crate on multi-lib workspaces) and Python/Go/JS run
at the root (wrong-component on polyglot monorepos → silent false verdicts). **The highest-leverage
structural fix:** thread `component:{dir,language}` from `detect_components` into the scaffold + run
`cwd=worktree/dir`. Roadmap-scope (§12), not introduced this session → **deferred (#64)**.

### P1-2: `EngineSteps.bootstrap` is a no-op → the scheduler hunts un-bootstrapped repos (filed #65)
**Flagged by:** chief-architect
The real env-bootstrap (#62/#63) runs only on the engine's verify/fix path, not the scheduler's
clone→bootstrap→hunt path, so a live loop would hunt before building. Documented honestly (scheduler
docstring + §11.35); wiring it (or making `_maybe_bootstrap` the single authority) is **deferred
(#65)**. Not a blocker for these commits (the live loop is already gated on a model + open network).

---

## P2 — RECOMMENDED

**Fixed in this pass:**
- **bare `pytest` collected the cloned `targets/` repos → 12 collection errors** (critic, ops). Added
  `pytest.ini` (`testpaths=tests`, `norecursedirs=targets …`) — bare `pytest` now collects exactly 322.
- **stale "hunt is unwired/TODO" text** (user-advocate) in `scheduler.py` module docstring + `schedule
  --run` CLI help — corrected (hunt wired #61; bootstrap is a documented no-op).
- **README test count `228–229` → 322** (critic, ops).

**Deferred (advisory):**
- **Adapter contract is duck-typed; `getattr(...container_cache_env...)` default masks it; cache-keep
  invariant spread across 3 files** (architect, api-designer, chief-programmer, devils-advocate) →
  HarnessAdapter Protocol/ABC + derive `_CLEAN_KEEP` from adapters — **filed #66**.
- **Rust `_resolve_crate` "first lib member"** is a load-bearing heuristic (subsumed by #64); the
  all-bin-workspace + root-package-with-members edges degrade to BUILD_ERROR (safe). Documented §11.33.
- **#25 batch writers still hold the global `pipeline_lock`** around the per-key loop — coherence, not a
  defect (ordering acyclic, verified); the shard's win lands on the orchestrate path. Documented §11.34.
- **`RateLimiter` budget is per-source-instance/per-process** — N GitHub sources/run = N× the pace.

## P3 — MINOR (advisory; see prior REVIEW + perspective outputs)

Lock files accumulate under `cell-1/.locks/` (swept by `make clean`); `_set_gate` no longer fail-soft
under a stale lock; `finally: pristine()` cleanup return discarded; `demo_targets.materialize(name)`
lacks a `_safe_name` check (no reachable exploit); `enrich_candidate` mutates-and-returns; the
`pkg::stem` selector is a stringly-typed channel; small untested branches (`_resolve_crate` fallbacks,
RateLimiter pacing+backoff together, `enrich` partial-known); doc: "5 demo targets" → 6 (`rustws-demo`).

---

## Verified-correct (re-confirmed on the post-fix state)

- **`container_env` cannot leak host secrets** — `_ContainerBackend.run` emits `-e` only from
  `spec.container_env`; `spec.env`/`os.environ` reach only the trusted local backend. Checked across
  env / mounts / build-args / **image env** (Dockerfiles bake only cache dirs; runtime `-e` overrides).
- **Untrusted installs never run on the host** — `_local_run` reachable only on the trusted branch;
  untrusted → container or fail-closed.
- **Locks** — cross-process `flock`, per-thread reentrant, exception-safe; ordering acyclic (the
  orchestrate path takes only `keyed_lock`; batch takes `pipeline_lock`→`keyed`, never reversed).
- **#59 enrichment** — best-effort, truncated-tree leaves fields unknown (not a false `False`),
  `_enrichable` pre-gate avoids wasted API calls, native-ext regex `$`-anchored (no suffix bypass).
- **#56** — all 6 `_src` targets materialize to gitignored working copies; no nested `.git`/gitlinks;
  `commit.gpgsign=false`. **rustws-demo** genuinely exercises the `-p` member path.
- **pristine perf** — measured ~0.23s guard overhead on a 137M real clone; the status-listing rewrite
  needs no filesystem walk. Negligible.
