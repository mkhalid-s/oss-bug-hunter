#!/usr/bin/env python3
"""Day 2 backtest dataset-builder for Cell #1 (Jackson-databind x correctness).

Reads cell-1/recon/closed-bugs.json (produced by scripts/day1-recon.sh).
For each closed bug:
  1. Find the fix commit in the local clone (grep commit messages/bodies for "#NNNN")
  2. Compute parent-commit-sha (the "pre-fix" snapshot for backtesting)
  3. Compute files-touched and lines-changed
  4. Apply correctness/feature/security keyword heuristics
  5. Score as a backtest candidate; rank

Outputs:
  cell-1/backtest/candidates.yaml      — top 30 ranked candidates (full metadata)
  cell-1/backtest/dataset-autopick.yaml — top 10 in dataset schema (human fills 2 fields)

The human reviews candidates.yaml, either accepts autopick or hand-picks 10, then sets
'expected_subagent' (code-quality | edge-case) and 'notes' for each entry, saving the
final list as cell-1/backtest/dataset.yaml.

See phase-0-scope.md §3 for context.

Dependencies: python3, PyYAML (pip install pyyaml).
Run after scripts/day1-recon.sh has populated cell-1/recon/.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
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

# ---- classification heuristics ----
CORRECTNESS_KEYWORDS = re.compile(
    # P0-10 fix: append \w* to identifier-like tokens so NullPointerException,
    # ClassCastException, StackOverflowError, etc. match. Previously the trailing
    # \b excluded any extension of the bare keyword.
    r"\b(NPE|NullPointer\w*|regression|wrong|incorrect|returns? \w+ instead|"
    r"fails?|throws|ClassCast\w*|StackOverflow\w*|infinite loop|deadlock|"
    r"missing|ignored|not honored|not respected|breaks?|broken|"
    r"unexpected|inconsistent|race|off.by.one)\b",
    re.IGNORECASE,
)

FEATURE_KEYWORDS = re.compile(
    r"\b(add support|support for|allow \w+ to|introduce|new (option|feature|setting|module|annotation)|"
    r"propose|proposal|enhancement|enable \w+)\b",
    re.IGNORECASE,
)

SECURITY_KEYWORDS = re.compile(
    r"\b(CVE|RCE|deserialization gadget|XSS|SSRF|authentication|authorization|"
    r"credential|crypto|sandbox escape|polymorphic.*RCE|untrusted)\b",
    re.IGNORECASE,
)

FIX_VERB = re.compile(
    r"\b(fix|fixes|fixed|close|closes|closed|resolve|resolves|resolved)\b",
    re.IGNORECASE,
)


# ---- git helpers ----
def pinned_tag() -> str | None:
    """Read the pinned tag from cell-1/recon/target-pin.json. Returns 'jackson-databind-2.21.3' or similar."""
    pin_path = RECON_DIR / "target-pin.json"
    if not pin_path.exists():
        return None
    try:
        return json.loads(pin_path.read_text()).get("tag")
    except (json.JSONDecodeError, OSError):
        return None


def is_in_pinned_history(sha: str, tag: str) -> bool:
    """True iff `sha` is an ancestor of `tag` — i.e. the commit is in the pinned tree's history.

    P0-4 fix: jackson-databind has 2.x (LTS) and 3.x (master) parallel branches. Fixes
    landed only on 3.x must be skipped when the pin is 2.x — otherwise backtest worktrees
    end up referencing 3.x file paths (e.g. `src/main/java/tools/jackson/...`) that don't
    exist in the 2.x tree (`src/main/java/com/fasterxml/jackson/...`).
    """
    r = subprocess.run(
        ["git", "-C", str(TARGET_DIR), "merge-base", "--is-ancestor", sha, tag],
        capture_output=True,
    )
    return r.returncode == 0


def git(*args: str, default: str = "") -> str:
    """Run a git command against TARGET_DIR. Return stdout or `default` on failure."""
    r = subprocess.run(
        ["git", "-C", str(TARGET_DIR), *args],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else default


def find_fix_commit(issue_num: int, issue_created_at: str | None) -> tuple[str, str] | None:
    """Locate the fix commit for an issue. Returns (sha, subject) or None.

    Strategy: broad `--grep=#N`, then in Python require word-boundary match on #N
    and prefer commits whose subject contains fix/close/resolve verbs. Excludes
    commits authored *before* the issue was created (false matches).
    """
    sha_list = git("log", "--all", "--no-merges",
                   f"--grep=#{issue_num}", "--pretty=format:%H").strip()
    if not sha_list:
        return None

    issue_ref = re.compile(rf"#{issue_num}\b")
    matches: list[tuple[str, str, str]] = []  # (sha, subject, commit_iso_date)
    for sha in sha_list.splitlines():
        sha = sha.strip()
        if not sha:
            continue
        info = git("show", "-s", "--format=%s%x1f%b%x1f%cI", sha)
        if not info:
            continue
        parts = info.split("\x1f", 2)
        if len(parts) < 3:
            continue
        subject, body, commit_date = parts[0], parts[1], parts[2].strip()
        if not (issue_ref.search(subject) or issue_ref.search(body)):
            continue
        # Sanity: drop commits authored before the issue existed.
        # P1-6 fix: parse to datetime before comparing. Previously this used a
        # lexical string comparison, which is wrong across `Z` vs `+00:00`
        # suffixes (same instant, but `+` sorts before `Z` so equal timestamps
        # comparing the +00:00 form against the Z form were falsely "earlier").
        if issue_created_at and commit_date:
            try:
                ic = _dt.datetime.fromisoformat(issue_created_at.replace("Z", "+00:00"))
                cd = _dt.datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
                if cd < ic:
                    continue
            except ValueError:
                # Malformed timestamp on either side — fall back to lexical compare
                # (preserves original behavior rather than silently accepting all).
                if commit_date < issue_created_at:
                    continue
        matches.append((sha, subject, commit_date))

    if not matches:
        return None

    # Prefer commits with explicit fix/close/resolve verbs
    for sha, subject, _ in matches:
        if FIX_VERB.search(subject):
            return sha, subject
    return matches[0][0], matches[0][1]


def commit_stats(sha: str) -> tuple[int, int, int, list[str]]:
    """Return (n_files_changed, lines_added, lines_removed, files_touched)."""
    files_raw = git("show", "--name-only", "--format=", sha).strip()
    files = [f for f in files_raw.splitlines() if f]
    numstat = git("show", "--numstat", "--format=", sha)
    added = removed = 0
    for line in numstat.splitlines():
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        try:
            added += int(cols[0])
            removed += int(cols[1])
        except ValueError:
            continue  # binary file shows "-"
    return len(files), added, removed, files


# ---- scoring ----
def classify(title: str, body: str) -> dict:
    text = f"{title}\n{body[:2000]}"
    return {
        "correctness": bool(CORRECTNESS_KEYWORDS.search(text)),
        "feature": bool(FEATURE_KEYWORDS.search(text)),
        "security": bool(SECURITY_KEYWORDS.search(text)),
        "has_code_fence": "```" in body,
    }


def score(c: dict) -> float:
    s = 0.0
    if c["correctness"]: s += 3
    if c["feature"]:     s -= 5
    if c["security"]:    s -= 2
    if c["has_code_fence"]: s += 2

    f = c["files_changed"]
    if 1 <= f <= 5: s += 2
    elif f > 20:    s -= 5
    elif f > 10:    s -= 2

    lines = c["lines_added"] + c["lines_removed"]
    if 5 <= lines <= 100: s += 1
    elif lines > 500:     s -= 3
    elif lines < 2:       s -= 2

    # bonus if any touched file is under the deserialization area
    if any(f.startswith("src/main/java/com/fasterxml/jackson/databind/deser/")
           or f.startswith("src/main/java/com/fasterxml/jackson/databind/jsontype/")
           for f in c["files_touched"]):
        s += 1.5

    return s


# ---- output ----
HEADER_CANDIDATES = """\
# Backtest candidate pool — top {n}, ranked by heuristic score.
# Source: scripts/day2-build-dataset.py
# Pick 10 (or accept dataset-autopick.yaml) and finalize as dataset.yaml.

