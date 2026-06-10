"""Pipeline state + step runner. Used by the FastAPI server and MCP server.

Wraps the existing scripts/ — does not duplicate their logic. Each step is a
small declaration: id, title, kind (auto|human), state predicate, action.

Reuses scripts/_check.py's content-check functions for state detection.
"""
from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

# Make scripts/_check.py importable for state detection
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import _check  # noqa: E402

SCRIPTS = PROJECT_ROOT / "scripts"
CELL = PROJECT_ROOT / "cell-1"
VENV_PY = str(PROJECT_ROOT / ".venv" / "bin" / "python")

# R3: ids (issue numbers, finding ids) become path components under cell-1/.
# Reject anything that isn't a bare token so a batch body like
# {"finding_ids":["../../etc/x"]} can't drive an arbitrary-path write.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_id(value) -> bool:
    return bool(_SAFE_ID_RE.match(str(value)))


def _safe_load_scaffold(scaffold_p):
    """(data, None) on success, (None, msg) if the YAML is malformed — so one
    bad scaffold can't abort a whole batch (R11)."""
    import yaml as _yaml
    try:
        return (_yaml.safe_load(scaffold_p.read_text()) or {}), None
    except _yaml.YAMLError as e:
        return None, f"malformed scaffold {scaffold_p.name}: {str(e)[:120]}"

# ---- P1-12: global pipeline lock + #25: per-key sharded locks (G1) ----
_LOCK_PATH = CELL / ".pipeline.lock"
_LOCKS_DIR = CELL / ".locks"            # one lock file per key for keyed_lock (#25)
_held_keys = threading.local()          # per-thread set of held keys → keyed_lock is reentrant


def _flock_acquire(f, timeout_s: float, what: str) -> None:
    """Block-acquire an exclusive flock on open file `f`, polling until `timeout_s`. Raises
    RuntimeError on timeout so a STALE lock surfaces loudly instead of hanging forever."""
    deadline = time.time() + timeout_s
    announced = False
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if not announced:
                print(f"[pipeline] waiting for lock {what} (another holder in progress)...",
                      file=sys.stderr)
                announced = True
            if time.time() > deadline:
                raise RuntimeError(f"Could not acquire lock {what} within {timeout_s}s "
                                   f"(another process holds it; if stale, remove the lock file).")
            time.sleep(0.5)


@contextlib.contextmanager
def pipeline_lock(timeout_s: float = 30.0) -> Iterator[None]:
    """GLOBAL exclusive lock on cell-1/.pipeline.lock — the COARSE boundary, held by
    `run_step` for a whole make step (which mutates broad shared cell-1 state). Per-finding /
    per-artifact work uses the finer `keyed_lock` (#25) instead, so concurrent runs on
    different findings/worktrees parallelize (G1) rather than all serialize here.

    Prevents Python-side races between FastAPI handlers and MCP tool calls. `make` recipes are
    NOT under this lock. Raises RuntimeError if not acquired within `timeout_s`.
    """
    CELL.mkdir(parents=True, exist_ok=True)
    f = open(_LOCK_PATH, "w")
    try:
        _flock_acquire(f, timeout_s, str(_LOCK_PATH))
        try:
            yield
        finally:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


@contextlib.contextmanager
def keyed_lock(key: str, timeout_s: float = 30.0) -> Iterator[None]:
    """G1 per-key shard (#25): a cross-process file lock scoped to `key` (e.g. "finding:ec-1")
    rather than the single global pipeline_lock. Same key → serialized (race-free read-modify-
    write); DIFFERENT keys → parallel, so two orchestrate runs on different findings/worktrees
    don't block each other. Reentrant per-thread (a nested same-key acquire is a no-op, so leaf
    writers compose without self-deadlock); across threads/processes the same key still
    serializes via flock."""
    held = getattr(_held_keys, "s", None)
    if held is None:
        held = _held_keys.s = set()
    if key in held:                     # this thread already holds it → reentrant no-op
        yield
        return
    _LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    f = open(_LOCKS_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".lock"), "w")
    try:
        _flock_acquire(f, timeout_s, key)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


