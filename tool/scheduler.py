"""Autonomous scheduler / outer loop (plan §12.5, Phase 3 — the loop-closer).

Consumes the discovery queue (discovery.enqueue) and drives each candidate through
the per-finding pipeline — clone → (env-bootstrap) → hunt → verify → fix → gated-PR
draft — BUDGETED, IDEMPOTENT, AUDITED, with a KILL-SWITCH. The per-candidate steps are
INJECTABLE (the `Steps` protocol) so the loop logic is hermetic + testable; the default
`EngineSteps` wires the real components. It still NEVER pushes — it stops at a reviewable
draft (the human approves + pushes, §12.6).

HONEST STATUS: the loop STRUCTURE (budget / idempotency / audit / kill-switch / dispatch)
is built + tested here. `EngineSteps.hunt` IS wired (#61, via tool/hunt.py → vuln-scan →
ingest); `EngineSteps.bootstrap` is a no-op pass-through — the real env-bootstrap runs inside
the engine's `_maybe_bootstrap` on the verify/fix path (#62/#63); wiring it into the loop
BEFORE hunt is a follow-on. A full live run still needs the model + an open-network host.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CELL = ROOT / "cell-1"
QUEUE = CELL / "hunt" / "discovery-queue.yaml"
STATE = CELL / "hunt" / "scheduler-state.yaml"
STOP_FILE = CELL / "hunt" / "STOP"           # touch this to halt between candidates

# terminal per-candidate outcomes — skipped on re-run once attempts are exhausted.
_TERMINAL = {"drafted", "no-draft", "no-bug-found", "clone-failed", "error", "ineligible"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class Budget:
    max_targets: int = 5          # candidates processed per run_once
    max_attempts: int = 1         # re-attempts per repo across runs (idempotency)


class Steps:
    """Per-candidate pipeline steps — inject for tests or override for real wiring.
    Raise to signal a hard error (the driver isolates it to this candidate)."""

    def clone(self, cand: dict) -> str | None:     # -> target name, or None on failure
        raise NotImplementedError

    def bootstrap(self, target: str) -> bool:      # M5 (#46) — env setup; default no-op
        return True

    def hunt(self, target: str) -> list:           # -> [finding_id, ...]
        raise NotImplementedError

    def verify(self, finding_id: str) -> str:      # -> "reproduced" | ...
        raise NotImplementedError

    def fix(self, finding_id: str) -> str:         # -> "fixed" | ...
        raise NotImplementedError

    def draft(self, finding_id: str) -> bool:      # -> queued a reviewable PR draft?
        raise NotImplementedError


def _stop_file_kill():
    return STOP_FILE.exists()


def load_queue(path=QUEUE) -> list:
    """Read discovery.enqueue()'s output → ranked candidate list (highest score first)."""
    try:
        doc = yaml.safe_load(Path(path).read_text()) or {}
    except Exception:
        return []
    return doc.get("candidates", []) if isinstance(doc, dict) else (doc or [])


def _load_state(path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text()) or {}
    except Exception:
        return {}


def _save_state(path, state) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(state, sort_keys=False))


def _run_candidate(steps: Steps, cand: dict, repo: str, record) -> str:
    """One candidate: clone → bootstrap → hunt → (verify → fix → draft)*. Returns a
    terminal outcome. Exceptions are caught by run_once (isolated to this candidate)."""
    target = steps.clone(cand)
    if not target:
        record(repo, "clone", "failed")
        return "clone-failed"
    record(repo, "clone", target)
    if not steps.bootstrap(target):
        record(repo, "bootstrap", "failed")
        return "error"
    findings = steps.hunt(target) or []
    record(repo, "hunt", f"{len(findings)} finding(s)")
    if not findings:
        return "no-bug-found"
    drafted = 0
    for fid in findings:
        v = steps.verify(fid)
        record(repo, "verify", f"{fid}:{v}")
        if v != "reproduced":
            continue
        fx = steps.fix(fid)
        record(repo, "fix", f"{fid}:{fx}")
        if fx != "fixed":
            continue
        if steps.draft(fid):
            drafted += 1
            record(repo, "draft", f"{fid}:queued")
    return "drafted" if drafted else "no-draft"


