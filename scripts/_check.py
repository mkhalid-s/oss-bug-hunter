#!/usr/bin/env python3
"""Content-based gate checks for the Cell #1 Makefile.

Each subcommand exits 0 if the corresponding state is "ready", 1 otherwise.
Used by the Makefile to gate recipes that depend on human-edited YAML.

Subcommands:
  dataset-finalized          dataset.yaml entries all have expected_subagent set
  backtest-runs-populated    every cell-1/backtest/runs/<n>/findings.yaml is non-stub
  backtest-labels-populated  every cell-1/backtest/runs/<n>/labels.yaml has labels filled
  hunt-findings-populated    both angles' findings-pass1.yaml are non-stub
  hunt-gates-filled          every cell-1/hunt/validation/*.yaml has final_status != pending
  passes23-populated         all four findings-pass{2,3}.yaml are non-stub
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(2)  # caller can detect missing dep

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # P0-2: relocatable
CELL = PROJECT_ROOT / "cell-1"


def _load(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        return None


def dataset_finalized() -> bool:
    d = _load(CELL / "backtest" / "dataset.yaml")
    if not d:
        return False
    ds = d.get("dataset") or []
    if not ds:
        return False
    return all(
        isinstance(e, dict) and e.get("expected_subagent")
        for e in ds
    )


#: Unique marker line stamped at the top of every stub file the pipeline
#: writes. The human deletes this line when they paste the real agent
#: output. _stub_findings detects "needs human action" iff this exact line
#: is still present — replacing the prior text-sniffing heuristic that
#: couldn't tell `findings: []` (legitimate "agent found nothing") from
#: "human hasn't pasted yet" when both contained the boilerplate comment.
#: P1-4 fix (2026-05-19).
STUB_SENTINEL = "# AGENT-OUTPUT-NOT-YET-PASTED — delete this line when pasting"


def _stub_findings(p: Path) -> bool:
    """True iff the file is a fresh stub still awaiting human paste.

    Uses an explicit sentinel marker (`STUB_SENTINEL`) rather than sniffing
    for the boilerplate comment text. A file is a stub iff:
      - the file does not exist, OR
      - the sentinel line is present anywhere in the content.

    Once the human pastes (or deletes the sentinel line), the file is
    treated as populated regardless of whether `findings: []` (legit zero
    result) or a populated list — that distinction is no longer ambiguous.
    """
    if not p.exists():
        return True
    txt = p.read_text()
    if STUB_SENTINEL in txt:
        return True
    # Backward-compat for any files written by an older version of the
    # pipeline (pre-P1-4): still treat them as stubs if BOTH the old
    # boilerplate comment AND `findings: []` are present. This catches
    # the legacy "needs paste" case without false-positive-ing on a
    # legitimate `findings: []` that has the sentinel removed.
    return (("Paste the agent's `findings:` YAML block" in txt
             or "Paste the YAML block from the agent" in txt)
            and "findings: []" in txt)


def backtest_runs_populated() -> bool:
    runs = CELL / "backtest" / "runs"
    if not runs.is_dir():
        return False
    issue_dirs = [d for d in runs.iterdir() if d.is_dir()]
    if not issue_dirs:
        return False
    for d in issue_dirs:
        if _stub_findings(d / "findings.yaml"):
            return False
    return True


def backtest_labels_populated() -> bool:
    runs = CELL / "backtest" / "runs"
    if not runs.is_dir():
        return False
    issue_dirs = [d for d in runs.iterdir() if d.is_dir()]
    if not issue_dirs:
        return False
    for d in issue_dirs:
        lbl = _load(d / "labels.yaml")
        if not lbl:
            return False
        # Must have at least one label entry OR matched_rank explicitly set to null
        # (null = agent found nothing, which is a valid result)
        labels = lbl.get("labels") or []
        matched_rank = lbl.get("matched_rank", "MISSING")
        # If labels list is non-empty, that's populated.
        if labels:
            continue
        # If matched_rank is explicitly null AND findings is empty, that's a legit "scored zero" case
        findings = _load(d / "findings.yaml") or {}
        if matched_rank is None and not findings.get("findings"):
            continue
        return False
    return True


def hunt_findings_populated() -> bool:
    for angle in ("code-quality", "edge-case"):
        p = CELL / "hunt" / angle / "findings-pass1.yaml"
        if _stub_findings(p):
            return False
    return True


def hunt_gates_filled() -> bool:
    vdir = CELL / "hunt" / "validation"
    if not vdir.is_dir():
        return False
    scaffolds = list(vdir.glob("*.yaml"))
    if not scaffolds:
        return False
    for p in scaffolds:
        d = _load(p) or {}
        status = d.get("final_status")
        if not status or status == "pending":
            return False
    return True


def passes23_populated() -> bool:
    for angle in ("code-quality", "edge-case"):
        for n in (2, 3):
            p = CELL / "hunt" / angle / f"findings-pass{n}.yaml"
            if _stub_findings(p):
                return False
    return True


CHECKS = {
    "dataset-finalized":         dataset_finalized,
    "backtest-runs-populated":   backtest_runs_populated,
    "backtest-labels-populated": backtest_labels_populated,
    "hunt-findings-populated":   hunt_findings_populated,
    "hunt-gates-filled":         hunt_gates_filled,
    "passes23-populated":        passes23_populated,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: _check.py <{' | '.join(CHECKS)}>", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if CHECKS[sys.argv[1]]() else 1)


if __name__ == "__main__":
    main()