def _keyed(key_fn):
    """Decorator: run the wrapped per-key write under keyed_lock(key_fn(*args)) — race-free
    against a concurrent writer of the SAME key, parallel across DIFFERENT keys (#25)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            with keyed_lock(key_fn(*a, **k)):
                return fn(*a, **k)
        return wrap
    return deco

# ---- step definitions ----
@dataclass
class Step:
    id: str
    title: str
    day: int
    kind: str        # "auto" | "human"
    check: Callable[[], bool]
    cmd: list[str] | None = None         # auto: subprocess to run
    prompt_path: str | None = None       # human-subagent: path to prompt file
    output_path: str | None = None       # human-subagent: where output goes
    output_dir: str | None = None        # human-multi: directory of stubs to fill
    instructions: str | None = None      # human-edit: what to write

def file_exists(p: Path) -> Callable[[], bool]:
    return lambda: p.exists()

PIPELINE: list[Step] = [
    Step("day1-recon", "Day 1: recon artifacts", 1, "auto",
         check=file_exists(CELL / "recon" / "cell-1-recon.md"),
         cmd=[str(SCRIPTS / "day1-recon.sh")]),

    Step("day1-explore", "Day 1: Explore inventory", 1, "human",
         check=file_exists(CELL / "recon" / "explore-inventory.md"),
         prompt_path=str(SCRIPTS / "explore-prompt.md"),
         output_path=str(CELL / "recon" / "explore-inventory.md"),
         instructions="Drive the Explore subagent (or `claude -p` headless). "
                      "Save its full structured-inventory response to the output path."),

    Step("day1-shortlist", "Day 1: shortlist", 1, "human",
         check=lambda: (CELL / "shortlist.txt").exists() and any(
             ln.strip() and not ln.strip().startswith("#")
             for ln in (CELL / "shortlist.txt").read_text().splitlines()
         ),
         output_path=str(CELL / "shortlist.txt"),
         instructions="Pick 3-8 hot-spot file paths (one per line). Sources: "
                      "hot-spots-coarse.txt + explore-inventory.md."),

    Step("day2-build", "Day 2: backtest candidates", 2, "auto",
         check=file_exists(CELL / "backtest" / "candidates.yaml"),
         cmd=[VENV_PY, str(SCRIPTS / "day2-build-dataset.py")]),

    Step("day2-dataset", "Day 2: dataset finalized", 2, "human",
         check=_check.dataset_finalized,
         output_path=str(CELL / "backtest" / "dataset.yaml"),
         instructions="cp dataset-autopick.yaml dataset.yaml (or hand-pick 10 from "
                      "candidates.yaml), then fill `expected_subagent` "
                      "(code-quality | edge-case) + `notes` per entry."),

    Step("day2-backtest-prep", "Day 2: backtest prepared", 2, "auto",
         check=file_exists(CELL / "backtest" / "runbook.md"),
         cmd=[VENV_PY, str(SCRIPTS / "day2-backtest.py"), "prepare"]),

    Step("day2-runs", "Day 2: backtest findings populated", 2, "human",
         check=_check.backtest_runs_populated,
         output_dir=str(CELL / "backtest" / "runs"),
         instructions="For each issue under runs/<N>/, run prompt.md via a fresh "
                      "subagent (or `claude -p`); paste findings: YAML into "
                      "findings.yaml."),

    Step("day2-labels", "Day 2: backtest labels filled", 2, "human",
         check=_check.backtest_labels_populated,
         output_dir=str(CELL / "backtest" / "runs"),
         instructions="For each runs/<N>/labels.yaml, label each finding: "
                      "matches_known | unrelated_tp | fp | dupe_of_baseline. "
                      "Set matched_rank (1-indexed) or null."),

    Step("day2-score", "Day 2: backtest scored", 2, "auto",
         check=file_exists(CELL / "cell-1-backtest.md"),
         cmd=[VENV_PY, str(SCRIPTS / "day2-backtest.py"), "score"]),

    Step("day3-hunt-prep", "Day 3: hunt prompts ready", 3, "auto",
         check=file_exists(CELL / "hunt" / "code-quality" / "prompt.md"),
         cmd=[VENV_PY, str(SCRIPTS / "day3-hunt.py"), "prepare"]),

    Step("day3-findings", "Day 3: hunt findings populated", 3, "human",
         check=_check.hunt_findings_populated,
         output_dir=str(CELL / "hunt"),
         instructions="Run each angle's prompt.md via fresh subagent; paste "
                      "findings: YAML into findings-pass1.yaml."),

    Step("day3-scaffolds", "Day 3: validation scaffolds generated", 3, "auto",
         check=file_exists(CELL / "hunt" / "validation" / ".scaffolds-generated"),
         cmd=[VENV_PY, str(SCRIPTS / "day3-hunt.py"), "validate"]),

    Step("day3-gates", "Day 3: validation gates filled", 3, "human",
         check=_check.hunt_gates_filled,
         output_dir=str(CELL / "hunt" / "validation"),
         instructions="For each scaffold: write reproducer, review dedup, assign "
                      "CWE, write fix patch + run mvn test, set final_status."),

    Step("day3-report", "Day 3: pass-1 report", 3, "auto",
         check=file_exists(CELL / "cell-1-candidates-pass1.md"),
         cmd=[VENV_PY, str(SCRIPTS / "day3-hunt.py"), "validate"]),

    Step("day4-prep", "Day 4: pass-2/3 stubs ready", 4, "auto",
         check=file_exists(CELL / "hunt" / "code-quality" / "findings-pass2.yaml"),
         cmd=[VENV_PY, str(SCRIPTS / "day4-finalize.py"), "prepare"]),

    Step("day4-passes", "Day 4: pass-2/3 findings populated", 4, "human",
         check=_check.passes23_populated,
         output_dir=str(CELL / "hunt"),
         instructions="Re-run the SAME prompt.md in 2 more fresh contexts per angle "
                      "(4 total runs). Each must be a fresh subagent — critical for "
                      "self-consistency."),

    Step("day4-report", "Day 4: final report", 4, "auto",
         check=file_exists(CELL / "cell-1-report.md"),
         cmd=[VENV_PY, str(SCRIPTS / "day4-finalize.py"), "report"]),
]


# ---- artifact whitelist (no arbitrary file reads) ----
ARTIFACTS: dict[str, Path] = {
    "recon-report":         CELL / "recon" / "cell-1-recon.md",
    "explore-inventory":    CELL / "recon" / "explore-inventory.md",
    "hot-spots":            CELL / "recon" / "hot-spots-coarse.txt",
    "deserializer-index":   CELL / "recon" / "deserializer-inventory.txt",
    "shortlist":            CELL / "shortlist.txt",
    "backtest-candidates":  CELL / "backtest" / "candidates.yaml",
    "backtest-dataset":     CELL / "backtest" / "dataset.yaml",
    "backtest-autopick":    CELL / "backtest" / "dataset-autopick.yaml",
    "backtest-runbook":     CELL / "backtest" / "runbook.md",
    "backtest-report":      CELL / "cell-1-backtest.md",
    "hunt-prompt-cq":       CELL / "hunt" / "code-quality" / "prompt.md",
    "hunt-prompt-ec":       CELL / "hunt" / "edge-case" / "prompt.md",
    "hunt-findings-cq":     CELL / "hunt" / "code-quality" / "findings-pass1.yaml",
    "hunt-findings-ec":     CELL / "hunt" / "edge-case" / "findings-pass1.yaml",
    "pass1-candidates":     CELL / "cell-1-candidates-pass1.md",
    "final-report":         CELL / "cell-1-report.md",
    # prompts (static — read from scripts/)
    "explore-prompt":       SCRIPTS / "explore-prompt.md",
    "hunt-prompts-doc":     SCRIPTS / "day3-novel-hunt-prompts.md",
}


# ---- P1-9: unified error envelope ----
# Standard response shape across HTTP (FastAPI) and MCP (stdio) surfaces:
#   success: {"ok": True, ...data}
#   error:   {"ok": False, "error": {"code": "<id>", "message": "...", "status": <http_code>}}
# Helpers below; endpoints + MCP dispatch use them.

ERROR_CODES = {
    "unknown_step":         (400, "Step id not found in PIPELINE."),
    "step_not_auto":        (400, "Step is human-driven; cannot auto-run."),
    "step_no_command":      (500, "Step has no command attached."),
    "unknown_artifact":     (404, "Artifact name not in whitelist."),
    "unwritable_target":    (400, "Artifact name is not in the writable-output whitelist."),
    "subagent_missing_input": (400, "Required input file missing for subagent run."),
    "subagent_failed":      (500, "Subagent process exited non-zero."),
    "yaml_extract_failed":  (500, "Could not extract YAML from agent output."),
    "lock_timeout":         (503, "Pipeline lock could not be acquired."),
    "internal":             (500, "Internal error."),
}


def envelope_success(**data) -> dict:
    """{ok: true, ...data} envelope (P1-9)."""
    return {"ok": True, **data}


def envelope_error(code: str, message: str | None = None) -> dict:
    """{ok: false, error: {...}} envelope (P1-9). `code` should be in ERROR_CODES;
    `message` overrides the default if supplied."""
    default_status, default_msg = ERROR_CODES.get(code, (500, "Unknown error code."))
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message or default_msg,
            "status": default_status,
        },
    }


# ---- API surface (used by server.py, mcp_server.py, and scripts/_status.sh) ----
def status_lines(use_ansi: bool = True) -> list[str]:
    """Render the pipeline-state checklist as text lines, matching _status.sh's
    historical output format. P0-5 fix: this is the single source of truth —
    _status.sh now delegates here, eliminating the prior triplication between
    Makefile, _status.sh, and pipeline.py.
    """
    state = get_state()
    out: list[str] = []
    out.append("")
    out.append("Pipeline status — Cell #1 (Jackson-databind × correctness)")
    out.append("")
    # ANSI escape codes for color (skipped when not a TTY)
    DIM = "\033[2m" if use_ansi else ""
    YELLOW = "\033[33m" if use_ansi else ""
    RESET = "\033[0m" if use_ansi else ""
    cursor_seen = False
    for s in state["steps"]:
        if s["done"]:
            marker = "[x]"
        elif not cursor_seen:
            marker = "[>]"
            cursor_seen = True
        else:
            marker = "[ ]"
        kind_tag = f"{DIM}(auto){RESET}" if s["kind"] == "auto" else f"{YELLOW}(human){RESET}"
        out.append(f"  {marker}  {s['title']}  {kind_tag}")
    out.append("")
    out.append(f"Progress: {state['progress']}/{state['total']} steps complete.")
    if state["cursor"] is None:
        out.append(f"✓ All steps complete. See cell-1/cell-1-report.md.")
    else:
        cursor_step = next((s for s in state["steps"] if s["id"] == state["cursor"]), None)
        if cursor_step:
            kind_label = "run `make` to advance" if cursor_step["kind"] == "auto" \
                         else "`make` will print the human-action instructions"
            out.append(f"Next: {cursor_step['title']} — {kind_label}.")
    out.append("")
    return out


def get_state() -> dict:
    """Snapshot of all step states + computed cursor (next [>] step)."""
    steps = []
    cursor = None
    for s in PIPELINE:
        try:
            done = bool(s.check())
        except Exception:
            done = False
        steps.append({
            "id": s.id,
            "title": s.title,
            "day": s.day,
            "kind": s.kind,
            "done": done,
            "instructions": s.instructions,
            "prompt_path": s.prompt_path,
            "output_path": s.output_path,
            "output_dir": s.output_dir,
        })
        if cursor is None and not done:
            cursor = s.id
    progress = sum(1 for s in steps if s["done"])
    return {"steps": steps, "cursor": cursor, "progress": progress, "total": len(steps)}


def run_step(step_id: str) -> dict:
    """Run an auto step (blocking). Returns {stdout, stderr, returncode, elapsed_s}.

    P1-12: holds the pipeline lock for the duration of the subprocess so
    concurrent dashboard / MCP calls serialize cleanly.
    """
    with pipeline_lock():
        return _run_step_impl(step_id)


def _run_step_impl(step_id: str) -> dict:
    step = next((s for s in PIPELINE if s.id == step_id), None)
    if step is None:
        return {"error": f"unknown step: {step_id}"}
    if step.kind != "auto":
        return {"error": f"step {step_id} is human-driven, not auto-runnable"}
    if step.cmd is None:
        return {"error": f"step {step_id} has no command"}
    # P0-11 hardening: detach child into its own session so timeout / parent
    # interrupt can kill the whole subtree. Hard cap at 1h — longest documented
    # auto step is `day1-recon.sh` (mvn package + semgrep + clone) ≈ 10min.
    import signal as _signal
    timeout_s = 3600
    t0 = time.time()
    proc = subprocess.Popen(
        step.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(PROJECT_ROOT), start_new_session=True,
    )
    stdout, stderr, timed_out = "", "", False
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                import os as _os
                _os.killpg(proc.pid, _signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                rest_out, rest_err = proc.communicate(timeout=5.0)
                stdout, stderr = (stdout or "") + (rest_out or ""), (stderr or "") + (rest_err or "")
            except subprocess.TimeoutExpired:
                try:
                    import os as _os
                    _os.killpg(proc.pid, _signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
    finally:
        if proc.poll() is None:
            try:
                import os as _os
                _os.killpg(proc.pid, _signal.SIGTERM)
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    import os as _os
                    _os.killpg(proc.pid, _signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

    returncode = proc.returncode if proc.returncode is not None else -1
    if timed_out:
        stderr = (stderr or "") + f"\nTIMEOUT after {timeout_s}s (process group killed)"
        returncode = -1
    return {
        "stdout": stdout or "",
        "stderr": stderr or "",
        "returncode": returncode,
        "elapsed_s": round(time.time() - t0, 2),
        "pid": proc.pid,
        "timed_out": timed_out,
    }


def get_artifact(name: str) -> dict | None:
    """Return {name, path, content, exists} or None if not whitelisted."""
    if name not in ARTIFACTS:
        return None
    p = ARTIFACTS[name]
    if not p.exists():
        return {"name": name, "path": str(p), "exists": False, "content": None}
    return {"name": name, "path": str(p), "exists": True, "content": p.read_text()}


def list_artifacts() -> list[dict]:
    """All whitelisted artifacts and whether they exist."""
    return [
        {"name": n, "path": str(p), "exists": p.exists(), "size": p.stat().st_size if p.exists() else 0}
        for n, p in ARTIFACTS.items()
    ]


def write_file(name: str, content: str) -> dict:
    """Write to a whitelisted output (used for shortlist, explore-inventory, etc).

    P1-12: holds pipeline lock for the write.
    """
    # Whitelist: only allow writing to step.output_path locations
    output_targets = {s.output_path for s in PIPELINE if s.output_path}
    target_path = ARTIFACTS.get(name)
    if target_path is None or str(target_path) not in output_targets:
        return {"error": f"{name} is not a writable target"}
    with pipeline_lock():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content)
    return {"name": name, "path": str(target_path), "bytes": len(content)}


# ============================================================================
# Stage 2 — claude-driver subagent runners
# ============================================================================

import yaml as _yaml
sys.path.insert(0, str(Path(__file__).parent))
import claude_driver as _cd  # noqa: E402


def _read_text(p: Path) -> str:
    return p.read_text() if p.exists() else ""


def list_backtest_entries() -> list[dict]:
    """Per-issue backtest state. Used by dashboard's day2-runs detail panel."""
    runs_dir = CELL / "backtest" / "runs"
    if not runs_dir.is_dir():
        return []
    out = []
    for d in sorted(runs_dir.iterdir(), key=lambda x: int(x.name) if x.name.isdigit() else 0):
        if not d.is_dir():
            continue
        prompt_p, findings_p, labels_p = d / "prompt.md", d / "findings.yaml", d / "labels.yaml"
        if not prompt_p.exists():
            continue
        findings_txt = _read_text(findings_p)
        # P1-4 fix: prefer the explicit sentinel; fall back to legacy markers for
        # backward compat with files written before the P1-4 change.
        is_stub_f = ("AGENT-OUTPUT-NOT-YET-PASTED" in findings_txt
                     or "Paste the agent" in findings_txt
                     or "Paste the YAML" in findings_txt)
        findings_doc = _yaml.safe_load(findings_txt) if findings_txt else {}
        findings_list = (findings_doc or {}).get("findings") or []
        labels_doc = (_yaml.safe_load(_read_text(labels_p)) or {}) if labels_p.exists() else {}
        # P1-5 fix: align labels_populated logic with scripts/_check.py::backtest_labels_populated.
        # Populated iff:
        #   (a) there are explicit label entries, OR
        #   (b) the entry has zero findings AND matched_rank is explicitly null
        #       (agent legitimately reported nothing; no labels needed).
        has_explicit_labels = bool(labels_doc.get("labels"))
        zero_findings_explicit = (
            bool(labels_doc)                                  # file exists with content
            and not findings_list                             # agent returned no findings
            and labels_doc.get("matched_rank", "MISSING") is None  # explicit null, not missing
        )
        out.append({
            "issue": d.name,
            "findings_populated": findings_p.exists() and not is_stub_f,
            "labels_populated": has_explicit_labels or zero_findings_explicit,
            "labels_set": has_explicit_labels,
            "matched_rank": labels_doc.get("matched_rank"),
        })
    return out