def run_once(candidates, steps: Steps, *, budget: Budget | None = None,
             state_path=STATE, kill_switch=None, log=None) -> dict:
    """Drive up to `budget.max_targets` candidates through the pipeline. Idempotent
    (skips repos already terminal in state once attempts are exhausted), kill-switch
    aware (checked before each candidate; defaults to the STOP file), per-candidate
    error-isolated, and audited. NEVER pushes. Returns
    {processed, skipped, outcomes:{...}, audit:[...]}."""
    budget = budget or Budget()
    _log = log or (lambda *a: None)
    kill = kill_switch or _stop_file_kill
    state = _load_state(state_path)
    audit: list = []
    outcomes: dict = {}
    processed = skipped = 0

    def record(repo, step, outcome):
        audit.append({"ts": _now(), "repo": repo, "step": step, "outcome": outcome})
        _log(f"[sched] {repo}: {step} -> {outcome}")

    for cand in candidates:
        if processed >= budget.max_targets:
            break
        if kill():
            record("-", "kill-switch", "halt")
            break
        repo = cand.get("repo") or "?"
        st = state.get(repo, {})
        if st.get("status") in _TERMINAL and st.get("attempts", 0) >= budget.max_attempts:
            skipped += 1
            record(repo, "skip", st.get("status"))
            continue
        processed += 1
        try:
            outcome = _run_candidate(steps, cand, repo, record)
        except Exception as e:                     # isolate one bad candidate
            record(repo, "error", str(e)[:120])
            outcome = "error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        state[repo] = {"status": outcome, "attempts": st.get("attempts", 0) + 1,
                       "updated": _now(), "score": cand.get("score")}
        _save_state(state_path, state)
    return {"processed": processed, "skipped": skipped, "outcomes": outcomes, "audit": audit}


class EngineSteps(Steps):
    """Default real wiring. clone → targets.add_target (trust=False, fail-closed);
    verify → pipeline.verify_finding; fix → pipeline.orchestrate_finding; draft →
    pr_draft.queue_draft. `bootstrap` (M5 #46) and `hunt` (the Anthropic skills, run in
    Claude Code) — `hunt` is WIRED via tool/hunt.py (headless vuln-scan → ingest);
    `bootstrap` (M5 #46) is still a no-op, so multi-dep real repos need M5. The LLM
    calls (hunt/fix builders) need the model + a host — they're live, not hermetic."""

    def clone(self, cand: dict) -> str | None:
        from exec_backend import select_backend, BackendError
        import targets as _tg
        # Untrusted repos require docker/podman. Fail here rather than silently
        # at validate time (hours later) when no container backend is available.
        try:
            select_backend(trusted=False)
        except BackendError:
            return None
        name = (cand.get("repo") or "").replace("/", "__")
        url = cand.get("url") or f"https://github.com/{cand.get('repo')}"
        try:
            _tg.add_target(url, name=name, trusted=False)
        except Exception:
            return None
        return name if _tg.get_target(name) else None

    def hunt(self, target: str) -> list:
        import hunt as _hunt
        import targets as _tg
        tdir = ROOT / "targets" / target
        lang = _tg.detect_language(str(tdir))
        return _hunt.vuln_scan(str(tdir), language=lang, target_name=target).get("finding_ids", [])

    def verify(self, finding_id: str) -> str:
        import pipeline as _pl
        return _pl.verify_finding(finding_id).get("outcome", "inconclusive")

    def fix(self, finding_id: str) -> str:
        import pipeline as _pl
        return _pl.orchestrate_finding(finding_id).get("outcome", "inconclusive")

    def draft(self, finding_id: str) -> bool:
        import pr_draft as _pd
        return _pd.queue_draft(finding_id).get("ok", False)


def plan(candidates, *, budget: Budget | None = None, state_path=STATE) -> dict:
    """DRY-RUN: what run_once WOULD process vs skip (idempotency + budget), without
    executing any step (no clone/hunt/LLM/push). Safe to run anytime."""
    budget = budget or Budget()
    state = _load_state(state_path)
    would, skip = [], []
    for cand in candidates:
        repo = cand.get("repo") or "?"
        st = state.get(repo, {})
        if st.get("status") in _TERMINAL and st.get("attempts", 0) >= budget.max_attempts:
            skip.append({"repo": repo, "status": st.get("status")})
        elif len(would) < budget.max_targets:
            would.append({"repo": repo, "score": cand.get("score")})
    return {"would_process": would, "would_skip": skip,
            "budget": {"max_targets": budget.max_targets, "max_attempts": budget.max_attempts}}