"""

HEADER_AUTOPICK = """\
# Auto-picked top 10 in dataset schema.
# Review each entry. For each, fill in:
#   expected_subagent — one of: code-quality, edge-case
#   notes             — one line on why this bug is interesting to backtest
#   gt_2h_unaided     — true | false | null (P1-18; only human-judged Phase-0 gate input
#                       remaining post-P0-1). "Would the engineer have taken >2h to find
#                       this unaided?" — null = not yet judged.
# Then save as cell-1/backtest/dataset.yaml.

"""


def write_candidates(candidates: list[dict], path: Path) -> None:
    path.write_text(
        HEADER_CANDIDATES.format(n=len(candidates))
        + yaml.safe_dump({"candidates": candidates}, sort_keys=False, default_flow_style=False, width=120)
    )


def write_autopick(candidates: list[dict], path: Path) -> None:
    dataset = []
    for c in candidates[:10]:
        dataset.append({
            "issue": c["issue_num"],
            "title": c["title"],
            "url": c["url"],
            "fix_commit": c["fix_commit"],
            "parent_commit": c["parent_commit"],
            "files_touched": c["files_touched"][:5],
            "expected_subagent": "",   # human fills: code-quality | edge-case
            "notes": "",               # human fills
            "gt_2h_unaided": None,     # P1-18: human fills after reviewing fix — true/false
        })
    path.write_text(
        HEADER_AUTOPICK
        + yaml.safe_dump({"dataset": dataset}, sort_keys=False, default_flow_style=False, width=120)
    )


# ---- main ----
def main() -> None:
    if not (TARGET_DIR / ".git").is_dir():
        sys.exit(f"FAIL: target not cloned at {TARGET_DIR}. Run scripts/day1-recon.sh first.")
    bugs_path = RECON_DIR / "closed-bugs.json"
    if not bugs_path.exists():
        sys.exit(f"FAIL: {bugs_path} missing. Run scripts/day1-recon.sh first.")

    bugs = json.loads(bugs_path.read_text())
    print(f"[backtest] loaded {len(bugs)} closed bugs", file=sys.stderr)

    # P0-4: filter candidates to the pinned branch. Otherwise 3.x fixes end up
    # in a 2.x backtest and worktrees can't find the referenced files.
    pin = pinned_tag()
    if pin is None:
        sys.exit(f"FAIL: cannot read target-pin.json from {RECON_DIR}. Run day1-recon.sh first.")
    # Verify the tag exists in the local clone
    r = subprocess.run(["git", "-C", str(TARGET_DIR), "rev-parse", "--verify", f"{pin}^{{commit}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAIL: pinned tag {pin} not found in clone. `git -C {TARGET_DIR} fetch --tags`?")
    print(f"[backtest] pinned tag: {pin}; filtering candidates to that branch", file=sys.stderr)

    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

    candidates: list[dict] = []
    skipped_no_fix = 0
    skipped_no_src = 0
    skipped_off_branch = 0

    for i, bug in enumerate(bugs):
        num = bug.get("number")
        if num is None:
            continue
        if i and i % 25 == 0:
            print(f"[backtest] processed {i}/{len(bugs)} ({len(candidates)} kept)",
                  file=sys.stderr)

        fix = find_fix_commit(num, bug.get("created_at"))
        if not fix:
            skipped_no_fix += 1
            continue
        fix_sha, fix_subject = fix

        # P0-4: drop fixes that aren't in the pinned tag's history.
        if not is_in_pinned_history(fix_sha, pin):
            skipped_off_branch += 1
            continue

        parent = git("rev-parse", f"{fix_sha}^").strip()
        if not parent:
            continue

        n_files, n_added, n_removed, files_touched = commit_stats(fix_sha)
        if n_files == 0:
            continue
        if not any(f.startswith("src/main/java/") for f in files_touched):
            skipped_no_src += 1
            continue

        flags = classify(bug.get("title") or "", bug.get("body") or "")
        cand = {
            "issue_num": num,
            "title": (bug.get("title") or "").strip(),
            "url": bug.get("html_url") or "",
            "fix_commit": fix_sha,
            "fix_subject": fix_subject.strip(),
            "parent_commit": parent,
            "files_changed": n_files,
            "lines_added": n_added,
            "lines_removed": n_removed,
            "files_touched": files_touched,
            **flags,
        }
        cand["score"] = round(score(cand), 2)
        candidates.append(cand)

    candidates.sort(key=lambda c: c["score"], reverse=True)
    print(f"[backtest] {len(candidates)} candidates kept "
          f"({skipped_no_fix} no fix-commit, {skipped_off_branch} off-branch (P0-4 filter), "
          f"{skipped_no_src} no src/main/java change)",
          file=sys.stderr)

    top30 = candidates[:30]
    write_candidates(top30, BACKTEST_DIR / "candidates.yaml")
    print(f"[backtest] wrote top {len(top30)} → {BACKTEST_DIR / 'candidates.yaml'}",
          file=sys.stderr)

    autopick_path = BACKTEST_DIR / "dataset-autopick.yaml"
    write_autopick(candidates, autopick_path)
    print(f"[backtest] wrote auto-picked dataset → {autopick_path}", file=sys.stderr)

    print()
    print("Next:")
    print(f"  1. Review {BACKTEST_DIR / 'candidates.yaml'} (top 30 with scores).")
    print(f"  2. Either accept the top 10 (cp dataset-autopick.yaml dataset.yaml) or")
    print(f"     hand-pick 10 from the candidates pool into dataset.yaml.")
    print(f"  3. For each entry in dataset.yaml, fill 'expected_subagent' (code-quality | edge-case)")
    print(f"     and 'notes' (one-line on what makes the bug interesting).")
    print(f"  4. Then drive the backtest runner (scripts/day2-run-backtest.py — not yet written).")


if __name__ == "__main__":
    main()