@_keyed(lambda issue_num, *a, **k: f"backtest:{issue_num}")     # #25 per-issue shard
def _write_backtest_result(issue_num: str, result: dict) -> dict:
    """Post-process one backtest claude result: write raw output, extract +
    validate the findings YAML, write findings.yaml. Shared by the single-entry
    runner and the parallel batch runner so both behave identically."""
    if not _safe_id(issue_num):
        return {"issue": issue_num, "ok": False, "error": "invalid issue id"}
    d = CELL / "backtest" / "runs" / str(issue_num)
    raw_path = d / "claude-raw-output.md"
    raw_path.write_text(result.get("stdout", ""))
    if result.get("returncode") != 0:
        return {"issue": issue_num, "ok": False, "error": (result.get("stderr") or "")[:500]}
    yaml_text = _cd.extract_yaml_block(result.get("stdout", ""), "findings")
    if yaml_text is None:
        return {"issue": issue_num, "ok": False, "error": "no findings YAML block found", "raw_path": str(raw_path)}
    try:
        _yaml.safe_load(yaml_text)
    except _yaml.YAMLError as e:
        return {"issue": issue_num, "ok": False, "error": f"YAML parse error: {e}", "raw_path": str(raw_path)}
    (d / "findings.yaml").write_text(f"# Auto-generated via claude_driver.\n# Raw response: claude-raw-output.md\n\n{yaml_text}\n")
    return {"issue": issue_num, "ok": True, "elapsed_s": result.get("elapsed_s"), "yaml_bytes": len(yaml_text)}


def run_backtest_subagent(issue_num: str) -> dict:
    """Run claude -p for one backtest entry; save findings.yaml."""
    d = CELL / "backtest" / "runs" / issue_num
    if not (d / "prompt.md").exists():
        return {"error": f"no prompt at {d}/prompt.md"}
    result = _cd.run_claude_with_retry((d / "prompt.md").read_text())
    return _write_backtest_result(issue_num, result)


def list_backtest_issue_dirs() -> list[str]:
    """Prepared backtest entries (those with a prompt.md), sorted."""
    runs = CELL / "backtest" / "runs"
    if not runs.is_dir():
        return []
    return sorted(p.name for p in runs.iterdir() if p.is_dir() and (p / "prompt.md").exists())


def run_backtest_batch(issue_nums: list[str] | None = None, max_parallel: int = 4) -> dict:
    """Run several backtest subagents CONCURRENTLY via claude_driver.run_claude_batch.

    issue_nums=None runs every prepared entry. The expensive `claude -p` dispatch
    is fanned out (bounded by max_parallel, each with retry); the file-write phase
    then runs serially UNDER pipeline_lock so it can't interleave with a
    concurrent `make`/dashboard write. Returns per-issue results plus a summary."""
    if issue_nums is None:
        issue_nums = list_backtest_issue_dirs()
    if not issue_nums:
        return {"ok": False, "error": "no backtest entries with prompt.md found"}

    jobs, results = [], []
    for n in issue_nums:
        p = CELL / "backtest" / "runs" / str(n) / "prompt.md"
        if not p.exists():
            results.append({"issue": str(n), "ok": False, "error": "no prompt.md"})
            continue
        jobs.append({"key": str(n), "prompt": p.read_text()})

    raw = _cd.run_claude_batch(jobs, max_parallel=max_parallel)
    with pipeline_lock():
        for r in raw:
            results.append(_write_backtest_result(r["key"], r))

    results.sort(key=lambda r: str(r.get("issue")))
    succeeded = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "max_parallel": max_parallel,
            "results": results}


def _load_dataset_entry(issue_num: str) -> dict | None:
    ds_path = CELL / "backtest" / "dataset.yaml"
    if not ds_path.exists():
        return None
    data = _yaml.safe_load(ds_path.read_text()) or {}
    for e in (data.get("dataset") or []):
        if str(e.get("issue")) == str(issue_num):
            return e
    return None


