#!/usr/bin/env python3
"""Day 2 backtest runner for Cell #1 (Jackson-databind x correctness).

Two subcommands, run in order:

  prepare   — read cell-1/backtest/dataset.yaml; for each entry, set up a
              per-issue git worktree at the parent (pre-fix) commit, emit a
              uniform per-entry prompt, and create scoring stubs.

              The human then runs each prompt against a fresh subagent
              (subagent_type=general-purpose or code-reviewer) in their
              Claude Code session, captures the agent's findings YAML, and
              labels each finding (matches_known | unrelated_tp | fp | dupe_of_baseline).

  score     — read all per-entry findings + labels; compute recall@K and
              precision@K; cross-check against scanner baselines (Semgrep,
              SpotBugs) to flag whether the historical bug was already
              catchable by free tools. Write cell-1-backtest.md.

See phase-0-scope.md §3 and the review-notes at the top of the project for
why baseline-comparison matters (if free tools already catch the bug, agent
adds zero novelty even if recall is high).

Dependencies: python3, PyYAML, jackson-databind already cloned.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML not installed. Run: pip install pyyaml")

# ---- paths ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # P0-2: relocatable
TARGET_DIR = PROJECT_ROOT / "targets" / "jackson-databind"
RECON_DIR = PROJECT_ROOT / "cell-1" / "recon"
BACKTEST_DIR = PROJECT_ROOT / "cell-1" / "backtest"
WORKTREES_DIR = BACKTEST_DIR / "worktrees"
RUNS_DIR = BACKTEST_DIR / "runs"
DATASET_PATH = BACKTEST_DIR / "dataset.yaml"
REPORT_PATH = PROJECT_ROOT / "cell-1" / "cell-1-backtest.md"

# Scoring uses top-K. K=5 because the prompt caps findings at 5.
K = 5


# ---- shared utilities ----
def git(*args: str, default: str = "") -> str:
    r = subprocess.run(["git", "-C", str(TARGET_DIR), *args],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else default


def load_dataset() -> list[dict]:
    if not DATASET_PATH.exists():
        sys.exit(f"FAIL: {DATASET_PATH} missing. Run day2-build-dataset.py then hand-pick 10.")
    data = yaml.safe_load(DATASET_PATH.read_text()) or {}
    ds = data.get("dataset") or []
    if not ds:
        sys.exit(f"FAIL: {DATASET_PATH} has no 'dataset:' list.")
    # Drop empty/template placeholders
    ds = [e for e in ds if isinstance(e, dict) and e.get("issue")]
    if not ds:
        sys.exit(f"FAIL: {DATASET_PATH} has no populated entries.")
    return ds


# ---- prepare subcommand ----
PROMPT_TEMPLATE = """\
You are reviewing a code module for CORRECTNESS bugs (not security, not style).

Working directory (already checked out at the relevant snapshot):
  {worktree}

Files to investigate (read these in full, plus any callers/callees needed):
{files_block}

Task: find at most 5 distinct correctness bugs in the listed files. Examples
of correctness bugs: NullPointerException on unusual input, off-by-one, wrong
return value, edge case in encoding/timezone/format handling, race condition,
infinite loop, deadlock, incorrect generic-type handling, dropped exception.

Out of scope: security (deserialization gadgets, authn/authz), code style,
refactoring opportunities, performance unless it's a correctness issue (e.g.,
StackOverflow), missing tests, javadoc gaps.

Confidence bar: only report findings where you would bet money that triggering
the described input causes the described wrong behavior. If unsure, OMIT.

Return your findings as a single YAML block at the end of your message. If you
find nothing, return an empty list.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<file:line-range>"
    type: "<NPE | off-by-one | wrong-return | edge-case | race | other>"
    evidence: "<excerpt of buggy code + why it's wrong>"
    reproducer_hint: "<one-line: how to trigger>"
