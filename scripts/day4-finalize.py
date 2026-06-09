#!/usr/bin/env python3
"""Day 4 finalize for Cell #1 (Jackson-databind x correctness).

Two subcommands:

  prepare   — Create empty pass-2 and pass-3 findings stubs alongside the
              existing pass-1 files. Day 3's prompt.md per angle stays as the
              canonical hunt prompt — re-run it in two more fresh subagent
              contexts per angle (4 additional runs total on Day 4).

  report    — Self-consistency check + final write-up.
              For each pass-1 finding, look for a semantically-similar finding
              in pass-2 and pass-3 (same file + >=2 keyword overlap). A finding
              survives self-consistency iff it appears in >=2 of the 3 contexts
              (pass-1 always counts; needs at least 1 of pass-2/pass-3 to match).
              Updates each validation scaffold's self_consistency block, then
              writes cell-1/cell-1-report.md (the final Cell #1 report).

The "2-of-3 rule" comes from the research brief §5.4 self-consistency gate.

Dependencies: python3, PyYAML, prior days completed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML not installed. Run: pip install pyyaml")

# ---- paths ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # P0-2: relocatable
CELL_DIR = PROJECT_ROOT / "cell-1"
HUNT_DIR = CELL_DIR / "hunt"
VALIDATION_DIR = HUNT_DIR / "validation"
PASS1_REPORT = CELL_DIR / "cell-1-candidates-pass1.md"
FINAL_REPORT = CELL_DIR / "cell-1-report.md"

ANGLES = ("code-quality", "edge-case")
PASSES = (1, 2, 3)

STUB = """\
# AGENT-OUTPUT-NOT-YET-PASTED — delete this line when pasting
# Pass-{n} re-run for self-consistency.
# Paste the agent's `findings:` YAML block from a FRESH subagent context here.
# Use the same prompt.md as pass-1 — do NOT modify the prompt between passes.
# If the agent returned an empty list, delete the sentinel line above and keep
# `findings: []` — that signals "agent found nothing this pass".