def _git_show_diff(sha: str, max_lines: int = 800) -> str:
    target_dir = PROJECT_ROOT / "targets" / "jackson-databind"
    if not target_dir.is_dir():
        return ""
    r = subprocess.run(
        ["git", "-C", str(target_dir), "show", "--stat", "-U3", sha,
         "--", "src/main/java/"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ""
    lines = r.stdout.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines truncated)"]
    return "\n".join(lines)


def label_backtest_subagent(issue_num: str) -> dict:
    """Run claude -p with a label prompt; update labels.yaml.

    ⚠️ ADVISORY ONLY — NO LONGER A GATE INPUT (P0-1 fix, 2026-05-19).

    Per the project's #1 architectural rule ("validators are non-AI", see
    `oss-bug-hunter-research.md` §5.4), this LLM-judges-LLM circularity is
    methodologically broken. As of the P0-1 fix in `scripts/day2-backtest.py`,
    the gate decision uses DETERMINISTIC file-coverage scoring computed from
    `files_touched` — see `_finding_file()` in that file. Labels written here
    are stored in `labels.yaml` with `_method: "llm-auto"` and rendered in the
    backtest report as an informational column, but the PROCEED/KILL decision
    does NOT depend on them.

    Keep this for richer triage of individual findings during human review.
    Never reintroduce as a gate input.
    """
    entry = _load_dataset_entry(issue_num)
    if entry is None:
        return {"issue": issue_num, "ok": False, "error": "issue not in dataset.yaml"}
    d = CELL / "backtest" / "runs" / issue_num
    findings_txt = _read_text(d / "findings.yaml")
    # P1-4: explicit sentinel + legacy fallback
    is_stub = (not findings_txt
               or "AGENT-OUTPUT-NOT-YET-PASTED" in findings_txt
               or "Paste the agent" in findings_txt)
    if is_stub:
        return {"issue": issue_num, "ok": False, "error": "findings not populated yet"}

    fix_diff = _git_show_diff(entry.get("fix_commit", ""))
    prompt = f"""You are scoring an agent's correctness-bug findings against a known historical bug.

# Known historical bug
Issue: #{entry.get('issue')}
Title: {entry.get('title', '')}
Fix commit: {entry.get('fix_commit', '')}
Files touched: {entry.get('files_touched', [])}

Fix diff (java sources only, truncated):
```diff
{fix_diff[:8000]}
```

# Agent's findings
```yaml
{findings_txt[:6000]}
```

# Baseline scanner findings
None (scanners not installed in this run; dupe_of_baseline never applies).

# Task

For EACH finding (0-indexed) in the agent output, assign exactly ONE label:
- matches_known: describes the same bug that the fix above addresses
- unrelated_tp: describes a different bug that is still real (would be a valid finding)
- fp: describes something that is NOT a bug (false positive)
- dupe_of_baseline: duplicates a baseline scanner finding — N/A here, never use

`matched_rank` = 1-indexed position of FIRST `matches_known` finding, or null if none.

Output ONLY this YAML at the end (no other text after the closing fence):
```yaml
matched_rank: <int or null>
labels:
  - index: 0
    label: matches_known | unrelated_tp | fp
    note: "one-line justification"
```
"""
    result = _cd.run_claude_with_retry(prompt)
    raw_path = d / "claude-label-raw.md"
    raw_path.write_text(result["stdout"])
    if result["returncode"] != 0:
        return {"issue": issue_num, "ok": False, "error": (result["stderr"] or "")[:500]}
    yaml_text = _cd.extract_yaml_block(result["stdout"], "matched_rank") \
                or _cd.extract_yaml_block(result["stdout"], "labels")
    if yaml_text is None:
        return {"issue": issue_num, "ok": False, "error": "no labels YAML extracted", "raw_path": str(raw_path)}
    try:
        parsed = _yaml.safe_load(yaml_text)
    except _yaml.YAMLError as e:
        return {"issue": issue_num, "ok": False, "error": f"YAML parse: {e}", "raw_path": str(raw_path)}

    # Preserve the original labels.yaml header context
    labels_p = d / "labels.yaml"
    existing = _yaml.safe_load(_read_text(labels_p)) or {}
    existing["matched_rank"] = parsed.get("matched_rank")
    existing["labels"] = parsed.get("labels", [])
    existing["_auto_labeled"] = True
    existing["_method"] = "llm-auto"
    existing["_auto_label_note"] = (
        "Labels generated by claude_driver. ADVISORY ONLY — gate decision uses "
        "deterministic file-coverage scoring (P0-1 fix). Human review recommended."
    )
    labels_p.write_text(_yaml.safe_dump(existing, sort_keys=False, default_flow_style=False, width=120))
    return {"issue": issue_num, "ok": True, "elapsed_s": result["elapsed_s"],
            "matched_rank": parsed.get("matched_rank"), "n_labels": len(parsed.get("labels", []))}


def run_explore_subagent() -> dict:
    """Run claude -p for the Explore inventory; save to cell-1/recon/explore-inventory.md."""
    prompt_path = SCRIPTS / "explore-prompt.md"
    # Extract the prompt body (everything after the "## Prompt body" header)
    full = prompt_path.read_text()
    m = re.search(r'## Prompt body[^\n]*\n+(.*)', full, re.DOTALL)
    body = m.group(1) if m else full
    result = _cd.run_claude_with_retry(body)
    if result["returncode"] != 0:
        return {"ok": False, "error": (result["stderr"] or "")[:500]}
    (CELL / "recon" / "explore-inventory.md").write_text(result["stdout"])
    return {"ok": True, "elapsed_s": result["elapsed_s"], "bytes": len(result["stdout"])}


# The four fresh re-runs Day 4 fans out for 2-of-3 self-consistency.
DAY4_PASSES = [("code-quality", 2), ("code-quality", 3),
               ("edge-case", 2), ("edge-case", 3)]


@_keyed(lambda angle, pass_num, *a, **k: f"hunt:{angle}:{pass_num}")   # #25 per-(angle,pass) shard
def _write_hunt_result(angle: str, pass_num: int, result: dict) -> dict:
    """Post-process one hunt claude result into findings-pass<N>.yaml. Shared by
    the single and batch runners."""
    raw_p = CELL / "hunt" / angle / f"claude-raw-pass{pass_num}.md"
    raw_p.write_text(result.get("stdout", ""))
    if result.get("returncode") != 0:
        return {"ok": False, "angle": angle, "pass": pass_num, "error": (result.get("stderr") or "")[:500]}
    yaml_text = _cd.extract_yaml_block(result.get("stdout", ""), "findings")
    if yaml_text is None:
        return {"ok": False, "angle": angle, "pass": pass_num, "error": "no findings YAML extracted", "raw_path": str(raw_p)}
    out_p = CELL / "hunt" / angle / f"findings-pass{pass_num}.yaml"
    out_p.write_text(f"# Auto-generated via claude_driver (pass {pass_num}).\n# Raw response: {raw_p.name}\n\n{yaml_text}\n")
    return {"ok": True, "angle": angle, "pass": pass_num, "elapsed_s": result.get("elapsed_s")}


def run_hunt_subagent(angle: str, pass_num: int) -> dict:
    """Run claude -p for a hunt prompt; save to findings-pass<N>.yaml."""
    if angle not in ("code-quality", "edge-case"):
        return {"ok": False, "error": f"invalid angle: {angle}"}
    if pass_num not in (1, 2, 3):
        return {"ok": False, "error": f"invalid pass_num: {pass_num}"}
    prompt_p = CELL / "hunt" / angle / "prompt.md"
    if not prompt_p.exists():
        return {"ok": False, "error": f"no prompt at {prompt_p}"}
    result = _cd.run_claude_with_retry(prompt_p.read_text())
    return _write_hunt_result(angle, pass_num, result)


def run_hunt_batch(passes: list | None = None, max_parallel: int = 4) -> dict:
    """Run several hunt passes CONCURRENTLY via claude_driver.run_claude_batch.

    `passes` is a list of (angle, pass_num) pairs; None = the four Day-4
    self-consistency passes (DAY4_PASSES). Each pass is a FRESH `claude -p`
    process, which is exactly the fresh-context property self-consistency needs.
    Returns per-pass results + a summary."""
    if passes is None:
        passes = DAY4_PASSES
    jobs, results = [], []
    for spec in passes:
        angle, pass_num = spec[0], spec[1]
        if angle not in ("code-quality", "edge-case") or pass_num not in (1, 2, 3):
            results.append({"ok": False, "angle": angle, "pass": pass_num, "error": "invalid angle/pass"})
            continue
        prompt_p = CELL / "hunt" / angle / "prompt.md"
        if not prompt_p.exists():
            results.append({"ok": False, "angle": angle, "pass": pass_num, "error": f"no prompt at {prompt_p}"})
            continue
        jobs.append({"key": (angle, pass_num), "prompt": prompt_p.read_text()})

    raw = _cd.run_claude_batch(jobs, max_parallel=max_parallel)
    with pipeline_lock():
        for r in raw:
            angle, pass_num = r["key"]
            results.append(_write_hunt_result(angle, pass_num, r))

    results.sort(key=lambda x: (str(x.get("angle")), x.get("pass") or 0))
    succeeded = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "max_parallel": max_parallel,
            "results": results}


_day3_hunt = None