```

Do NOT consult the git history, release notes, or issue tracker. Reason only
from the code in the working directory above.
"""

FINDINGS_STUB = """\
# AGENT-OUTPUT-NOT-YET-PASTED — delete this line when pasting
# Agent-produced findings for this entry.
# Paste the YAML block from the agent's final message here, then save.
# (If the agent returned an empty list, delete the sentinel line above
# and leave `findings: []` below — that signals "agent found nothing".)

findings: []
"""

LABELS_STUB_TEMPLATE = """\
# Human scoring for this backtest entry.
#
# For each finding in findings.yaml, add an entry below with the same index
# (0-based, in order) and a label:
#
#   matches_known      — describes the historical bug for this entry
#   unrelated_tp       — describes a different real bug
#   fp                 — describes something that is not actually a bug
#   dupe_of_baseline   — duplicates a Semgrep or SpotBugs baseline finding
#
# Add a one-line 'note' for matches_known and unrelated_tp entries.

issue: {issue}
known_bug:
  title: "{title}"
  url: "{url}"
  fix_commit: {fix_commit}

# matched_rank: position (1-indexed) of the first matches_known finding, or null.
matched_rank: null

labels:
  # - index: 0
  #   label: matches_known
  #   note: "Identified the NPE in getX() when input is null"
"""


def prepare() -> None:
    if not (TARGET_DIR / ".git").is_dir():
        sys.exit(f"FAIL: target not cloned at {TARGET_DIR}. Run day1-recon.sh first.")
    dataset = load_dataset()
    print(f"[prepare] {len(dataset)} entries in dataset", file=sys.stderr)

    # P1-16: each worktree is a separate working tree (~500MB on jackson-databind).
    # 10 entries × ~500MB = ~5GB. Default cap is conservative; the user can raise it
    # via env var once they've checked disk space.
    import os as _os
    worktree_cap = int(_os.environ.get("OSS_BUG_HUNTER_WORKTREE_MAX", "10"))
    if len(dataset) > worktree_cap:
        print(f"[prepare] WARN dataset has {len(dataset)} entries; worktree budget cap is "
              f"{worktree_cap}. Set OSS_BUG_HUNTER_WORKTREE_MAX=<N> to raise (each worktree "
              f"is ~500MB).", file=sys.stderr)
        print(f"[prepare]   Will process first {worktree_cap} entries only.", file=sys.stderr)
        dataset = dataset[:worktree_cap]

    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    runbook_lines: list[str] = []
    runbook_lines.append("# Day 2 Backtest Runbook\n")
    runbook_lines.append("Run each entry's prompt against a fresh subagent (subagent_type=general-purpose ")
    runbook_lines.append("or code-reviewer). After each run, paste the agent's findings YAML into the ")
    runbook_lines.append("entry's `findings.yaml`, then label each finding in `labels.yaml`. When all ")
    runbook_lines.append("10 are done, run: `python3 scripts/day2-backtest.py score`.\n\n")

    for i, entry in enumerate(dataset, start=1):
        issue = entry["issue"]
        parent = entry.get("parent_commit") or ""
        files = entry.get("files_touched") or []
        if not parent:
            print(f"[prepare] WARN issue #{issue} has no parent_commit; skipping", file=sys.stderr)
            continue
        files = [f for f in files if f.startswith("src/main/java/")]
        if not files:
            print(f"[prepare] WARN issue #{issue} has no src/main/java files; skipping", file=sys.stderr)
            continue

        worktree = WORKTREES_DIR / str(issue)
        run_dir = RUNS_DIR / str(issue)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Worktree (idempotent: skip if already present at correct commit)
        if worktree.is_dir() and (worktree / ".git").exists():
            current_sha = subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            ).stdout.strip()
            if current_sha != parent:
                # Re-checkout in place rather than recreating worktree
                subprocess.run(["git", "-C", str(worktree), "checkout", "--quiet", parent], check=False)
        else:
            r = subprocess.run(
                ["git", "-C", str(TARGET_DIR), "worktree", "add", "--quiet",
                 "--detach", str(worktree), parent],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"[prepare] WARN issue #{issue}: worktree add failed: {r.stderr.strip()}",
                      file=sys.stderr)
                continue

        # Per-entry prompt
        files_block = "\n".join(f"  - {f}" for f in files[:8])
        prompt_text = PROMPT_TEMPLATE.format(worktree=worktree, files_block=files_block)
        (run_dir / "prompt.md").write_text(prompt_text)

        # Findings stub (agent output paste target)
        findings_path = run_dir / "findings.yaml"
        if not findings_path.exists():
            findings_path.write_text(FINDINGS_STUB)

        # Labels stub (human scoring)
        labels_path = run_dir / "labels.yaml"
        if not labels_path.exists():
            labels_path.write_text(LABELS_STUB_TEMPLATE.format(
                issue=issue,
                title=(entry.get("title") or "").replace('"', '\\"'),
                url=entry.get("url") or "",
                fix_commit=entry.get("fix_commit") or "",
            ))

        runbook_lines.append(f"## Entry {i} — issue #{issue}\n")
        runbook_lines.append(f"- Title: {entry.get('title', '?')}\n")
        runbook_lines.append(f"- Parent commit: `{parent[:12]}`\n")
        runbook_lines.append(f"- Worktree: `{worktree}`\n")
        runbook_lines.append(f"- Prompt: `{run_dir / 'prompt.md'}`\n")
        runbook_lines.append(f"- After run: paste findings → `{run_dir / 'findings.yaml'}`\n")
        runbook_lines.append(f"- After review: label findings → `{run_dir / 'labels.yaml'}`\n\n")

    runbook_path = BACKTEST_DIR / "runbook.md"
    runbook_path.write_text("".join(runbook_lines))
    print(f"[prepare] runbook → {runbook_path}", file=sys.stderr)
    print(f"[prepare] {len(dataset)} entries prepared under {RUNS_DIR}", file=sys.stderr)
    print()
    print("Next: open runbook.md and execute each entry's prompt in a fresh subagent.")
    print("When all entries have findings.yaml + labels.yaml filled, run:")
    print("  python3 scripts/day2-backtest.py score")


# ---- score subcommand ----
def load_baseline_files() -> dict[str, set[str]]:
    """Return {tool: {set of file paths flagged by tool}} for baseline comparison."""
    baselines: dict[str, set[str]] = {}

    semgrep = RECON_DIR / "scanners" / "semgrep.json"
    if semgrep.exists():
        try:
            data = json.loads(semgrep.read_text())
            files = {r.get("path", "") for r in data.get("results", []) if r.get("path")}
            # Normalize to repo-relative paths
            target_prefix = str(TARGET_DIR) + "/"
            baselines["semgrep"] = {
                f.removeprefix(target_prefix) if f.startswith(target_prefix) else f
                for f in files
            }
        except (json.JSONDecodeError, OSError):
            pass

    spotbugs = RECON_DIR / "scanners" / "spotbugs.xml"
    if spotbugs.exists():
        try:
            text = spotbugs.read_text()
            # crude: extract <SourceLine sourcepath="..."/> paths
            sourcepaths = set(re.findall(r'sourcepath="([^"]+)"', text))
            baselines["spotbugs"] = sourcepaths
        except OSError:
            pass

    return baselines


def baseline_flags_file(file_path: str, baselines: dict[str, set[str]]) -> list[str]:
    """Return which baseline tools flagged this file (any finding, not necessarily the same one)."""
    tools = []
    for tool, files in baselines.items():
        # Match by basename suffix (handles relative-vs-absolute path differences)
        basename = file_path.rsplit("/", 1)[-1]
        for flagged in files:
            if flagged == file_path or flagged.endswith("/" + file_path) or flagged.endswith("/" + basename):
                tools.append(tool)
                break
    return tools


def _finding_file(finding: dict) -> str:
    """Extract the repo-relative file path from a finding's `location` field."""
    loc = (finding or {}).get("location", "") or ""
    return loc.split(":", 1)[0].strip()


def score() -> None:
    dataset = load_dataset()
    baselines = load_baseline_files()
    if not baselines:
        print("[score] WARN no scanner baselines found; baseline-comparison column will be N/A",
              file=sys.stderr)

    # Per-entry rollup
    rows: list[dict] = []
    # Deterministic file-coverage metrics (P0-1 fix: replaces LLM-label-driven recall as the gate input)
    file_match_at_1 = 0
    file_match_at_3 = 0
    file_match_at_5 = 0
    total_file_matches = 0
    # LLM-driven metrics (kept as informational only — no longer gate-required)
    matched_at_1 = 0
    matched_at_3 = 0
    matched_at_5 = 0
    total_findings = 0
    total_matches_known = 0     # P0-3 fix: report this separately from anyTP
    total_unrelated_tp = 0
    total_true_positives = 0    # = matches_known + unrelated_tp (kept for transparency)
    total_fp = 0
    total_dupe_baseline = 0

    for entry in dataset:
        issue = entry["issue"]
        run_dir = RUNS_DIR / str(issue)
        findings_path = run_dir / "findings.yaml"
        labels_path = run_dir / "labels.yaml"
        # P0-1: findings.yaml is required (deterministic scoring). labels.yaml is now optional.
        if not findings_path.exists():
            print(f"[score] WARN issue #{issue}: findings.yaml missing — skipping", file=sys.stderr)
            continue

        findings_doc = yaml.safe_load(findings_path.read_text()) or {}
        findings = findings_doc.get("findings") or []
        # labels.yaml is optional; if absent, treat all label counts as zero.
        labels_doc = yaml.safe_load(labels_path.read_text()) if labels_path.exists() else {}
        labels_doc = labels_doc or {}
        labels = labels_doc.get("labels") or []
        matched_rank = labels_doc.get("matched_rank")
        labels_method = labels_doc.get("_method", "human-or-llm")

        # ---- P0-1: deterministic file-coverage from findings + fix files (no LLM, no label needed) ----
        fix_files = set(entry.get("files_touched", []))
        n = len(findings)
        finding_files = [_finding_file(f) for f in findings if isinstance(f, dict)]
        file_match_rank: int | None = None
        n_file_matches_here = 0
        for idx, fp in enumerate(finding_files, start=1):
            if fp in fix_files:
                n_file_matches_here += 1
                if file_match_rank is None:
                    file_match_rank = idx
        if file_match_rank is not None:
            if file_match_rank <= 1: file_match_at_1 += 1
            if file_match_rank <= 3: file_match_at_3 += 1
            if file_match_rank <= 5: file_match_at_5 += 1
        total_file_matches += n_file_matches_here

        # ---- LLM/human-driven label counts (informational, optional) ----
        label_by_index = {l.get("index"): l.get("label") for l in labels if isinstance(l, dict)}
        n_matches = sum(1 for v in label_by_index.values() if v == "matches_known")
        n_unrelated_tp = sum(1 for v in label_by_index.values() if v == "unrelated_tp")
        n_fp = sum(1 for v in label_by_index.values() if v == "fp")
        n_dupe = sum(1 for v in label_by_index.values() if v == "dupe_of_baseline")

        # Recall@K — if any matches_known landed within the top K findings
        # (matched_rank is 1-indexed; null = no match)
        if matched_rank and isinstance(matched_rank, int):
            if matched_rank <= 1: matched_at_1 += 1
            if matched_rank <= 3: matched_at_3 += 1
            if matched_rank <= 5: matched_at_5 += 1

        total_findings += n
        total_matches_known += n_matches
        total_unrelated_tp += n_unrelated_tp
        total_true_positives += n_matches + n_unrelated_tp
        total_fp += n_fp
        total_dupe_baseline += n_dupe

        # Baseline coverage for the *historical* bug — did Semgrep/SpotBugs flag any file
        # the fix touched? (Crude proxy for "free tools would have caught it.")
        touched_baseline = set()
        for f in entry.get("files_touched", [])[:8]:
            if f.startswith("src/main/java/"):
                touched_baseline.update(baseline_flags_file(f, baselines))

        rows.append({
            "issue": issue,
            "title": entry.get("title", "")[:60],
            "n_findings": n,
            "file_match_rank": file_match_rank,
            "file_matches": n_file_matches_here,
            "matched_rank": matched_rank,
            "matches_known": n_matches,
            "unrelated_tp": n_unrelated_tp,
            "fp": n_fp,
            "dupe_baseline": n_dupe,
            "labels_method": labels_method,
            "baseline_flagged_file": sorted(touched_baseline) or ["—"],
        })

    n_runs = len(rows)
    if n_runs == 0:
        sys.exit("FAIL: no completed runs to score. Fill findings.yaml + labels.yaml first.")

    # ---- Deterministic file-coverage metrics (P0-1 — primary gate inputs) ----
    file_coverage_1 = file_match_at_1 / n_runs if n_runs else 0.0
    file_coverage_3 = file_match_at_3 / n_runs if n_runs else 0.0
    file_coverage_5 = file_match_at_5 / n_runs if n_runs else 0.0
    file_match_precision = (total_file_matches / total_findings) if total_findings else 0.0

    # ---- LLM/human-driven metrics (informational; never the sole gate basis) ----
    recall_1 = matched_at_1 / n_runs if n_runs else 0.0
    recall_3 = matched_at_3 / n_runs if n_runs else 0.0
    recall_5 = matched_at_5 / n_runs if n_runs else 0.0
    # P0-3 fix: report both. Gate uses precision_matches (strict).
    precision_matches = (total_matches_known / total_findings) if total_findings else 0.0
    precision_anyTP = (total_true_positives / total_findings) if total_findings else 0.0
    baseline_coverage = sum(1 for r in rows if r["baseline_flagged_file"] != ["—"]) / n_runs

    # P0-6 detect: are scanner baselines present at all?
    baselines_present = bool(baselines)  # baselines is {tool: {file_set}}
    # P0-1: are any labels populated at all (i.e. is there any LLM/human judgment to report)?
    labels_present = any(r["matches_known"] + r["unrelated_tp"] + r["fp"] > 0 for r in rows) \
                     or any(r["matched_rank"] is not None for r in rows)

    # Write report
    md = []
    md.append("# Cell #1 Day 2 Backtest Report\n")
    generated_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    md.append(f"\n**Generated:** {generated_at} — re-run `python3 scripts/day2-backtest.py score` to refresh.\n")
    md.append(f"\n## Aggregate metrics ({n_runs} entries scored)\n\n")
    md.append("### Deterministic file-coverage (primary gate input — no LLM judgment involved)\n\n")
    md.append("| Metric | Value | Phase 0 gate |\n")
    md.append("|---|---|---|\n")
    md.append(f"| file_coverage@1 | {file_coverage_1:.0%} ({file_match_at_1}/{n_runs}) | — |\n")
    md.append(f"| **file_coverage@3** | {file_coverage_3:.0%} ({file_match_at_3}/{n_runs}) | **≥30% required** to proceed to Day 3 |\n")
    md.append(f"| file_coverage@5 | {file_coverage_5:.0%} ({file_match_at_5}/{n_runs}) | — |\n")
    md.append(f"| **file_match_precision@{K}** | {file_match_precision:.0%} ({total_file_matches}/{total_findings} findings) | **≥20% required** (gate input) |\n")
    md.append(f"| total findings | {total_findings} | — |\n")
    md.append(f"| total file matches | {total_file_matches} | — |\n\n")
    md.append("> *file_coverage@K* counts an entry as covered iff one of the agent's top-K findings\n")
    md.append("> names a file the historical fix actually touched. *file_match_precision@K* is the\n")
    md.append("> fraction of findings overall that hit a fix-touched file. Both are computed from\n")
    md.append("> `files_touched` (deterministic) — no LLM labels involved.\n\n")

    if labels_present:
        md.append("### LLM/human-judged labels (informational only — NOT a gate input as of P0-1 fix)\n\n")
        md.append("| Metric | Value | Status |\n")
        md.append("|---|---|---|\n")
        md.append(f"| recall@1 | {recall_1:.0%} ({matched_at_1}/{n_runs}) | informational |\n")
        md.append(f"| recall@3 | {recall_3:.0%} ({matched_at_3}/{n_runs}) | informational |\n")
        md.append(f"| recall@5 | {recall_5:.0%} ({matched_at_5}/{n_runs}) | informational |\n")
        md.append(f"| precision_matches@{K} | {precision_matches:.0%} ({total_matches_known}/{total_findings} `matches_known`) | informational |\n")
        md.append(f"| precision_anyTP@{K} | {precision_anyTP:.0%} ({total_true_positives}/{total_findings} matches_known + unrelated_tp) | informational |\n")
        md.append(f"| matches_known total | {total_matches_known} | — |\n")
        md.append(f"| unrelated_tp total | {total_unrelated_tp} | — |\n")
        md.append(f"| FP total | {total_fp} | — |\n\n")
        # Surface labelling method per entry (auto vs human) — flags circularity risk
        n_auto = sum(1 for r in rows if r.get("labels_method") == "deterministic")
        md.append(f"_Note: {n_auto}/{n_runs} entries have deterministic labels; "
                  f"others reflect LLM auto-label or human review._\n\n")
    else:
        md.append("### LLM/human-judged labels\n\n_None present — `labels.yaml` files are empty or "
                  "stubbed. Deterministic file-coverage metrics above are the sole gate input._\n\n")

    md.append(f"### Baseline scanner coverage\n\n")
    md.append("| Metric | Value | Status |\n|---|---|---|\n")
    md.append(f"| baseline coverage | {baseline_coverage:.0%} ({sum(1 for r in rows if r['baseline_flagged_file'] != ['—'])}/{n_runs}) | informational |\n")
    md.append(f"| baseline-dupe count | {total_dupe_baseline} | informational |\n\n")

    md.append("## Per-entry rollup\n\n")
    md.append("| Issue | Title | #findings | File-match rank | File matches | Label rank | Matches_known | Unrelated_tp | FP | Baseline-flagged file |\n")
    md.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        fm_rank = str(r["file_match_rank"]) if r["file_match_rank"] else "—"
        rank_str = str(r["matched_rank"]) if r["matched_rank"] else "—"
        md.append(f"| #{r['issue']} | {r['title']} | {r['n_findings']} | {fm_rank} | "
                  f"{r['file_matches']} | {rank_str} | "
                  f"{r['matches_known']} | {r['unrelated_tp']} | {r['fp']} | "
                  f"{', '.join(r['baseline_flagged_file'])} |\n")

    md.append("\n## Caveats\n\n")
    md.append("- **Statistical thinness:** 10-entry recall has ±15% error bars. Treat metrics as directional.\n")
    md.append("- **Found-fix bias:** the prompt pointed the agent at the right *file*. Real novel hunting "
              "doesn't get that hint. Adjust expectations: real-world recall will be lower.\n")
    md.append("- **Baseline coverage = 'free tools touched the file'**, NOT 'free tools caught the exact "
              "bug'. If baseline-flagged is high but agent's `matches_known` is on different findings than "
              "the baselines, the agent may still be adding signal. Inspect dupe_of_baseline counts.\n")
    md.append("- **If baseline coverage ≈ 100% AND dupe_of_baseline ≈ matches_known**, the agent is mostly "
              "regenerating free-tool output. **Kill Cell #1** even if recall@3 passed.\n")

    md.append("\n## Gate decision\n\n")

    # P0-6: refuse to compute PROCEED/KILL when scanner baselines are missing.
    # The "novel signal over baseline" gate is structurally unfalsifiable without them.
    if not baselines_present:
        md.append("**Decision: BASELINES_MISSING — gate not evaluable.**\n\n")
        md.append("Neither Semgrep nor SpotBugs baseline output is present in "
                  "`cell-1/recon/scanners/`. The Phase-0 'novel signal over free tools' "
                  "gate cannot be evaluated without them.\n\n")
        md.append("To recover:\n\n")
        md.append("```\npipx install semgrep   # or: brew install semgrep\n")
        md.append("# install spotbugs: see https://spotbugs.readthedocs.io/en/latest/installing.html\n")
        md.append("make reset-backtest && make           # re-run from recon\n```\n\n")
        md.append("Or, accept incomplete gating: re-run with `--allow-no-baseline`. "
                  "(Not yet implemented — this would weaken Phase-0's main claim.)\n")
        decision = "BASELINES_MISSING"
    else:
        md.append("Phase 0 Cell #1 proceeds to Day 3 novel-hunt IFF all of:\n\n")
        # P0-1 fix: gate inputs are now deterministic file-coverage metrics,
        # not LLM-judged recall/precision.
        pass_fc3 = file_coverage_3 >= 0.30
        pass_fmp5 = file_match_precision >= 0.20
        # "Novel signal over baseline" check: at least one entry where a file_match
        # happened in a file NOT also flagged by Semgrep/SpotBugs (i.e. agent found
        # something in code the free tools didn't even touch).
        novel_signal_rows = [r for r in rows if r["file_matches"] > 0
                             and r["baseline_flagged_file"] == ["—"]]
        novel_signal = len(novel_signal_rows) > 0
        md.append(f"- [{'x' if pass_fc3 else ' '}] file_coverage@3 ≥ 30%  (actual: {file_coverage_3:.0%})\n")
        md.append(f"- [{'x' if pass_fmp5 else ' '}] file_match_precision@{K} ≥ 20%  (actual: {file_match_precision:.0%})\n")
        md.append(f"- [{'x' if novel_signal else ' '}] at least one entry has file_match where the file was NOT baseline-flagged  ({len(novel_signal_rows)}/{n_runs})\n")

        decision = "PROCEED to Day 3" if (pass_fc3 and pass_fmp5 and novel_signal) else "KILL — see §6 of scope for retry protocol"
        md.append(f"\n**Decision:** {decision}\n")
        md.append("\n_Gate inputs are deterministic file-coverage metrics (P0-1 fix). The LLM-driven "
                  "labels above are advisory; they do not affect PROCEED/KILL._\n")

    REPORT_PATH.write_text("".join(md))
    print(f"[score] report → {REPORT_PATH}", file=sys.stderr)
    print(f"[score] file_coverage@3 = {file_coverage_3:.0%}, file_match_precision@{K} = {file_match_precision:.0%}",
          file=sys.stderr)
    if labels_present:
        print(f"[score] (advisory) recall@3 = {recall_3:.0%}, precision_matches@{K} = {precision_matches:.0%}",
              file=sys.stderr)
    print(f"[score] decision: {decision}")


# ---- main ----
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="Set up worktrees + per-entry prompts (run once after dataset.yaml is finalized)")
    sub.add_parser("score",   help="Aggregate per-entry findings+labels, write cell-1-backtest.md")
    args = p.parse_args()

    if args.cmd == "prepare":
        prepare()
    elif args.cmd == "score":
        score()


if __name__ == "__main__":
    main()