findings: []
"""

# ---- helpers ----
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import extract_keyword_set_ci as extract_keywords  # noqa: E402


def extract_file(location: str) -> str:
    """'src/main/java/.../Foo.java:123-145' → 'src/main/java/.../Foo.java'."""
    return (location or "").split(":", 1)[0].strip()


def load_findings(angle: str, pass_n: int) -> list[dict]:
    path = HUNT_DIR / angle / f"findings-pass{pass_n}.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("findings") or []


def find_match(target: dict, candidates: list[dict]) -> dict | None:
    """Best-fit match by same-file + max keyword overlap (>=2)."""
    match, _reason = find_match_with_diag(target, candidates)
    return match


def find_match_with_diag(target: dict, candidates: list[dict]) -> tuple[dict | None, str]:
    """Same as find_match, but also returns a reason code for diagnostics.

    P1-15: lets the report surface WHY a pass-1 finding failed self-consistency.
    Reason codes:
      - "match"                       — found a same-file >=2-keyword overlap candidate
      - "no_target_file"              — pass-1 finding had no parsable file path
      - "no_target_keywords"          — pass-1 summary has zero meaningful keywords
      - "no_same_file_candidate"      — none of the pass-N findings touched the same file
      - "same_file_overlap_le_1:<N>"  — same-file candidate found, but max overlap was N (<2)
    """
    tgt_file = extract_file(target.get("location", ""))
    tgt_kw = extract_keywords(target.get("summary", ""))
    if not tgt_file:
        return None, "no_target_file"
    if not tgt_kw:
        return None, "no_target_keywords"

    same_file_count = 0
    best: dict | None = None
    best_score = 1  # require strictly >=2 overlap
    max_overlap = 0
    for c in candidates:
        if extract_file(c.get("location", "")) != tgt_file:
            continue
        same_file_count += 1
        overlap = len(tgt_kw & extract_keywords(c.get("summary", "")))
        if overlap > max_overlap:
            max_overlap = overlap
        if overlap > best_score:
            best_score = overlap
            best = c

    if best is not None:
        return best, "match"
    if same_file_count == 0:
        return None, "no_same_file_candidate"
    return None, f"same_file_overlap_le_1:{max_overlap}"


# ---- subcommand: prepare ----
def cmd_prepare() -> None:
    if not HUNT_DIR.exists():
        sys.exit(f"FAIL: {HUNT_DIR} missing. Run Day 3 (day3-hunt.py prepare) first.")

    created = 0
    for angle in ANGLES:
        angle_dir = HUNT_DIR / angle
        if not (angle_dir / "prompt.md").exists():
            sys.exit(f"FAIL: {angle_dir / 'prompt.md'} missing. Run day3-hunt.py prepare first.")
        for n in (2, 3):
            stub_path = angle_dir / f"findings-pass{n}.yaml"
            if stub_path.exists():
                continue
            stub_path.write_text(STUB.format(n=n))
            created += 1
            print(f"[prepare] stub → {stub_path}", file=sys.stderr)

    if created == 0:
        print("[prepare] all pass-2/pass-3 stubs already exist; nothing to do.", file=sys.stderr)

    print()
    print("Next: in Claude Code, re-run each angle's prompt against TWO MORE fresh")
    print("subagent contexts (one for pass-2, one for pass-3). 4 additional runs total:")
    print()
    for angle in ANGLES:
        for n in (2, 3):
            print(f"  - {angle} pass-{n}: run cell-1/hunt/{angle}/prompt.md → "
                  f"paste into cell-1/hunt/{angle}/findings-pass{n}.yaml")
    print()
    print("CRITICAL: each pass must be a FRESH subagent context (no carryover from")
    print("pass-1 or each other). If using Claude Code's Agent tool, that's automatic")
    print("— a fresh Agent() call = a fresh context.")
    print()
    print("When all four pass-2/pass-3 files are populated, run:")
    print("  python3 scripts/day4-finalize.py report")


# ---- subcommand: report ----
def update_scaffold_with_self_consistency(
    scaffold_path: Path,
    pass2_match: dict | None,
    pass3_match: dict | None,
) -> dict:
    """Mutate the scaffold to add a self_consistency block. Returns updated scaffold."""
    data = yaml.safe_load(scaffold_path.read_text()) or {}
    matched_in = []
    if pass2_match: matched_in.append("pass-2")
    if pass3_match: matched_in.append("pass-3")
    agreement = 1 + len(matched_in)  # pass-1 always counts
    survived = agreement >= 2

    data["self_consistency"] = {
        "passes_total": 3,
        "agreement": f"{agreement}/3",
        "matched_in": matched_in,
        "survived": survived,
        "pass2_match_summary": (pass2_match or {}).get("summary"),
        "pass3_match_summary": (pass3_match or {}).get("summary"),
    }
    # If pass-1 had marked this validated but self-consistency failed, downgrade.
    if not survived and (data.get("final_status") == "validated"):
        data["final_status"] = "failed-self-consistency"

    # width is effectively unbounded + allow_unicode so long em-dash notes stay on
    # ONE line and aren't escaped to \uXXXX. A wrapped/escaped multi-line scalar
    # would later break the single-line surgical gate editors (set_*_gate) if a
    # gate is (re)validated after this round-trip.
    scaffold_path.write_text(yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, width=10**9, allow_unicode=True))
    return data


def cmd_report() -> None:
    if not VALIDATION_DIR.is_dir() or not any(VALIDATION_DIR.glob("*.yaml")):
        sys.exit(f"FAIL: no scaffolds at {VALIDATION_DIR}. Run day3-hunt.py validate (twice) first.")

    # Load pass-1 / pass-2 / pass-3 findings per angle
    findings_by_pass: dict[str, dict[int, list[dict]]] = {
        angle: {p: load_findings(angle, p) for p in PASSES} for angle in ANGLES
    }

    # Quick sanity check
    missing = []
    for angle in ANGLES:
        for p in PASSES:
            f = HUNT_DIR / angle / f"findings-pass{p}.yaml"
            if not f.exists():
                missing.append(str(f))
    if missing:
        sys.exit(f"FAIL: missing pass files:\n  " + "\n  ".join(missing))

    # Process every scaffold. P1-15: track WHY drops happened, not just how many.
    scaffolds: list[dict] = []
    drop_reasons: dict[str, int] = {}  # reason → count (combined p2 + p3)
    for scaffold_path in sorted(VALIDATION_DIR.glob("*.yaml")):
        try:
            scaffold = yaml.safe_load(scaffold_path.read_text()) or {}
        except yaml.YAMLError as e:
            print(f"[report] WARN parse error in {scaffold_path}: {e}", file=sys.stderr)
            continue

        angle = scaffold.get("angle")
        if angle not in ANGLES:
            print(f"[report] WARN unknown angle in {scaffold_path}: {angle}", file=sys.stderr)
            continue

        target = {
            "location": scaffold.get("location", ""),
            "summary": scaffold.get("summary", ""),
        }
        p2_match, p2_reason = find_match_with_diag(target, findings_by_pass[angle][2])
        p3_match, p3_reason = find_match_with_diag(target, findings_by_pass[angle][3])
        # Aggregate non-match reasons for diagnostic surfacing
        if p2_reason != "match":
            drop_reasons[f"pass-2: {p2_reason}"] = drop_reasons.get(f"pass-2: {p2_reason}", 0) + 1
        if p3_reason != "match":
            drop_reasons[f"pass-3: {p3_reason}"] = drop_reasons.get(f"pass-3: {p3_reason}", 0) + 1
        updated = update_scaffold_with_self_consistency(scaffold_path, p2_match, p3_match)
        # Stash reasons on the scaffold for the report renderer below.
        updated.setdefault("self_consistency", {})["_p2_reason"] = p2_reason
        updated.setdefault("self_consistency", {})["_p3_reason"] = p3_reason
        scaffolds.append(updated)

    # Aggregate
    total = len(scaffolds)
    survived = [s for s in scaffolds if s.get("self_consistency", {}).get("survived")]
    dropped_sc = [s for s in scaffolds if not s.get("self_consistency", {}).get("survived")]

    status_buckets = {"validated": [], "unreproducible": [], "dupe": [],
                      "false-positive": [], "failed-self-consistency": [], "pending": []}
    for s in scaffolds:
        st = s.get("final_status") or "pending"
        status_buckets.setdefault(st, []).append(s)

    n_validated_final = len(status_buckets["validated"])

    # ---- gate evaluation (from phase-0-scope.md §2) ----
    # "Pass" = >=1 finding that is (a) novel (status=validated, dedup says not dupe),
    # (b) reproducible (reproducer gate=pass), (c) self-consistency survived (>=2/3),
    # (d) human-judged would-have-taken->2h-unaided (we cannot auto-judge that).
    auto_pass_candidates = []
    for s in status_buckets["validated"]:
        repro = (s.get("gates", {}) or {}).get("reproducer", {}) or {}
        dedup = (s.get("gates", {}) or {}).get("dedup", {}) or {}
        sc = s.get("self_consistency", {}) or {}
        if repro.get("status") == "pass" and dedup.get("is_duplicate") is False and sc.get("survived"):
            auto_pass_candidates.append(s)

    gate_auto_pass = len(auto_pass_candidates) >= 1

    # P1-18: read gt_2h_unaided from dataset.yaml (human-fills this field per entry).
    # If any validated candidate has gt_2h_unaided=true, gate criterion (d) PASSES auto.
    dataset_path = PROJECT_ROOT / "cell-1" / "backtest" / "dataset.yaml"
    gt_2h_judgments: dict[str, bool | None] = {}
    if dataset_path.exists():
        try:
            ds_doc = yaml.safe_load(dataset_path.read_text()) or {}
            for e in (ds_doc.get("dataset") or []):
                gt_2h_judgments[str(e.get("issue", ""))] = e.get("gt_2h_unaided")
        except yaml.YAMLError:
            pass
    n_gt_2h_true = sum(1 for v in gt_2h_judgments.values() if v is True)
    n_gt_2h_false = sum(1 for v in gt_2h_judgments.values() if v is False)
    n_gt_2h_unjudged = sum(1 for v in gt_2h_judgments.values() if v is None)

    # ---- write the final report ----
    md: list[str] = []
    md.append("# Cell #1 Final Report — Jackson-databind x correctness\n\n")
    md.append("**Phase:** 0  ·  **Cell:** #1  ·  **Owner:** mshaikh@guidewire.com\n\n")
    md.append(f"**Generated:** $(re-run `python3 scripts/day4-finalize.py report` to refresh)\n\n")
    md.append("**Source artifacts:**\n")
    md.append("- Recon: [cell-1/recon/cell-1-recon.md](recon/cell-1-recon.md)\n")
    md.append("- Backtest: [cell-1/cell-1-backtest.md](cell-1-backtest.md)\n")
    md.append("- Pass-1 candidates: [cell-1/cell-1-candidates-pass1.md](cell-1-candidates-pass1.md)\n")
    md.append("- Validation scaffolds: [cell-1/hunt/validation/](hunt/validation/)\n\n")

    md.append("---\n\n## TL;DR\n\n")
    decision_auto = "**PASS** (auto-checks)" if gate_auto_pass else "**FAIL** (auto-checks)"
    md.append(f"- Cell #1 auto-gate: {decision_auto}\n")
    md.append(f"- Final validated findings (after self-consistency): **{n_validated_final}** "
              f"(of {total} pass-1 candidates, {len(survived)} survived self-consistency)\n")
    md.append(f"- Auto-pass candidates (status=validated AND repro=pass AND not-dupe AND self-consistent): "
              f"**{len(auto_pass_candidates)}**\n")
    md.append(f"- Full pass requires a HUMAN judgment that >=1 of those would have taken >2h unaided "
              f"(see scope §2 success criteria) — fill in below.\n\n")

    md.append("---\n\n## Stats\n\n")
    md.append("| Metric | Value |\n|---|---|\n")
    md.append(f"| Pass-1 candidates total | {total} |\n")
    md.append(f"| Code-quality candidates | {sum(1 for s in scaffolds if s.get('angle') == 'code-quality')} |\n")
    md.append(f"| Edge-case candidates | {sum(1 for s in scaffolds if s.get('angle') == 'edge-case')} |\n")
    md.append(f"| Survived self-consistency (>=2/3) | {len(survived)} |\n")
    md.append(f"| Dropped by self-consistency (1/3 only) | {len(dropped_sc)} |\n")
    md.append(f"| Final status = validated | {n_validated_final} |\n")
    md.append(f"| Final status = unreproducible | {len(status_buckets['unreproducible'])} |\n")
    md.append(f"| Final status = dupe | {len(status_buckets['dupe'])} |\n")
    md.append(f"| Final status = false-positive | {len(status_buckets['false-positive'])} |\n")
    md.append(f"| Final status = failed-self-consistency | {len(status_buckets['failed-self-consistency'])} |\n")
    md.append(f"| Final status = pending | {len(status_buckets['pending'])} |\n\n")

    # P1-15: surface self-consistency matcher drop-rate breakdown so humans can
    # see whether drops are "agent didn't reproduce in another pass" (legit) or
    # "matcher's same-file + 2-keyword threshold was too strict" (matcher bug).
    if drop_reasons:
        md.append("### Self-consistency matcher drops (P1-15 diagnostic)\n\n")
        md.append("Pass-2 + pass-3 lookups that returned no match, by reason. Multiple "
                  "entries per pass-1 candidate possible (one per pass that didn't match). "
                  "If `same_file_overlap_le_1` dominates, the 2-keyword threshold may be too "
                  "strict; if `no_same_file_candidate` dominates, the re-run found entirely "
                  "different bugs (likely a real signal-vs-noise issue).\n\n")
        md.append("| Reason | Count |\n|---|---|\n")
        for reason in sorted(drop_reasons.keys()):
            md.append(f"| `{reason}` | {drop_reasons[reason]} |\n")
        md.append("\n")

    md.append("---\n\n## Validated findings (survived self-consistency)\n\n")
    if auto_pass_candidates:
        md.append("| ID | Angle | Location | Summary | CWE | Self-cons. |\n")
        md.append("|---|---|---|---|---|---|\n")
        for s in auto_pass_candidates:
            sc = s.get("self_consistency", {}) or {}
            cwe = ((s.get("gates", {}) or {}).get("cwe", {}) or {}).get("cwe", "—")
            md.append(f"| `{s.get('finding_id', '?')}` | {s.get('angle', '?')} | "
                      f"`{s.get('location', '?')}` | {(s.get('summary') or '')[:80]} | "
                      f"{cwe} | {sc.get('agreement', '?')} |\n")
    else:
        md.append("_No candidates passed all auto-checks._\n")

    md.append("\n---\n\n## Dropped by self-consistency (1-of-3 contexts only)\n\n")
    if dropped_sc:
        md.append("| ID | Angle | Location | Summary | Pass-1 status |\n")
        md.append("|---|---|---|---|---|\n")
        for s in dropped_sc:
            md.append(f"| `{s.get('finding_id', '?')}` | {s.get('angle', '?')} | "
                      f"`{s.get('location', '?')}` | {(s.get('summary') or '')[:80]} | "
                      f"{s.get('final_status', '?')} |\n")
    else:
        md.append("_None — every pass-1 candidate survived self-consistency._\n")
    md.append("\n_If you disagree with any of these drops (e.g., the matcher was too strict on summary "
              "keywords), edit the corresponding `cell-1/hunt/validation/<id>.yaml` scaffold's "
              "`self_consistency.survived` field manually and re-run report._\n")

    md.append("\n---\n\n## Other rejected findings (full breakdown)\n\n")
    for st in ("unreproducible", "dupe", "false-positive", "failed-self-consistency", "pending"):
        items = status_buckets[st]
        if not items:
            continue
        md.append(f"### {st} ({len(items)})\n\n")
        for s in items:
            md.append(f"- `{s.get('finding_id', '?')}` ({s.get('angle', '?')}) — "
                      f"`{s.get('location', '?')}`: {(s.get('summary') or '')[:100]}\n")
        md.append("\n")

    md.append("---\n\n## Auto-gate breakdown\n\n")
    md.append("Per phase-0-scope.md §2 (Success criteria for Cell #1):\n\n")
    md.append("| Gate | Auto-check | Status |\n|---|---|---|\n")
    md.append(f"| (a) novel | status=validated AND dedup.is_duplicate=false | "
              f"{'PASS' if any((s.get('gates') or {}).get('dedup', {}).get('is_duplicate') is False for s in status_buckets['validated']) else 'FAIL'} |\n")
    md.append(f"| (b) reproducible | gates.reproducer.status=pass | "
              f"{'PASS' if any((s.get('gates') or {}).get('reproducer', {}).get('status') == 'pass' for s in status_buckets['validated']) else 'FAIL'} |\n")
    md.append(f"| (c) self-consistent | appears in >=2/3 contexts | "
              f"{'PASS' if any((s.get('self_consistency') or {}).get('survived') for s in status_buckets['validated']) else 'FAIL'} |\n")
    if n_gt_2h_true > 0:
        d_status = f"PASS ({n_gt_2h_true} entries judged >2h unaided)"
    elif n_gt_2h_unjudged > 0:
        d_status = f"_PARTIAL_ — {n_gt_2h_unjudged} entries unjudged, {n_gt_2h_false} judged ≤2h"
    elif n_gt_2h_false > 0:
        d_status = f"FAIL ({n_gt_2h_false} entries judged ≤2h, 0 >2h)"
    else:
        d_status = "_HUMAN JUDGMENT REQUIRED_ — fill `gt_2h_unaided` per entry in dataset.yaml"
    md.append(f"| (d) would have taken >2h unaided | dataset.yaml `gt_2h_unaided` field | {d_status} |\n\n")

    md.append("---\n\n## Kill-criteria check\n\n")
    md.append("Per phase-0-scope.md §2 (Kill criteria for Cell #1):\n\n")
    all_baseline_dupes = total > 0 and len(status_buckets["dupe"]) == total
    no_repros = total > 0 and not any(
        (s.get("gates") or {}).get("reproducer", {}).get("status") == "pass" for s in scaffolds
    )
    md.append(f"- [{'x' if all_baseline_dupes else ' '}] All candidates are Semgrep/SpotBugs dupes → KILL\n")
    md.append(f"- [{'x' if no_repros else ' '}] 0 reproducers ran successfully → KILL\n")
    md.append("- [ ] Token spend > $50 with no validated findings → KILL (fill cost section below)\n")
    md.append("- [ ] Engineer judges every finding to be slop → KILL (judgment call — fill below)\n\n")

    md.append("---\n\n## Cost (HUMAN: fill in)\n\n")
    md.append("- **Token spend (approx):** $___ (vs $25 cap)\n")
    md.append("- **Engineer hours:** ___ (vs ~14h planned across Days 1-4)\n")
    md.append("- **Within budget?** yes / no\n\n")

    md.append("---\n\n## Lessons learned (HUMAN: fill in)\n\n")
    md.append("- What worked:\n  - \n")
    md.append("- What didn't:\n  - \n")
    md.append("- What I'd change about the loop:\n  - \n")
    md.append("- Surprises:\n  - \n\n")

    md.append("---\n\n## Recommendation (HUMAN: fill in)\n\n")
    md.append("_Pick one:_\n\n")
    md.append("- [ ] **Proceed to Cell #2** (Drools x correctness). Auto-gate passed AND human judges >=1 "
              "finding would have taken >2h unaided. Run cells in week 2 per scope §1.\n")
    md.append("- [ ] **Run kill-protocol retry cell** (per scope §6) — try Cell #2 OR Cell #3 with the same "
              "1-week budget before declaring the project dead.\n")
    md.append("- [ ] **Kill Phase 0** — no novel signal over free tools, or every finding is slop. "
              "Write post-mortem per scope §6.\n\n")

    md.append("---\n\n## Open questions\n\n")
    md.append("- (Auto) Did self-consistency feel too strict or too lenient? Edit "
              "`cell-1/hunt/validation/<id>.yaml::self_consistency.survived` if matches were missed; "
              "the matcher is coarse (same file + >=2 keyword overlap).\n")
    md.append("- (Auto) Were OSV/GitHub auto-dedup matches useful or noisy? Feedback shapes Phase 1's "
              "dedup design.\n")
    md.append("- (Human) Would you have found any of the validated findings in your normal review work? "
              "If yes, the agent's marginal value is lower than the recall numbers suggest.\n")
    md.append("- (Human) Reproducer-builder gap: how much time did writing each reproducer take? "
              "This is the critical Phase 1 deliverable.\n")

    FINAL_REPORT.write_text("".join(md))
    print(f"[report] final report → {FINAL_REPORT}", file=sys.stderr)
    print(f"[report] survived self-consistency: {len(survived)}/{total}", file=sys.stderr)
    print(f"[report] auto-pass candidates: {len(auto_pass_candidates)}", file=sys.stderr)
    print(f"[report] gate_auto_pass: {gate_auto_pass}")


# ---- main ----
def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="Create empty pass-2 and pass-3 stubs.")
    sub.add_parser("report", help="Self-consistency check + write final cell-1-report.md.")
    args = p.parse_args()

    if args.cmd == "prepare":
        cmd_prepare()
    elif args.cmd == "report":
        cmd_report()


if __name__ == "__main__":
    main()