def _load_day3_hunt():
    """Lazy-load scripts/day3-hunt.py (hyphenated → importlib) for its
    reproducer-builder helpers. Cached."""
    global _day3_hunt
    if _day3_hunt is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("day3_hunt", SCRIPTS / "day3-hunt.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _day3_hunt = mod
    return _day3_hunt


@_keyed(lambda finding_id, *a, **k: f"finding:{finding_id}")   # #25 per-finding shard
def _write_repro_result(finding_id: str, result: dict) -> dict:
    """Post-process one reproducer-builder claude result: write raw output,
    extract the ```java block, write cell-1/hunt/repros/<id>.java. Shared by the
    single and batch runners."""
    if not _safe_id(finding_id):
        return {"ok": False, "finding_id": finding_id, "error": "invalid finding_id"}
    d3 = _load_day3_hunt()
    repros_dir = CELL / "hunt" / "repros"
    repros_dir.mkdir(parents=True, exist_ok=True)
    raw_p = repros_dir / f"{finding_id}.claude-raw.md"
    raw_p.write_text(result.get("stdout", ""))
    if result.get("returncode") != 0:
        return {"ok": False, "finding_id": finding_id, "error": (result.get("stderr") or "")[:500], "raw_path": str(raw_p)}
    java = d3.extract_java_block(result.get("stdout", ""))
    if java is None:
        return {"ok": False, "finding_id": finding_id, "error": "no java block extracted", "raw_path": str(raw_p)}
    out_p = repros_dir / f"{finding_id}.java"
    out_p.write_text(java + "\n")
    res = {"ok": True, "finding_id": finding_id, "path": str(out_p), "elapsed_s": result.get("elapsed_s")}
    expected_cls = d3.repro_class_name(finding_id)
    if expected_cls not in java:
        res["warning"] = f"produced .java does not declare expected class {expected_cls}; run-repros may not find it"
    return res


def _quality_kwargs(model: str | None, effort: str | None, timeout_s: int | None) -> dict:
    """Build the model/effort/timeout overrides for run_claude, dropping Nones so
    run_claude's own defaults (haiku/low/900s) apply when nothing is requested."""
    return {k: v for k, v in (("model", model), ("effort", effort), ("timeout_s", timeout_s))
            if v is not None}


def run_repro_subagent(finding_id: str, *, model: str | None = None,
                       effort: str | None = None, timeout_s: int | None = None) -> dict:
    """Headless reproducer-builder (WS3): build the prompt for a finding,
    dispatch `claude -p` (with retry), extract the ```java block, and write
    cell-1/hunt/repros/<id>.java. Does NOT run the test — that's the non-AI
    validator (`day3-hunt.py run-repros`). model/effort/timeout_s override the
    haiku/low defaults (the orchestrator passes opus/high)."""
    import yaml as _yaml
    if not _safe_id(finding_id):
        return {"ok": False, "finding_id": finding_id, "error": "invalid finding_id"}
    d3 = _load_day3_hunt()
    scaffold_p = CELL / "hunt" / "validation" / f"{finding_id}.yaml"
    if not scaffold_p.exists():
        return {"ok": False, "error": f"no scaffold at {scaffold_p}"}
    scaffold, err = _safe_load_scaffold(scaffold_p)
    if err:
        return {"ok": False, "finding_id": finding_id, "error": err}
    result = _cd.run_claude_with_retry(d3.build_repro_prompt(scaffold),
                                       **_quality_kwargs(model, effort, timeout_s))
    return _write_repro_result(finding_id, result)


def list_repro_finding_ids() -> list[str]:
    """Finding ids (from validation scaffolds) that still lack a repros/<id>.java."""
    vdir = CELL / "hunt" / "validation"
    repros = CELL / "hunt" / "repros"
    if not vdir.is_dir():
        return []
    return sorted(p.stem for p in vdir.glob("*.yaml")
                  if not (repros / f"{p.stem}.java").exists())


def run_repro_batch(finding_ids: list[str] | None = None, max_parallel: int = 4) -> dict:
    """Build reproducers for several findings CONCURRENTLY via run_claude_batch.

    finding_ids=None = every validation scaffold still missing a .java. Writes
    one cell-1/hunt/repros/<id>.java per success. Does NOT run the tests (that's
    the non-AI `day3-hunt.py run-repros`). Returns per-finding results + summary."""
    import yaml as _yaml
    d3 = _load_day3_hunt()
    if finding_ids is None:
        finding_ids = list_repro_finding_ids()
    if not finding_ids:
        return {"ok": False, "error": "no validation scaffolds needing a reproducer"}

    jobs, results = [], []
    for fid in finding_ids:
        scaffold_p = CELL / "hunt" / "validation" / f"{fid}.yaml"
        if not scaffold_p.exists():
            results.append({"ok": False, "finding_id": fid, "error": f"no scaffold at {scaffold_p}"})
            continue
        scaffold, err = _safe_load_scaffold(scaffold_p)
        if err:
            results.append({"ok": False, "finding_id": fid, "error": err})
            continue
        jobs.append({"key": fid, "prompt": d3.build_repro_prompt(scaffold)})

    raw = _cd.run_claude_batch(jobs, max_parallel=max_parallel)
    with pipeline_lock():
        for r in raw:
            results.append(_write_repro_result(r["key"], r))

    results.sort(key=lambda x: str(x.get("finding_id")))
    succeeded = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "max_parallel": max_parallel,
            "results": results}


# ---- fix-builder (#4) -------------------------------------------------------
@_keyed(lambda finding_id, *a, **k: f"finding:{finding_id}")   # #25 per-finding shard
def _write_fix_result(finding_id: str, result: dict) -> dict:
    """Post-process one fix-builder claude result: write raw output, extract the
    ```diff block, write cell-1/hunt/patches/<id>.patch. Shared single+batch."""
    if not _safe_id(finding_id):
        return {"ok": False, "finding_id": finding_id, "error": "invalid finding_id"}
    d3 = _load_day3_hunt()
    patches_dir = CELL / "hunt" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    raw_p = patches_dir / f"{finding_id}.claude-raw.md"
    raw_p.write_text(result.get("stdout", ""))
    if result.get("returncode") != 0:
        return {"ok": False, "finding_id": finding_id, "error": (result.get("stderr") or "")[:500], "raw_path": str(raw_p)}
    diff = d3.extract_diff_block(result.get("stdout", ""))
    if diff is None:
        return {"ok": False, "finding_id": finding_id, "error": "no diff block extracted", "raw_path": str(raw_p)}
    out_p = patches_dir / f"{finding_id}.patch"
    out_p.write_text(diff + "\n")
    return {"ok": True, "finding_id": finding_id, "path": str(out_p), "elapsed_s": result.get("elapsed_s")}


def _build_fix_prompt_for(finding_id: str, feedback: str | None = None):
    """(prompt, error) for a finding: loads its scaffold + reproducer source.
    Returns (None, errdict) if the scaffold is missing. `feedback` (a prior
    attempt's failure) is threaded into the prompt for self-correction."""
    import yaml as _yaml
    if not _safe_id(finding_id):
        return None, {"ok": False, "finding_id": finding_id, "error": "invalid finding_id"}
    d3 = _load_day3_hunt()
    scaffold_p = CELL / "hunt" / "validation" / f"{finding_id}.yaml"
    if not scaffold_p.exists():
        return None, {"ok": False, "finding_id": finding_id, "error": f"no scaffold at {scaffold_p}"}
    scaffold, err = _safe_load_scaffold(scaffold_p)
    if err:
        return None, {"ok": False, "finding_id": finding_id, "error": err}
    java_p = CELL / "hunt" / "repros" / f"{finding_id}.java"
    repro_src = java_p.read_text() if java_p.exists() else None
    return d3.build_fix_prompt(scaffold, repro_src, feedback=feedback), None


def run_fix_subagent(finding_id: str, feedback: str | None = None, *,
                     model: str | None = None, effort: str | None = None,
                     timeout_s: int | None = None) -> dict:
    """Headless fix-builder (#4): build the fix prompt for a finding (embedding
    its reproducer, plus any `feedback` from a failed attempt), dispatch
    `claude -p`, extract the ```diff block, write cell-1/hunt/patches/<id>.patch.
    Does NOT apply/run it — that's the non-AI validator (`day3-hunt.py run-fixes`).
    model/effort/timeout_s override the haiku/low defaults (orchestrator → opus/high)."""
    prompt, err = _build_fix_prompt_for(finding_id, feedback=feedback)
    if err:
        return err
    result = _cd.run_claude_with_retry(prompt, **_quality_kwargs(model, effort, timeout_s))
    return _write_fix_result(finding_id, result)


def list_fix_finding_ids() -> list[str]:
    """Finding ids with a reproducer .java (needed to validate a fix) but no
    patches/<id>.patch yet."""
    repros = CELL / "hunt" / "repros"
    patches = CELL / "hunt" / "patches"
    if not repros.is_dir():
        return []
    return sorted(p.stem for p in repros.glob("*.java")
                  if not (patches / f"{p.stem}.patch").exists())


def run_fix_batch(finding_ids: list[str] | None = None, max_parallel: int = 4) -> dict:
    """Build fix patches for several findings CONCURRENTLY via run_claude_batch.

    finding_ids=None = every finding with a reproducer .java but no patch yet.
    Writes one cell-1/hunt/patches/<id>.patch per success. Does NOT apply/run
    them (that's the non-AI `day3-hunt.py run-fixes`)."""
    if finding_ids is None:
        finding_ids = list_fix_finding_ids()
    if not finding_ids:
        return {"ok": False, "error": "no findings with a reproducer awaiting a fix patch"}

    jobs, results = [], []
    for fid in finding_ids:
        prompt, err = _build_fix_prompt_for(fid)
        if err:
            results.append(err)
            continue
        jobs.append({"key": fid, "prompt": prompt})

    raw = _cd.run_claude_batch(jobs, max_parallel=max_parallel)
    with pipeline_lock():
        for r in raw:
            results.append(_write_fix_result(r["key"], r))

    results.sort(key=lambda x: str(x.get("finding_id")))
    succeeded = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "max_parallel": max_parallel,
            "results": results}


def suggest_gates() -> dict:
    """Deterministic (non-AI) advisory auto-fill of blank dedup/cwe gates. Fills
    only blanks; never sets is_duplicate or final_status. Returns {ok, updated,
    results}."""
    return _load_day3_hunt().apply_gate_suggestions()


# ---- self-correcting orchestrator -----------------------------------------
# The orchestrator runs its BUILDERS at opus/high (quality matters most when a
# fix has to actually flip a test green and survive retries), with a longer
# timeout since opus/high is slower. The standalone/batch runners keep the
# haiku/low defaults for throughput. Override via the model/effort params.
ORCHESTRATOR_MODEL = "opus"
ORCHESTRATOR_EFFORT = "high"
ORCHESTRATOR_TIMEOUT_S = 1800

# R5: only these outcomes mean the non-AI validator actually reached a verdict.
# Everything else (Docker down, nothing built) is INCONCLUSIVE and must not look
# like success to a caller that only checks `ok`.
# A conclusive (non-AI-decided) verdict — distinct from an environment/build
# failure. `fix-failed-after-retries` IS conclusive (reproduced + the patch didn't
# flip it green), so it counts as "validated" (a real result), unlike
# inconclusive / no-reproducer-built / no-fix-built.
_CONCLUSIVE_OUTCOMES = {"fixed", "does-not-reproduce", "fix-failed-after-retries"}

_REPRO_EXT_ORCH = {"java": ".java", "python": ".py", "go": ".go",
                   "rust": ".rs", "javascript": ".js"}


@_keyed(lambda scaffold_path, *a, **k: f"finding:{Path(scaffold_path).stem}")   # #25: race-free RMW
def _set_gate(scaffold_path, gate: str, status: str, notes: str) -> None:
    """Update one gate's status/notes in a finding scaffold, preserving the rest. Per-finding
    keyed_lock (#25) makes the read-modify-write race-free against a concurrent gate write."""
    import yaml as _yaml
    try:
        s = _yaml.safe_load(Path(scaffold_path).read_text()) or {}
    except Exception:
        return
    g = s.setdefault("gates", {})
    cur = g.get(gate) if isinstance(g.get(gate), dict) else {}
    cur.update({"status": status, "notes": notes})
    g[gate] = cur
    Path(scaffold_path).write_text(_yaml.safe_dump(s, sort_keys=False))


def verify_finding(finding_id: str, *, build: bool = True, worktree: str | None = None,
                   network: str | None = None, model: str = ORCHESTRATOR_MODEL,
                   effort: str = ORCHESTRATOR_EFFORT, timeout_s: int = ORCHESTRATOR_TIMEOUT_S,
                   log=None) -> dict:
    """Anthropic-style VERIFY stage (task #54): ensure a reproducer (build it if
    missing — Java via run_repro_subagent, others via llm_repro_provider), run it
    through run_harness.validate_repro, write the reproducer gate, and return
    reproduced | does-not-reproduce | no-reproducer-built | inconclusive. The AI only
    PROPOSES the reproducer; the non-AI validator DECIDES. This moves an ingested
    'proposed' finding to 'reproduced' WITHOUT needing a fix yet (the fix is #55)."""
    import yaml as _yaml
    import run_harness as _rh
    steps: list[dict] = []
    _log = log or (lambda *a: None)
    scaffold_p = CELL / "hunt" / "validation" / f"{finding_id}.yaml"
    if not scaffold_p.exists():
        return {"finding_id": finding_id, "outcome": "no-reproducer-built",
                "note": "no validation scaffold", "steps": steps}
    scaffold = _yaml.safe_load(scaffold_p.read_text()) or {}
    lang = scaffold.get("language", "java")
    target = scaffold.get("target", "jackson-databind")
    wt = worktree or f"targets/{target}"
    net = network or ("bridge" if lang == "java" else "none")
    trusted = True
    try:
        import targets as _tg
        tinfo = _tg.get_target(target)
        if tinfo is not None:
            trusted = bool(tinfo.get("trusted", False))
    except Exception:
        pass
    repro_p = CELL / "hunt" / "repros" / f"{finding_id}{_REPRO_EXT_ORCH.get(lang, '.java')}"

    # 1. ensure a reproducer (build if missing + allowed)
    if not repro_p.exists() and build:
        if lang == "java":
            steps.append({"step": "build-reproducer",
                          **run_repro_subagent(finding_id, model=model, effort=effort, timeout_s=timeout_s)})
        else:
            import llm_repro_provider as _lrp
            built = _lrp.build_repro(scaffold, str(repro_p), model=model, effort=effort,
                                     timeout_s=timeout_s, log=_log)
            steps.append({"step": "build-reproducer", "ok": bool(built), "path": built})
    if not repro_p.exists():
        _set_gate(scaffold_p, "reproducer", "not-attempted",
                  "no reproducer (builder produced nothing or build disabled)")
        return {"finding_id": finding_id, "outcome": "no-reproducer-built", "lang": lang, "steps": steps}

    # 2. VERIFY via the non-AI validator: FAILED on HEAD = the bug reproduces.
    fqcn = (f"com.fasterxml.jackson.databind.repro.Repro_{finding_id.replace('-', '_')}"
            if lang == "java" else finding_id)
    v = _rh.validate_repro(wt, fqcn, str(repro_p), trusted=trusted, network=net, lang=lang, log=_log)
    gate, outcome = {"FAILED": ("pass", "reproduced"),
                     "PASSED": ("fail", "does-not-reproduce")}.get(
        v.outcome.name, ("not-attempted", "inconclusive"))
    _set_gate(scaffold_p, "reproducer", gate, f"verify: {outcome} ({v.raw_summary})")
    steps.append({"step": "verify", "outcome": outcome, "verdict": v.raw_summary})
    return {"finding_id": finding_id, "outcome": outcome, "lang": lang,
            "verdict": v.raw_summary, "reproducer": str(repro_p), "steps": steps}


def orchestrate_finding(finding_id: str, max_fix_attempts: int = 2,
                        worktree: str | None = None, network: str | None = None,
                        *, model: str = ORCHESTRATOR_MODEL, effort: str = ORCHESTRATOR_EFFORT,
                        timeout_s: int = ORCHESTRATOR_TIMEOUT_S, log=None) -> dict:
    """Public wrapper: run the loop, then tag whether validation actually
    concluded (`validated`) so callers can tell a real verdict from an
    environment failure (R5). `log` (optional) streams engine lines live (the SSE
    job passes it so the UI's Orchestrate uses THIS converged engine)."""
    r = _orchestrate_finding(finding_id, max_fix_attempts, worktree, network,
                             model=model, effort=effort, timeout_s=timeout_s, log=log)
    r["validated"] = r.get("outcome") in _CONCLUSIVE_OUTCOMES
    return r


def _orchestrate_finding(finding_id: str, max_fix_attempts: int = 2,
                         worktree: str | None = None, network: str | None = None,
                         *, model: str = ORCHESTRATOR_MODEL, effort: str = ORCHESTRATOR_EFFORT,
                         timeout_s: int = ORCHESTRATOR_TIMEOUT_S, log=None) -> dict:
    """Run the build→validate→retry loop for ONE finding via the SINGLE
    multi-language engine `run_harness.orchestrate` (SWE-agent / OpenHands shape,
    on this project's primitives + non-AI gates):

        resolve language/target from the scaffold
          → ensure a reproducer (build via LLM if missing — Java: run_repro_subagent,
            others: llm_repro_provider #54)
          → ensure an initial fix patch (Java: run_fix_subagent, others: llm_fix_builder #55)
          → run_harness.orchestrate: reproduce → fix → retry-with-feedback
            (LLM fix-builder is the retry provider, language-aware; non-AI validators decide)

    Builders run at `model`/`effort` (default opus/high). Returns
    {finding_id, outcome, attempts, model, lang, target, detail, steps:[...]}.
    outcome ∈ fixed | does-not-reproduce | fix-failed-after-retries | inconclusive
    | no-reproducer-built | no-fix-built. The AI only proposes (.java/.patch);
    run_harness's sandboxed validators dispose and restore the worktree."""
    # CONVERGED (2026-06-08): the reproduce→fix→retry loop is the SINGLE
    # multi-language engine `run_harness.orchestrate` (not the old Java-only
    # run-repro.sh / run-fix.sh path). The AI only proposes (reproducer + .patch);
    # all five languages now have LLM repro + fix builders (#54/#55) — non-AI
    # validators dispose.
    import yaml as _yaml
    import run_harness as _rh
    q = {"model": model, "effort": effort, "timeout_s": timeout_s}
    steps: list[dict] = []

    scaffold_p = CELL / "hunt" / "validation" / f"{finding_id}.yaml"
    if not scaffold_p.exists():
        return {"finding_id": finding_id, "outcome": "no-reproducer-built",
                "model": model, "note": "no validation scaffold", "steps": steps}
    scaffold = _yaml.safe_load(scaffold_p.read_text()) or {}
    lang = scaffold.get("language", "java")
    target = scaffold.get("target", "jackson-databind")
    ext = {"java": ".java", "python": ".py", "go": ".go",
           "rust": ".rs", "javascript": ".js"}.get(lang, ".java")
    wt = worktree or f"targets/{target}"
    net = network or ("bridge" if lang == "java" else "none")
    trusted = True
    try:
        import targets as _tg
        tinfo = _tg.get_target(target)
        if tinfo is not None:
            trusted = bool(tinfo.get("trusted", False))
    except Exception:
        pass

    repro_p = CELL / "hunt" / "repros" / f"{finding_id}{ext}"
    patch_p = CELL / "hunt" / "patches" / f"{finding_id}.patch"

    # 1. Ensure a reproducer (Java: build via LLM if absent; others must pre-exist).
    if not repro_p.exists():
        if lang == "java":
            rr = run_repro_subagent(finding_id, **q)
            steps.append({"step": "build-reproducer", **rr})
            if not rr.get("ok") or not repro_p.exists():
                return {"finding_id": finding_id, "outcome": "no-reproducer-built", "model": model, "steps": steps}
        else:
            # non-Java: LLM reproducer-builder (task #54). AI proposes; engine disposes.
            import llm_repro_provider as _lrp
            built = _lrp.build_repro(scaffold, str(repro_p), model=model, effort=effort,
                                     timeout_s=timeout_s, log=(log or print))
            steps.append({"step": "build-reproducer", "ok": bool(built), "path": built})
            if not repro_p.exists():
                return {"finding_id": finding_id, "outcome": "no-reproducer-built", "model": model,
                        "note": f"{lang} reproducer-builder produced nothing", "steps": steps}

    # 2. Ensure an initial fix patch (Java: build via LLM; others must pre-exist).
    if not patch_p.exists():
        if lang == "java":
            rf = run_fix_subagent(finding_id, feedback=None, **q)
            steps.append({"step": "build-fix#1", **rf})
            if not rf.get("ok") or not patch_p.exists():
                return {"finding_id": finding_id, "outcome": "no-fix-built", "attempts": 1, "model": model, "steps": steps}
        else:
            # non-Java: LLM fix-builder (task #55). AI proposes a patch; engine disposes.
            import llm_fix_builder as _lfb
            built = _lfb.build_fix(scaffold, repro_p.read_text(), str(patch_p),
                                   model=model, effort=effort, timeout_s=timeout_s, log=(log or print))
            steps.append({"step": "build-fix#1", "ok": bool(built), "path": built})
            if not patch_p.exists():
                return {"finding_id": finding_id, "outcome": "no-fix-built", "attempts": 1, "model": model,
                        "note": f"{lang} fix-builder produced nothing", "steps": steps}

    # 3. Retry-with-feedback provider — the LLM fix-builder (language-aware).
    provider = None
    if max_fix_attempts > 0:
        try:
            if lang == "java":
                import llm_fix_provider as _lfp
                provider = _lfp.make_llm_fix_provider(scaffold, repro_p.read_text(),
                                                      str(patch_p.parent), model=model,
                                                      effort=effort, timeout_s=timeout_s)
            else:
                import llm_fix_builder as _lfb
                provider = _lfb.make_provider(scaffold, repro_p.read_text(),
                                              str(patch_p.parent), model=model,
                                              effort=effort, timeout_s=timeout_s)
        except Exception:
            provider = None

    fqcn = (f"com.fasterxml.jackson.databind.repro.Repro_{finding_id.replace('-', '_')}"
            if lang == "java" else finding_id)

    # 4. ONE engine: reproduce → fix → retry, language-aware, non-AI deciders.
    def _tee(line):
        steps.append({"log": line})
        if log:
            log(line)
    res = _rh.orchestrate(wt, fqcn, str(repro_p), str(patch_p), trusted=trusted,
                          network=net, lang=lang, fix_provider=provider,
                          max_retries=max_fix_attempts, log=_tee)
    outcome = {"validated": "fixed", "not-reproduced": "does-not-reproduce",
               "fix-failed": "fix-failed-after-retries",
               "inconclusive": "inconclusive"}.get(res.status, res.status)
    return {"finding_id": finding_id, "outcome": outcome, "attempts": res.attempts,
            "model": model, "lang": lang, "target": target, "detail": res.detail,
            "steps": steps}


def orchestrate(finding_ids: list[str] | None = None, max_fix_attempts: int = 2,
                worktree: str | None = None, network: str | None = None,
                *, model: str = ORCHESTRATOR_MODEL, effort: str = ORCHESTRATOR_EFFORT,
                timeout_s: int = ORCHESTRATOR_TIMEOUT_S) -> dict:
    """Run orchestrate_finding across findings (default: every validation
    scaffold). Sequential — each finding does Docker runs. Builders default to
    opus/high. Returns per-finding results + an outcome tally."""
    if finding_ids is None:
        vdir = CELL / "hunt" / "validation"
        finding_ids = sorted(p.stem for p in vdir.glob("*.yaml")) if vdir.is_dir() else []
    if not finding_ids:
        return {"ok": False, "error": "no validation scaffolds to orchestrate"}
    results = [orchestrate_finding(fid, max_fix_attempts, worktree, network,
                                   model=model, effort=effort, timeout_s=timeout_s)
               for fid in finding_ids]
    outcomes: dict[str, int] = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    inconclusive = sum(1 for r in results if not r.get("validated"))
    return {"ok": True, "total": len(results), "fixed": outcomes.get("fixed", 0),
            # R5: all_validated is False if any finding ended without a real
            # verdict (e.g. Docker down) — so `ok` alone never reads as success.
            "all_validated": inconclusive == 0, "inconclusive": inconclusive,
            "outcomes": outcomes, "model": model, "results": results}


# (re is imported at the top of the module.)


# ===========================================================================
# CLI — full command-line parity with the dashboard/MCP surface. Every headless
# operation reachable from the UI or MCP is also reachable as:
#     .venv/bin/python tool/pipeline.py <command> [args]
# Output is JSON on stdout; exit code is 0 when the op's `ok` is truthy, else 1.
# ===========================================================================
def _parse_pass_token(tok: str) -> tuple[str, int]:
    """'code-quality:2' -> ('code-quality', 2). A malformed token (no colon or
    non-int pass) returns pass_num -1 so run_hunt_batch rejects it as invalid
    rather than crashing the CLI (R14)."""
    angle, _, num = tok.rpartition(":")
    try:
        return angle, int(num)
    except ValueError:
        return angle, -1


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="pipeline",
        description="OSS bug-hunter headless pipeline — full CLI parity with the dashboard/MCP.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Pipeline state (steps + cursor).")
    sp = sub.add_parser("run-step", help="Run one AUTO step by id.")
    sp.add_argument("step_id")
    sub.add_parser("list-artifacts", help="List whitelisted artifacts + existence.")
    ra = sub.add_parser("read-artifact", help="Read one whitelisted artifact.")
    ra.add_argument("name")
    wa = sub.add_parser("write-artifact", help="Write a whitelisted output path.")
    wa.add_argument("name")
    wa.add_argument("file", help="content file path, or '-' for stdin")

    sub.add_parser("list-backtest", help="Per-issue backtest state.")
    rb = sub.add_parser("run-backtest", help="Run one backtest subagent.")
    rb.add_argument("issue")
    rbb = sub.add_parser("run-backtest-batch", help="Run backtest subagents in parallel.")
    rbb.add_argument("--issues", nargs="*", default=None, help="issue numbers; omit = all prepared")
    rbb.add_argument("--parallel", type=int, default=4)
    lb = sub.add_parser("label-backtest", help="Auto-label one backtest entry (advisory).")
    lb.add_argument("issue")

    sub.add_parser("run-explore", help="Run the Day-1 Explore inventory subagent.")

    rh = sub.add_parser("run-hunt", help="Run one hunt pass.")
    rh.add_argument("angle", choices=["code-quality", "edge-case"])
    rh.add_argument("pass_num", type=int, choices=[1, 2, 3])
    rhb = sub.add_parser("run-hunt-batch", help="Run hunt passes in parallel.")
    rhb.add_argument("--passes", nargs="*", default=None,
                     help="angle:pass tokens (e.g. code-quality:2 edge-case:3); omit = the 4 Day-4 passes")
    rhb.add_argument("--parallel", type=int, default=4)

    rr = sub.add_parser("run-repro", help="Build a reproducer .java for one finding.")
    rr.add_argument("finding_id")
    rrb = sub.add_parser("run-repro-batch", help="Build reproducers in parallel.")
    rrb.add_argument("--ids", nargs="*", default=None, help="finding ids; omit = all scaffolds missing a .java")
    rrb.add_argument("--parallel", type=int, default=4)

    rfx = sub.add_parser("run-fix", help="Build a fix patch for one finding (needs its reproducer).")
    rfx.add_argument("finding_id")
    rfxb = sub.add_parser("run-fix-batch", help="Build fix patches in parallel.")
    rfxb.add_argument("--ids", nargs="*", default=None, help="finding ids; omit = all with a reproducer but no patch")
    rfxb.add_argument("--parallel", type=int, default=4)

    sub.add_parser("suggest-gates", help="Advisory: auto-fill blank dedup/cwe gates (non-AI; deterministic).")
    orc = sub.add_parser("orchestrate", help="Self-correcting loop: reproduce → fix → validate → retry-with-feedback.")
    orc.add_argument("--ids", nargs="*", default=None, help="finding ids; omit = all validation scaffolds")
    orc.add_argument("--max-fix-attempts", type=int, default=2, help="retries after the first fix attempt (default 2)")
    orc.add_argument("--worktree", default=None, help="worktree/clone to validate against (default: targets/jackson-databind)")
    orc.add_argument("--network", default=None, help="REPRO_NETWORK passed to run-repro.sh/run-fix.sh")
    orc.add_argument("--model", default=ORCHESTRATOR_MODEL, help=f"builder model (default {ORCHESTRATOR_MODEL})")
    orc.add_argument("--effort", default=ORCHESTRATOR_EFFORT, help=f"builder effort (default {ORCHESTRATOR_EFFORT})")

    vfy = sub.add_parser("verify", help="VERIFY stage: build a reproducer if missing (Java + non-Java via the LLM repro-builder) and run it; mark the reproducer gate. Moves an ingested 'proposed' finding to 'reproduced'.")
    vfy.add_argument("finding_id")
    vfy.add_argument("--no-build", action="store_true", help="don't build a reproducer; only verify an existing one")
    vfy.add_argument("--worktree", default=None)
    vfy.add_argument("--network", default=None)
    vfy.add_argument("--model", default=ORCHESTRATOR_MODEL)
    vfy.add_argument("--effort", default=ORCHESTRATOR_EFFORT)

    pdq = sub.add_parser("pr-draft", help="Queue a validated keeper finding as a reviewable PR DRAFT (never pushes).")
    pdq.add_argument("finding_id")
    pdq.add_argument("--target", default="jackson-databind")
    pdq.add_argument("--force", action="store_true", help="queue even if not a ready keeper")
    sub.add_parser("pr-drafts", help="List queued PR drafts (the human review queue).")
    pdd = sub.add_parser("pr-decide", help="Record a human review decision on a PR draft (never pushes).")
    pdd.add_argument("finding_id")
    pdd.add_argument("decision", choices=["approved", "rejected"])
    pdd.add_argument("--note", default=None)

    dsc = sub.add_parser("discover", help="Rank candidate OSS repos to hunt (non-AI) from a JSON file and/or GitHub search; optionally enqueue for the scheduler.")
    dsc.add_argument("--json", default=None, help="JSON file of candidate repos (list or {candidates:[...]})")
    dsc.add_argument("--github", default=None, help="GitHub repo-search query (NETWORK), e.g. 'language:go stars:>500'")
    dsc.add_argument("--limit", type=int, default=20)
    dsc.add_argument("--deny", nargs="*", default=[], help="owner/name repos to skip")
    dsc.add_argument("--allow", nargs="*", default=None, help="restrict to these owners or owner/name repos")
    dsc.add_argument("--enqueue", action="store_true", help="persist the ranked queue for the scheduler")
    dsc.add_argument("--no-enrich", action="store_true", help="skip GitHub enrichment (has_tests/native_heavy via repos/.../languages + git tree) — cheaper, fewer API calls")
    dsc.add_argument("--rate-limit", type=float, default=0.0, metavar="SEC", help="min seconds between GitHub API calls (per-source pacing; backoff-on-throttle is always on)")

    sch = sub.add_parser("schedule", help="Outer loop (§12.5): consume the discovery queue → clone → hunt → fix → draft (budgeted, idempotent, NEVER pushes). Default DRY-RUN; --run executes (gated on the hunt step).")
    sch.add_argument("--queue", default=None, help="discovery queue file (default cell-1/hunt/discovery-queue.yaml)")
    sch.add_argument("--max-targets", type=int, default=5)
    sch.add_argument("--max-attempts", type=int, default=1)
    sch.add_argument("--run", action="store_true", help="execute for real (EngineSteps: clone + hunt (wired #61) + verify/fix/draft; a live run needs the model + an open-network host)")

    args = p.parse_args(argv)

    if args.cmd == "status":
        result = get_state()
    elif args.cmd == "run-step":
        result = run_step(args.step_id)
    elif args.cmd == "list-artifacts":
        result = {"ok": True, "artifacts": list_artifacts()}
    elif args.cmd == "read-artifact":
        r = get_artifact(args.name)
        result = {"ok": False, "error": f"unknown artifact {args.name!r}"} if r is None else {"ok": True, **r}
    elif args.cmd == "write-artifact":
        content = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        result = write_file(args.name, content)
    elif args.cmd == "list-backtest":
        result = {"ok": True, "entries": list_backtest_entries()}
    elif args.cmd == "run-backtest":
        result = run_backtest_subagent(args.issue)
    elif args.cmd == "run-backtest-batch":
        result = run_backtest_batch(args.issues, max(1, min(args.parallel, 10)))
    elif args.cmd == "label-backtest":
        result = label_backtest_subagent(args.issue)
    elif args.cmd == "run-explore":
        result = run_explore_subagent()
    elif args.cmd == "run-hunt":
        result = run_hunt_subagent(args.angle, args.pass_num)
    elif args.cmd == "run-hunt-batch":
        passes = [_parse_pass_token(t) for t in args.passes] if args.passes else None
        result = run_hunt_batch(passes, max(1, min(args.parallel, 10)))
    elif args.cmd == "run-repro":
        result = run_repro_subagent(args.finding_id)
    elif args.cmd == "run-repro-batch":
        result = run_repro_batch(args.ids, max(1, min(args.parallel, 10)))
    elif args.cmd == "run-fix":
        result = run_fix_subagent(args.finding_id)
    elif args.cmd == "run-fix-batch":
        result = run_fix_batch(args.ids, max(1, min(args.parallel, 10)))
    elif args.cmd == "suggest-gates":
        result = suggest_gates()
    elif args.cmd == "orchestrate":
        result = orchestrate(args.ids, max(0, args.max_fix_attempts), args.worktree,
                             args.network, model=args.model, effort=args.effort)
    elif args.cmd == "verify":
        result = verify_finding(args.finding_id, build=not args.no_build,
                                worktree=args.worktree, network=args.network,
                                model=args.model, effort=args.effort, log=print)
    elif args.cmd == "pr-draft":
        import pr_draft as _pd
        result = _pd.queue_draft(args.finding_id, args.target, force=args.force)
    elif args.cmd == "pr-drafts":
        import pr_draft as _pd
        result = {"ok": True, "drafts": _pd.list_drafts()}
    elif args.cmd == "pr-decide":
        import pr_draft as _pd
        result = _pd.decide_draft(args.finding_id, args.decision, note=args.note)
    elif args.cmd == "discover":
        import discovery as _disc
        srcs = []
        if args.json:
            srcs.append(_disc.JsonSource(args.json))
        if args.github:
            srcs.append(_disc.GitHubSearchSource(
                args.github, enrich=not args.no_enrich,
                limiter=_disc.RateLimiter(min_interval=args.rate_limit)))   # #59
        if not srcs:
            result = {"ok": False, "error": "provide --json <file> and/or --github <query>"}
        else:
            try:
                cands = _disc.discover(srcs, limit=args.limit, denylist=args.deny,
                                       allowlist=args.allow, log=lambda m: print(m, file=sys.stderr))
                result = {"ok": True, "count": len(cands), "candidates": cands}
                if args.enqueue:
                    result["enqueued"] = _disc.enqueue(cands)
            except (FileNotFoundError, ValueError) as e:   # bad --json path / malformed JSON
                result = {"ok": False, "error": f"discovery source failed: {e}"}
    elif args.cmd == "schedule":
        import scheduler as _sch
        q = _sch.load_queue(args.queue or _sch.QUEUE)
        budget = _sch.Budget(max_targets=args.max_targets, max_attempts=args.max_attempts)
        if args.run:
            result = {"ok": True, **_sch.run_once(q, _sch.EngineSteps(), budget=budget,
                                                  log=lambda m: print(m, file=sys.stderr))}
        else:
            result = {"ok": True, "dry_run": True, **_sch.plan(q, budget=budget)}
    else:  # pragma: no cover - argparse enforces choices
        result = {"ok": False, "error": f"unknown command {args.cmd!r}"}

    print(json.dumps(result, indent=2, default=str))
    # R5: a failed op exits 1; an orchestrate run that couldn't actually validate
    # everything (e.g. Docker down) exits non-zero too, so automation polling the
    # exit code never mistakes "inconclusive" for success.
    if not result.get("ok", True):
        return 1
    return 0 if result.get("all_validated", True) else 2


if __name__ == "__main__":
    sys.exit(_cli())
