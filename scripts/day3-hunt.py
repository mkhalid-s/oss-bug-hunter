#!/usr/bin/env python3
"""Day 3 novel-hunt orchestrator for Cell #1 (Jackson-databind x correctness).

Three subcommands:

  prepare         — Read cell-1/shortlist.txt; substitute into both prompt
                    templates (code-quality, edge-case); write per-angle
                    prompt.md + findings stubs.

  dedup <query>   — One-off lookup: extract keywords from a summary string,
                    query OSV (cached) + GitHub issues; print likely dupes.

  validate        — State-aware. If validation/ scaffolds don't exist, read
                    both findings-pass1.yaml files, auto-dedup each candidate,
                    write per-candidate validation scaffolds, exit. If
                    scaffolds exist, aggregate filled-in gates → write
                    cell-1/cell-1-candidates-pass1.md.

See scripts/day3-novel-hunt-prompts.md for prompt templates (canonical copies
are inlined in this script — keep both in sync).

Dependencies: python3, PyYAML, jackson-databind already cloned, gh CLI (optional;
without it, GitHub dedup is skipped).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML not installed. Run: pip install pyyaml")

# ---- paths ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # P0-2: relocatable
TARGET_DIR = PROJECT_ROOT / "targets" / "jackson-databind"
CELL_DIR = PROJECT_ROOT / "cell-1"
SHORTLIST_PATH = CELL_DIR / "shortlist.txt"
HUNT_DIR = CELL_DIR / "hunt"
VALIDATION_DIR = HUNT_DIR / "validation"
REPROS_DIR = HUNT_DIR / "repros"
REPORT_PATH = CELL_DIR / "cell-1-candidates-pass1.md"

# Reproducer-builder (WS3): the agent that writes a JUnit test demonstrating a
# finding lives behind this canonical prompt. run-repro.sh copies the produced
# .java into src/test/java/com/fasterxml/jackson/databind/repro/, so the package
# + FQCN are fixed by that convention.
REPRO_PROMPT_DOC = Path(__file__).resolve().parent / "repro-builder-prompt.md"
REPRO_PACKAGE = "com.fasterxml.jackson.databind.repro"
RUN_REPRO_SH = Path(__file__).resolve().parent / "run-repro.sh"

# Fix-builder (#4): the agent that proposes a patch which makes the reproducer
# flip green. The fix is validated non-AI by run-fix.sh (apply patch + re-run the
# reproducer test inside the Docker sandbox).
PATCHES_DIR = HUNT_DIR / "patches"
FIX_PROMPT_DOC = Path(__file__).resolve().parent / "fix-builder-prompt.md"
RUN_FIX_SH = Path(__file__).resolve().parent / "run-fix.sh"

ANGLES = ("code-quality", "edge-case")

# ---- prompt templates ----
# P1-14 fix (2026-05-19): canonical templates live in
# `scripts/day3-novel-hunt-prompts.md`. They're loaded at runtime and cached.
# This eliminates the duplication that previously had the same prompt body
# inlined as Python string constants and as fenced code blocks in the .md.
#
# Schema in the .md:
#   ## Template A — code-quality angle
#       ... (any prose) ...
#       ```
#       <prompt body, may contain nested fenced blocks>
#       ```
#   ## Template B — edge-case angle
#       (same structure)
#
# Loader (`_load_prompt_templates`): for each section, the prompt body is
# everything between the FIRST and LAST line that starts with ``` inside
# that section. Nested fences (e.g. inner ```yaml example) are preserved.

PROMPTS_DOC = Path(__file__).resolve().parent / "day3-novel-hunt-prompts.md"

_TEMPLATES_CACHE: dict[str, str] | None = None

_SECTION_HEADERS = {
    "code-quality": "## Template A — code-quality angle",
    "edge-case":    "## Template B — edge-case angle",
}


def _load_prompt_templates() -> dict[str, str]:
    """Parse the canonical prompt templates from PROMPTS_DOC. Cached after first call."""
    global _TEMPLATES_CACHE
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    if not PROMPTS_DOC.exists():
        raise FileNotFoundError(f"Prompt source not found: {PROMPTS_DOC}")
    text = PROMPTS_DOC.read_text()

    out: dict[str, str] = {}
    for angle, header in _SECTION_HEADERS.items():
        sec_start = text.find(header)
        if sec_start < 0:
            raise ValueError(f"Missing section {header!r} in {PROMPTS_DOC}")
        rest = text[sec_start + len(header):]
        # Section ends at next "## " heading, next "---" separator, or EOF.
        end_candidates = [i for i in (rest.find("\n## "), rest.find("\n---\n")) if i >= 0]
        sec_body = rest[:min(end_candidates)] if end_candidates else rest

        # All line-start ``` fence positions inside the section.
        fence_iter = list(re.finditer(r'(?m)^```', sec_body))
        if len(fence_iter) < 2:
            raise ValueError(
                f"Expected ≥2 fence lines in section {header!r}, got {len(fence_iter)}"
            )
        outer_open = fence_iter[0]
        outer_close = fence_iter[-1]
        # Body is from the line AFTER outer_open up to outer_close (exclusive).
        body_start = sec_body.find("\n", outer_open.end()) + 1
        body_end = outer_close.start()
        body = sec_body[body_start:body_end].rstrip("\n")
        if "{SHORTLIST_FILES_BLOCK}" not in body:
            raise ValueError(
                f"Template {angle!r} missing required {{SHORTLIST_FILES_BLOCK}} placeholder"
            )
        out[angle] = body

    _TEMPLATES_CACHE = out
    return out


def get_template(angle: str) -> str:
    """Return the (cached) prompt template for an angle. Raises KeyError if unknown."""
    return _load_prompt_templates()[angle]


# Legacy alias retained while the inlined Python constants are pruned from
# the rest of the file. `TEMPLATES["code-quality"]` still works after the
# Python constants below are removed.
class _TemplatesDictProxy:
    def __getitem__(self, k: str) -> str:
        return get_template(k)

    def __contains__(self, k: str) -> bool:
        return k in _SECTION_HEADERS


TEMPLATES = _TemplatesDictProxy()


# The Python string constants below are RETAINED but inert — kept as
# documentation of the prompt body for someone grepping for the text.
# Edits MUST be made in `day3-novel-hunt-prompts.md` (the canonical source).

_LEGACY_TEMPLATE_CODE_QUALITY_DOC = """\
You are reviewing Java code in the `jackson-databind` project for CORRECTNESS
bugs from a code-quality angle. This is a novel-hunt pass: you do not know
which (if any) of these files contains a bug.

Working directory: {{TARGET_DIR}}

Files to review (read each in full, plus callers/callees as needed):
{SHORTLIST_FILES_BLOCK}

# Find at most 5 distinct correctness bugs in the listed files

Focus areas (use as a checklist):
- NullPointerException risks on parameters or intermediate values that are
  reachable from public entry points without a guard
- Off-by-one errors in indexing, loop bounds, substring math, array slicing
- Wrong return values (empty where null expected, inverted boolean, wrong
  branch of a ternary, mis-cast)
- Dropped or swallowed exceptions (caught and ignored where the caller would
  reasonably expect propagation)
- Logic errors in control flow: unreachable branches, inverted conditions,
  fall-through where break was intended
- Incorrect generic-type handling: raw type leaks, unchecked casts that
  would ClassCastException under specific bindings, type erasure assumptions
- Concurrency: unsynchronized mutation of fields documented as shared

# Out of scope (do not report)

- Security (deserialization gadgets, authn/authz, crypto)
- Code style, naming, formatting
- Refactoring opportunities, dead code, redundant abstractions
- Performance, unless it's a correctness issue (StackOverflow, infinite loop)
- Missing tests, javadoc gaps

# Confidence bar

Only report findings where you would bet money that triggering the described
input causes the described wrong behavior. If unsure, OMIT. We would rather
miss real bugs than emit slop — slop here costs the user's reputation with
the Jackson maintainers.

# Constraints

- Do NOT consult: git history, release notes, issue tracker, OSV/GHSA, the
  internet. Reason only from the source code in the working directory.
- Each finding must cite a specific file:line-range, not "somewhere in X".
- Each finding must be derivable from the code shown in `evidence` alone -
  no "trust me, I traced it" arguments.
- Each `reproducer_hint` must be a concrete Java snippet a Jackson user could
  write (typically: `ObjectMapper.readValue(<input>, <type>)` or similar).

# Output format

Return a single YAML block at the end of your message. If you find nothing,
return `findings: []`.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<repo-relative-path:line-range>"
    type: "<NPE | off-by-one | wrong-return | dropped-exception | logic | generic-type | concurrency | other>"
    evidence: |
      <buggy code excerpt - 3-15 lines - followed by 1-2 sentences on why it's wrong>
    reproducer_hint: |
      <Java snippet, 1-5 lines>
```
"""

_LEGACY_TEMPLATE_EDGE_CASE_DOC = """\
You are reviewing Java code in the `jackson-databind` project for CORRECTNESS
bugs from an edge-case angle. This is a novel-hunt pass: you do not know
which (if any) of these files contains a bug.

Working directory: {{TARGET_DIR}}

Files to review (read each in full, plus callers/callees as needed):
{SHORTLIST_FILES_BLOCK}

# Find at most 5 distinct edge-case correctness bugs in the listed files

Edge-case categories to consider (use as a checklist - not all apply to every file):
- Timezone / DST: parsing/serializing dates around DST transitions, fixed
  offsets vs zone IDs, ZoneOffset.UTC vs ZoneId.of("UTC") equivalence
- Unicode: surrogate pairs, combining characters, BOM in input streams,
  non-BMP code points in string parsing
- Integer boundaries: MAX_VALUE+1 overflow, MIN_VALUE negation, unsigned-vs-signed
  in size/length math, overflow in size hint allocation
- Empty / singleton / deeply nested collections: empty arrays/maps where
  iteration assumed non-empty, single-element optimizations that break for size 0
- Locale-dependent formatting: Locale.getDefault() leaking into "should be
  invariant" code paths (decimal separator, case folding)
- Polymorphic-type resolution: subtype not yet loaded when parent deserializer
  runs, type id collision between equally-named classes in different packages
- Concurrent cache access: deserializer-cache reads during invalidation,
  double-construction of cached deserializers
- Recursive structures: cycles in object graphs, deep recursion hitting
  StackOverflowError before MAX_VALUE depth

# Out of scope (do not report)

- Security (deserialization gadgets, authn/authz, crypto)
- Code style, naming, formatting
- Refactoring opportunities, dead code, redundant abstractions
- Performance unless it's a correctness issue (StackOverflow, infinite loop)
- Generic NPE / off-by-one without an edge-case trigger (those are the
  code-quality angle's territory)

# Confidence bar

Only report findings where you would bet money that the described edge-case
input causes the described wrong behavior. If unsure, OMIT.

# Constraints

- Do NOT consult: git history, release notes, issue tracker, OSV/GHSA, the
  internet. Reason only from the source code in the working directory.
- Each finding must cite a specific file:line-range.
- Each finding must be derivable from the code shown in `evidence` alone.
- Each `reproducer_hint` must be a concrete Java snippet exhibiting the
  edge case (specific bad input value, specific call sequence).

# Output format

Return a single YAML block at the end of your message. If you find nothing,
return `findings: []`.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<repo-relative-path:line-range>"
    type: "<timezone | unicode | integer-overflow | empty-collection | locale | polymorphic | concurrent-cache | recursion | other>"
    evidence: |
      <buggy code excerpt + 1-2 sentences on the edge case>
    reproducer_hint: |
      <Java snippet, 1-5 lines, with the specific edge-case input>
```
"""

FINDINGS_STUB = """\
# AGENT-OUTPUT-NOT-YET-PASTED — delete this line when pasting
# Paste the agent's `findings:` YAML block from its final message here, then save.
# If the agent returned an empty list, delete the sentinel line above and leave
# `findings: []` below — that signals "agent found nothing" rather than "not run yet".

findings: []
"""


# ---- helpers ----
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import extract_keywords  # noqa: E402  shared with day4-finalize.py


def have_gh() -> bool:
    r = subprocess.run(["gh", "auth", "status"], capture_output=True)
    return r.returncode == 0


# ---- OSV ----
OSV_CACHE_PATH = HUNT_DIR / ".osv-cache.json"
OSV_TTL_SECONDS = 86400  # 24h


def fetch_osv() -> list[dict]:
    """Cached fetch of all OSV vulns for jackson-databind. Returns list[vuln]."""
    HUNT_DIR.mkdir(parents=True, exist_ok=True)
    if OSV_CACHE_PATH.exists() and (time.time() - OSV_CACHE_PATH.stat().st_mtime) < OSV_TTL_SECONDS:
        try:
            return json.loads(OSV_CACHE_PATH.read_text()).get("vulns", [])
        except (json.JSONDecodeError, OSError):
            pass

    body = json.dumps({
        "package": {
            "name": "com.fasterxml.jackson.core:jackson-databind",
            "ecosystem": "Maven",
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.osv.dev/v1/query",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        OSV_CACHE_PATH.write_text(json.dumps(data))
        return data.get("vulns", [])
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[dedup] WARN OSV fetch failed: {e}", file=sys.stderr)
        return []


def osv_keyword_matches(keywords: list[str], vulns: list[dict], limit: int = 5) -> list[dict]:
    """Score OSV entries by how many keywords appear in summary+details. Return top N."""
    if not keywords or not vulns:
        return []
    needles = [k.lower() for k in keywords]
    scored: list[tuple[int, dict]] = []
    for v in vulns:
        hay = ((v.get("summary") or "") + " " + (v.get("details") or "")).lower()
        hits = sum(1 for n in needles if n in hay)
        if hits >= 2:  # at least two keyword overlaps to be a match candidate
            scored.append((hits, v))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"id": v.get("id"), "summary": v.get("summary"), "score": s}
            for s, v in scored[:limit]]


def github_issue_matches(keywords: list[str], limit: int = 5) -> list[dict]:
    """gh search issues — returns top N matches in jackson-databind."""
    if not have_gh():
        return []
    if not keywords:
        return []
    q = " ".join(f'"{k}"' if k.isupper() and len(k) <= 4 else k for k in keywords[:4])
    r = subprocess.run(
        ["gh", "search", "issues", q,
         "--repo=FasterXML/jackson-databind",
         "--limit", str(limit),
         "--json", "title,url,state,number"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


# ---- subcommand: prepare ----
def cmd_prepare() -> None:
    if not SHORTLIST_PATH.exists():
        sys.exit(
            f"FAIL: {SHORTLIST_PATH} missing.\n"
            "Create it with one repo-relative file path per line "
            "(picked from Day 1 hot-spots + explore-inventory)."
        )
    files = [line.strip() for line in SHORTLIST_PATH.read_text().splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if not files:
        sys.exit(f"FAIL: {SHORTLIST_PATH} has no non-empty file paths.")
    if len(files) > 10:
        print(f"[prepare] WARN shortlist has {len(files)} files; recommend <=8 for prompt focus",
              file=sys.stderr)

    files_block = "\n".join(f"  - {f}" for f in files)
    HUNT_DIR.mkdir(parents=True, exist_ok=True)

    for angle in ANGLES:
        angle_dir = HUNT_DIR / angle
        angle_dir.mkdir(parents=True, exist_ok=True)
        # P1-14: use .replace() instead of .format() so any literal `{`/`}` in
        # the prompt body (e.g. future editorial changes) don't cause a
        # KeyError or silent format collision.
        prompt = TEMPLATES[angle].replace("{SHORTLIST_FILES_BLOCK}", files_block)
        # P0-2 / relocatability: the canonical .md template carries a
        # `{{TARGET_DIR}}` placeholder for the working-directory line; substitute
        # the actual target path (no hardcoded absolute path in the template).
        prompt = prompt.replace("{{TARGET_DIR}}", str(TARGET_DIR))
        (angle_dir / "prompt.md").write_text(prompt)
        findings_path = angle_dir / "findings-pass1.yaml"
        if not findings_path.exists():
            findings_path.write_text(FINDINGS_STUB)
        print(f"[prepare] wrote {angle_dir / 'prompt.md'}", file=sys.stderr)

    print()
    print("Next: in Claude Code, run each prompt against a fresh subagent.")
    print(f"  cat {HUNT_DIR / 'code-quality' / 'prompt.md'}  # then run via Agent tool")
    print(f"  cat {HUNT_DIR / 'edge-case'    / 'prompt.md'}  # then run via Agent tool")
    print()
    print("Paste each agent's findings YAML into the corresponding findings-pass1.yaml.")
    print("Then: python3 scripts/day3-hunt.py validate")


# ---- subcommand: dedup ----
def cmd_dedup(query: str) -> None:
    keywords = extract_keywords(query)
    if not keywords:
        sys.exit("FAIL: no keywords extracted from query.")
    print(f"keywords: {keywords}", file=sys.stderr)
    print()

    osv = osv_keyword_matches(keywords, fetch_osv())
    print("=== OSV matches ===")
    if osv:
        for m in osv:
            print(f"  [{m['score']} kw hits] {m['id']}: {m['summary']}")
    else:
        print("  (none)")
    print()

    gh = github_issue_matches(keywords)
    print("=== GitHub issue matches ===")
    if gh:
        for m in gh:
            print(f"  #{m['number']} ({m['state']}): {m['title']}")
            print(f"    {m['url']}")
    elif not have_gh():
        print("  (skipped — gh CLI not authenticated)")
    else:
        print("  (none)")


# ---- subcommand: validate ----
VALIDATION_SCAFFOLD_TEMPLATE = """\
# Validation scaffold for finding {finding_id} ({angle}).
#
# Auto-populated fields below are from the agent's pass-1 output + automated dedup.
# Human fills the 'gates:' block, then sets final_status.

finding_id: {finding_id}
angle: {angle}

# === from agent (do not edit) ===
summary: {summary_yaml}
location: {location_yaml}
type: {type_yaml}
evidence: |
{evidence_indented}
reproducer_hint: |
{reproducer_indented}

# === automated dedup (do not edit) ===
dedup_auto:
  osv_matches:
{osv_block}
  github_matches:
{github_block}

# === HUMAN FILLS BELOW ===
gates:
  reproducer:
    status: ""            # pass | fail | not-attempted
    path: ""              # cell-1/hunt/repros/{finding_id}.java
    notes: ""

  dedup:
    is_duplicate: null    # true | false
    references: []        # OSV ids or GitHub issue URLs that this dupes (if any)
    notes: ""

  cwe:
    cwe: ""               # CWE-XXX
    cvss: ""              # N/A is fine for correctness — record anyway
    notes: ""

  fix_passes_tests:
    status: ""            # pass | fail | not-attempted
    patch_path: ""        # cell-1/hunt/patches/{finding_id}.patch
    notes: ""

# Final status, set after all gates above are filled.
# One of: validated | unreproducible | dupe | false-positive | pending
final_status: pending
"""


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else pad for line in (text or "").splitlines())


def _yaml_scalar(value: str | None) -> str:
    """Format a single-line scalar safely as YAML (always double-quoted)."""
    if value is None:
        return '""'
    safe = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'


def _yaml_list_block(items: list[str], indent: int = 4) -> str:
    if not items:
        return " " * indent + "[]"
    pad = " " * indent
    return "\n".join(f"{pad}- {item}" for item in items)


def write_scaffolds(findings_by_angle: dict[str, list[dict]]) -> int:
    """Write one validation YAML per finding. Returns total scaffold count."""
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    vulns = fetch_osv()
    n_scaffolds = 0

    for angle, findings in findings_by_angle.items():
        prefix = "cq" if angle == "code-quality" else "ec"
        for i, f in enumerate(findings, start=1):
            finding_id = f"{prefix}-{i}"
            scaffold_path = VALIDATION_DIR / f"{finding_id}.yaml"
            if scaffold_path.exists():
                # Don't clobber human-filled scaffolds
                continue

            keywords = extract_keywords(f.get("summary", ""))
            osv_matches = osv_keyword_matches(keywords, vulns)
            gh_matches = github_issue_matches(keywords)

            osv_lines = []
            for m in osv_matches:
                osv_lines.append(f'    - id: "{m["id"]}"')
                osv_lines.append(f'      summary: {_yaml_scalar(m["summary"])}')
                osv_lines.append(f'      score: {m["score"]}')
            osv_block = "\n".join(osv_lines) if osv_lines else "    []"

            gh_lines = []
            for m in gh_matches:
                gh_lines.append(f'    - number: {m["number"]}')
                gh_lines.append(f'      state: "{m["state"]}"')
                gh_lines.append(f'      title: {_yaml_scalar(m["title"])}')
                gh_lines.append(f'      url: "{m["url"]}"')
            gh_block = "\n".join(gh_lines) if gh_lines else "    []"

            scaffold_path.write_text(
                VALIDATION_SCAFFOLD_TEMPLATE.format(
                    finding_id=finding_id,
                    angle=angle,
                    summary_yaml=_yaml_scalar(f.get("summary", "")),
                    location_yaml=_yaml_scalar(f.get("location", "")),
                    type_yaml=_yaml_scalar(f.get("type", "")),
                    evidence_indented=_indent(f.get("evidence", "") or "", 2),
                    reproducer_indented=_indent(f.get("reproducer_hint", "") or "", 2),
                    osv_block=osv_block,
                    github_block=gh_block,
                )
            )
            n_scaffolds += 1
            print(f"[validate] scaffold → {scaffold_path}", file=sys.stderr)

    return n_scaffolds


def load_findings_by_angle() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for angle in ANGLES:
        path = HUNT_DIR / angle / "findings-pass1.yaml"
        if not path.exists():
            sys.exit(f"FAIL: {path} missing. Run `prepare` first.")
        data = yaml.safe_load(path.read_text()) or {}
        findings = data.get("findings") or []
        if not isinstance(findings, list):
            sys.exit(f"FAIL: {path}: 'findings' must be a list, got {type(findings).__name__}")
        out[angle] = findings
    return out


def cmd_validate() -> None:
    findings_by_angle = load_findings_by_angle()
    total_findings = sum(len(v) for v in findings_by_angle.values())
    if total_findings == 0:
        sys.exit("FAIL: 0 findings across both angles. Paste agent output into findings-pass1.yaml.")

    scaffolds_exist = VALIDATION_DIR.is_dir() and any(VALIDATION_DIR.glob("*.yaml"))

    if not scaffolds_exist:
        n = write_scaffolds(findings_by_angle)
        # State sentinel for the day3-scaffolds pipeline step. Created here (not
        # only by the Makefile rule) so the CLI/dashboard/MCP path completes the
        # step too — pipeline.py checks for this file's existence.
        (VALIDATION_DIR / ".scaffolds-generated").touch()
        print()
        print(f"Wrote {n} validation scaffolds to {VALIDATION_DIR}/")
        print(f"Total findings: {total_findings} (code-quality: {len(findings_by_angle['code-quality'])}, "
              f"edge-case: {len(findings_by_angle['edge-case'])})")
        print()
        print("Next, for each scaffold:")
        print("  1. Read finding's evidence + reproducer_hint")
        print("  2. Write reproducer JUnit test → cell-1/hunt/repros/<id>.java; run; fill `reproducer` gate")
        print("  3. Review auto-dedup OSV/GitHub matches; fill `dedup` gate")
        print("  4. Assign CWE; fill `cwe` gate")
        print("  5. Write suggested fix as patch; run `mvn test` in target/; fill `fix_passes_tests` gate")
        print("  6. Set final_status (validated | unreproducible | dupe | false-positive)")
        print()
        print("Then re-run: python3 scripts/day3-hunt.py validate")
        return

    # Scaffolds exist — aggregate into the report. Ensure the state sentinel is
    # present (e.g. scaffolds written by an earlier run before this fix).
    (VALIDATION_DIR / ".scaffolds-generated").touch()
    scaffolds: list[dict] = []
    for p in sorted(VALIDATION_DIR.glob("*.yaml")):
        try:
            scaffolds.append(yaml.safe_load(p.read_text()))
        except yaml.YAMLError as e:
            print(f"[validate] WARN failed to parse {p}: {e}", file=sys.stderr)
            continue

    status_counts = {"validated": 0, "unreproducible": 0, "dupe": 0,
                     "false-positive": 0, "pending": 0}
    for s in scaffolds:
        st = (s or {}).get("final_status") or "pending"
        status_counts[st] = status_counts.get(st, 0) + 1

    md: list[str] = []
    md.append("# Cell #1 Day 3 Pass-1 Candidates\n\n")
    md.append(f"**Total candidates:** {len(scaffolds)}  ")
    md.append(f"(code-quality: {sum(1 for s in scaffolds if s.get('angle') == 'code-quality')}, "
              f"edge-case: {sum(1 for s in scaffolds if s.get('angle') == 'edge-case')})\n\n")

    md.append("## Status rollup\n\n")
    md.append("| Status | Count |\n|---|---|\n")
    for st in ("validated", "unreproducible", "dupe", "false-positive", "pending"):
        md.append(f"| {st} | {status_counts.get(st, 0)} |\n")

    md.append("\n## Per-candidate table\n\n")
    md.append("| ID | Angle | Status | Summary | Location | OSV/GH dupes |\n")
    md.append("|---|---|---|---|---|---|\n")
    for s in scaffolds:
        if not s:
            continue
        fid = s.get("finding_id", "?")
        angle = s.get("angle", "?")
        status = s.get("final_status", "pending")
        summary = (s.get("summary") or "")[:80]
        location = s.get("location") or "?"
        dedup_auto = s.get("dedup_auto") or {}
        n_osv = len(dedup_auto.get("osv_matches") or [])
        n_gh = len(dedup_auto.get("github_matches") or [])
        dedup_info = f"OSV:{n_osv} GH:{n_gh}"
        md.append(f"| `{fid}` | {angle} | {status} | {summary} | `{location}` | {dedup_info} |\n")

    md.append("\n## Pending work\n\n")
    pending = [s for s in scaffolds if (s or {}).get("final_status", "pending") == "pending"]
    if pending:
        for s in pending:
            md.append(f"- `{s['finding_id']}` — gates not yet filled\n")
    else:
        md.append("All candidates have a final_status set.\n")

    md.append("\n## Notes\n\n")
    md.append("- This is Pass 1. Day 4 re-runs the same prompts in fresh contexts; "
              "candidates that don't reappear in 2-of-3 contexts get dropped (self-consistency).\n")
    md.append("- Status `pending` means the human hasn't filled the validation gates yet — "
              "see corresponding YAML under `cell-1/hunt/validation/`.\n")
    md.append("- OSV/GH dedup counts above are *auto-suggested matches*, not confirmed duplicates. "
              "The human reviews each in the scaffold's `gates.dedup` section.\n")

    REPORT_PATH.write_text("".join(md))
    print(f"[validate] report → {REPORT_PATH}", file=sys.stderr)
    print(f"[validate] status counts: {status_counts}", file=sys.stderr)


# ---- reproducer-builder (WS3) ----
_REPRO_TEMPLATE_CACHE: str | None = None


def repro_class_name(finding_id: str) -> str:
    """Java-legal class name for a finding id (cq-1 -> Repro_cq_1)."""
    safe = re.sub(r"[^0-9A-Za-z]", "_", finding_id)
    return f"Repro_{safe}"


def repro_fqcn(finding_id: str) -> str:
    """Fully-qualified test class name for `mvn -Dtest=` / run-repro.sh."""
    return f"{REPRO_PACKAGE}.{repro_class_name(finding_id)}"


def _load_repro_template() -> str:
    """Load the canonical reproducer prompt body (everything after the '---').

    Cached. The file's intro (up to and including the first '---' line) is
    documentation; the prompt body is everything after it.
    """
    global _REPRO_TEMPLATE_CACHE
    if _REPRO_TEMPLATE_CACHE is not None:
        return _REPRO_TEMPLATE_CACHE
    if not REPRO_PROMPT_DOC.exists():
        sys.exit(f"FAIL: {REPRO_PROMPT_DOC} missing.")
    raw = REPRO_PROMPT_DOC.read_text()
    parts = raw.split("\n---\n", 1)
    _REPRO_TEMPLATE_CACHE = (parts[1] if len(parts) == 2 else raw).strip() + "\n"
    return _REPRO_TEMPLATE_CACHE


def build_repro_prompt(scaffold: dict) -> str:
    """Substitute a validation scaffold's finding fields into the repro prompt.

    Pure: takes the loaded scaffold dict, returns the prompt string. Uses token
    replacement (not str.format) because the template / evidence may contain
    literal braces from Java/code snippets.
    """
    fid = str(scaffold.get("finding_id", "")).strip()
    tokens = {
        "{{TARGET_DIR}}": str(TARGET_DIR),
        "{{FINDING_ID}}": fid,
        "{{CLASS_NAME}}": repro_class_name(fid),
        "{{SUMMARY}}": (scaffold.get("summary") or "").strip(),
        "{{LOCATION}}": (scaffold.get("location") or "").strip(),
        "{{TYPE}}": (scaffold.get("type") or "").strip(),
        "{{EVIDENCE}}": _indent((scaffold.get("evidence") or "").rstrip(), 4),
        "{{REPRODUCER_HINT}}": _indent((scaffold.get("reproducer_hint") or "").rstrip(), 4),
    }
    body = _load_repro_template()
    for k, v in tokens.items():
        body = body.replace(k, v)
    return body


def extract_java_block(text: str) -> str | None:
    """Return the last ```java fenced block's contents, or None if absent.

    Mirrors claude_driver.extract_yaml_block's fenced-only discipline: we never
    guess at unfenced output. Prefers ```java; falls back to a bare ``` block
    that looks like Java (declares a package or class).
    """
    blocks = re.findall(r"```java\s*\n(.*?)\n```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    for blk in reversed(re.findall(r"```\s*\n(.*?)\n```", text, re.DOTALL)):
        if re.search(r"\b(package|class)\b", blk):
            return blk.strip()
    return None


def load_scaffold(finding_id: str) -> dict:
    path = VALIDATION_DIR / f"{finding_id}.yaml"
    if not path.exists():
        sys.exit(f"FAIL: scaffold not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def _pending_repro_scaffolds() -> list[dict]:
    """Scaffolds whose reproducer gate has not been satisfied yet."""
    out = []
    for p in sorted(VALIDATION_DIR.glob("*.yaml")):
        s = yaml.safe_load(p.read_text()) or {}
        status = (((s.get("gates") or {}).get("reproducer") or {}).get("status") or "").strip()
        if status not in ("pass", "fail"):
            out.append(s)
    return out


def cmd_repro_prompts() -> None:
    """Write a reproducer-builder prompt per finding still needing a reproducer.

    Make/session path: a human (or the session's Agent tool) runs each
    prompt.md and saves the returned .java to cell-1/hunt/repros/<id>.java.
    The headless path uses pipeline.run_repro_subagent instead.
    """
    if not VALIDATION_DIR.is_dir() or not any(VALIDATION_DIR.glob("*.yaml")):
        sys.exit("FAIL: no validation scaffolds. Run `validate` first.")
    REPROS_DIR.mkdir(parents=True, exist_ok=True)
    pending = _pending_repro_scaffolds()
    if not pending:
        print("All findings already have a reproducer gate (pass/fail). Nothing to do.")
        return
    for s in pending:
        fid = s.get("finding_id", "?")
        prompt_path = REPROS_DIR / f"{fid}.prompt.md"
        prompt_path.write_text(build_repro_prompt(s))
        print(f"[repro-prompts] {prompt_path}  (class {repro_class_name(fid)})", file=sys.stderr)
    print()
    print(f"Wrote {len(pending)} reproducer prompt(s) to {REPROS_DIR}/")
    print("For each: run <id>.prompt.md via a fresh Agent, save the ```java block to")
    print(f"  {REPROS_DIR}/<id>.java   (class name must match Repro_<id>)")
    print("Then run the reproducers:  python3 scripts/day3-hunt.py run-repros")


def repro_status_from_exit(code: int) -> tuple[str, str]:
    """Map run-repro.sh exit code → (reproducer-gate status, note).

    The KEY inversion (non-AI validator): a reproducer SUCCEEDS when the JUnit
    test FAILS on buggy HEAD — that is what "the bug reproduces" means. So
    run-repro.sh exit 1 (test failed) → gate status "pass"; exit 0 (test passed)
    → "fail" (bug did not reproduce). Tooling errors → "not-attempted".
    """
    if code == 1:
        return "pass", "JUnit test FAILED on HEAD — bug reproduces (run-repro.sh exit 1)."
    if code == 0:
        return "fail", "JUnit test PASSED on HEAD — bug did not reproduce (run-repro.sh exit 0)."
    if code == 2:
        return "not-attempted", "Docker/mvn invocation error (run-repro.sh exit 2) — sandbox unavailable?"
    if code == 3:
        return "not-attempted", "Bad args / missing input to run-repro.sh (exit 3)."
    return "not-attempted", f"Unexpected run-repro.sh exit {code}."


def _set_gate_block(raw: str, block: str, values: dict[str, str],
                    raw_fields: set[str] | None = None) -> str:
    """Surgically set single-line fields inside a `  <block>:` gate of a scaffold.

    Preserves every comment and all other gates — only the named single-line
    fields (4-space indent) inside the given block are rewritten. `values` maps
    field-name → value; string values are double-quoted/escaped, except fields
    named in `raw_fields` (e.g. a flow-style list or bool) which are written
    verbatim. Raises ValueError if any requested field isn't found in the block.
    """
    raw_fields = raw_fields or set()

    def q(v: str) -> str:
        safe = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
        return f'"{safe}"'
    repl = {k: (str(v) if k in raw_fields else q(v)) for k, v in values.items()}
    keys = "|".join(re.escape(k) for k in values)
    # value capture must consume the WHOLE existing value, else a bare multi-word
    # scalar (what yaml.safe_dump emits, e.g. `notes: Docker error here`) leaves
    # its tail in the trailing-comment group and gets duplicated on rewrite (R1).
    # Match: a double/single-quoted string (honoring escapes), a flow list, or a
    # bare scalar to EOL — with an OPTIONAL trailing ` # comment` preserved. A
    # bare YAML scalar can't contain ` #`, and quoted `#` is inside the quotes,
    # so the comment split is unambiguous.
    field_re = re.compile(
        rf'^(    )({keys})(:[ \t]*)'
        r'''("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\[[^\]]*\]|[^#\n]*?)'''
        r'([ \t]*#.*)?$'
    )
    block_re = re.compile(rf"^  {re.escape(block)}:\s*$")
    out, in_block, done = [], False, set()
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if block_re.match(stripped):
            in_block = True
            out.append(line)
            continue
        if in_block:
            # leave the block at the next sibling key (2-space indent, non-blank)
            if re.match(r"^  \S", stripped) and not re.match(r"^    ", stripped):
                in_block = False
            else:
                m = field_re.match(stripped)
                if m and m.group(2) not in done:
                    done.add(m.group(2))
                    nl = "\n" if line.endswith("\n") else ""
                    out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{repl[m.group(2)]}{m.group(5) or ''}{nl}")
                    continue
        out.append(line)
    missing = set(values) - done
    if missing:
        raise ValueError(f"{block} gate fields not all found (missing: {sorted(missing)})")
    return "".join(out)


def set_reproducer_gate(raw: str, status: str, path: str, notes: str) -> str:
    """Set the reproducer gate's status/path/notes, preserving everything else."""
    return _set_gate_block(raw, "reproducer",
                           {"status": status, "path": path, "notes": notes})


def set_fix_gate(raw: str, status: str, patch_path: str, notes: str) -> str:
    """Set the fix_passes_tests gate's status/patch_path/notes, preserving the rest."""
    return _set_gate_block(raw, "fix_passes_tests",
                           {"status": status, "patch_path": patch_path, "notes": notes})


def set_cwe_gate(raw: str, cwe: str, cvss: str, notes: str) -> str:
    """Set the cwe gate's cwe/cvss/notes, preserving everything else."""
    return _set_gate_block(raw, "cwe", {"cwe": cwe, "cvss": cvss, "notes": notes})


def set_dedup_suggestion(raw: str, references_flow: str | None, notes: str) -> str:
    """Set the dedup gate's `references` (a flow-style YAML list, written
    verbatim) and `notes`. Deliberately leaves `is_duplicate` untouched — that's
    a gate-relevant human judgment, never auto-asserted."""
    values: dict[str, str] = {"notes": notes}
    raw_fields: set[str] = set()
    if references_flow is not None:
        values["references"] = references_flow
        raw_fields = {"references"}
    return _set_gate_block(raw, "dedup", values, raw_fields=raw_fields)


# Deterministic finding-type → likely CWE. Only confident mappings; unmapped
# types get no suggestion (the human fills them). CVSS stays N/A for correctness.
CWE_BY_TYPE = {
    "npe": "CWE-476", "null": "CWE-476", "empty-collection": "CWE-476",
    "off-by-one": "CWE-193",
    "race": "CWE-362", "concurrency": "CWE-362", "concurrent-cache": "CWE-362",
    "integer-overflow": "CWE-190",
    "recursion": "CWE-674",
    "dropped-exception": "CWE-755",
    "wrong-return": "CWE-393",
    "generic-type": "CWE-704",
    "logic": "CWE-670",
    "unicode": "CWE-176",
}


def suggest_cwe(finding_type: str | None) -> tuple[str, str]:
    """(cwe, advisory-note) for a finding type, or ('', '') when no confident map."""
    cwe = CWE_BY_TYPE.get((finding_type or "").strip().lower())
    if not cwe:
        return "", ""
    return cwe, (f"Auto-suggested from finding type '{finding_type}'. CVSS N/A for "
                 "correctness; confirm/correct the CWE.")


def suggest_dedup(scaffold: dict) -> tuple[str | None, str]:
    """(references-flow-list | None, advisory-note) from the scaffold's dedup_auto.

    Lists the OSV/GitHub candidates the auto-dedup already found (factual). Never
    decides is_duplicate. None references → zero candidates (note says so)."""
    da = scaffold.get("dedup_auto") or {}
    osv = da.get("osv_matches") or []
    gh = da.get("github_matches") or []
    refs = [str(m.get("id")) for m in osv if m.get("id")] + \
           [str(m.get("url")) for m in gh if m.get("url")]
    if not refs:
        return None, ("No OSV/GitHub candidates found by auto-dedup — likely not a "
                      "duplicate; confirm and set is_duplicate.")
    flow = "[" + ", ".join(f'"{r}"' for r in refs) + "]"
    return flow, (f"Auto-listed {len(osv)} OSV + {len(gh)} GitHub candidate(s); "
                  "review each and set is_duplicate.")


def apply_gate_suggestions() -> dict:
    """Advisory, deterministic (non-AI) auto-population of the dedup + cwe gates,
    filling ONLY blanks. Never sets is_duplicate or final_status — those stay the
    human's call — so this can't complete the day3-gates step on its own. Returns
    {ok, updated, results:[{finding_id, cwe, dedup_refs}]}."""
    if not VALIDATION_DIR.is_dir() or not any(VALIDATION_DIR.glob("*.yaml")):
        return {"ok": False, "error": "no validation scaffolds — run `validate` first"}
    results = []
    for sp in sorted(VALIDATION_DIR.glob("*.yaml")):
        s = _safe_load_scaffold(sp)
        if s is None:
            continue
        gates = s.get("gates") or {}
        text = sp.read_text()
        entry = {"finding_id": s.get("finding_id"), "cwe": None, "dedup_refs": 0}

        cwe_gate = gates.get("cwe") or {}
        if not str(cwe_gate.get("cwe") or "").strip():
            cwe, note = suggest_cwe(s.get("type"))
            if cwe:
                try:
                    text = set_cwe_gate(text, cwe, "N/A", note)
                    entry["cwe"] = cwe
                except ValueError:
                    pass

        dedup_gate = gates.get("dedup") or {}
        # Idempotent (R10): only fill when the gate is genuinely untouched —
        # references empty, is_duplicate unset, AND notes still blank. Without the
        # notes check the no-candidate note was rewritten on every run.
        if (not (dedup_gate.get("references") or [])
                and dedup_gate.get("is_duplicate") is None
                and not str(dedup_gate.get("notes") or "").strip()):
            refs_flow, note = suggest_dedup(s)
            try:
                text = set_dedup_suggestion(text, refs_flow, note)
                entry["dedup_refs"] = (refs_flow.count(",") + 1) if refs_flow else 0
            except ValueError:
                pass

        if entry["cwe"] is not None or text != sp.read_text():
            sp.write_text(text)
            results.append(entry)
    return {"ok": True, "updated": len(results), "results": results}


def cmd_suggest_gates() -> None:
    r = apply_gate_suggestions()
    if not r.get("ok"):
        sys.exit(f"FAIL: {r.get('error')}")
    for e in r["results"]:
        print(f"[suggest-gates] {e['finding_id']}: cwe={e['cwe']} dedup_refs={e['dedup_refs']}", file=sys.stderr)
    print(f"\nFilled advisory dedup/cwe suggestions for {r['updated']} scaffold(s). "
          "Review them, then set is_duplicate + final_status yourself.")


def fix_status_from_exit(code: int) -> tuple[str, str]:
    """Map run-fix.sh exit code → (fix_passes_tests gate status, note).

    Inverted vs the reproducer: the fix SUCCEEDS when the reproducer test now
    PASSES. exit 0 = reproducer green after patch → "pass"; exit 1 = still red →
    "fail"; exit 3 = patch didn't apply → "fail"; exit 4 = no test ran (patch
    likely broke compilation) → "fail"; exit 2 = docker/infra → "not-attempted".
    """
    if code == 0:
        return "pass", "Reproducer test PASSES after applying the fix (run-fix.sh exit 0)."
    if code == 1:
        return "fail", "Reproducer test still FAILS after the fix (run-fix.sh exit 1)."
    if code == 3:
        return "fail", "Patch did not apply cleanly (run-fix.sh exit 3)."
    if code == 4:
        return "fail", "No test ran after patch — fix likely broke compilation (run-fix.sh exit 4)."
    if code == 2:
        return "not-attempted", "Docker/sandbox unavailable (run-fix.sh exit 2)."
    return "not-attempted", f"Unexpected run-fix.sh exit {code}."


_FIX_TEMPLATE_CACHE: str | None = None


def _load_fix_template() -> str:
    """Load the canonical fix-builder prompt body (everything after the '---')."""
    global _FIX_TEMPLATE_CACHE
    if _FIX_TEMPLATE_CACHE is not None:
        return _FIX_TEMPLATE_CACHE
    if not FIX_PROMPT_DOC.exists():
        sys.exit(f"FAIL: {FIX_PROMPT_DOC} missing.")
    raw = FIX_PROMPT_DOC.read_text()
    parts = raw.split("\n---\n", 1)
    _FIX_TEMPLATE_CACHE = (parts[1] if len(parts) == 2 else raw).strip() + "\n"
    return _FIX_TEMPLATE_CACHE


def build_fix_prompt(scaffold: dict, repro_src: str | None = None,
                     feedback: str | None = None) -> str:
    """Substitute a finding + its reproducer source into the fix-builder prompt.

    Pure. Token replacement (not str.format) because evidence/diff snippets carry
    literal braces. The reproducer source is included so the agent can target a
    concrete failing test ("make THIS pass"). When `feedback` is given (a prior
    attempt's failure), it is appended so the next attempt self-corrects."""
    fid = str(scaffold.get("finding_id", "")).strip()
    repro_block = (repro_src.rstrip() if repro_src else
                   "(no reproducer .java found — propose the fix from the evidence alone)")
    tokens = {
        "{{TARGET_DIR}}": str(TARGET_DIR),
        "{{FINDING_ID}}": fid,
        "{{SUMMARY}}": (scaffold.get("summary") or "").strip(),
        "{{LOCATION}}": (scaffold.get("location") or "").strip(),
        "{{TYPE}}": (scaffold.get("type") or "").strip(),
        "{{EVIDENCE}}": _indent((scaffold.get("evidence") or "").rstrip(), 4),
        "{{REPRODUCER_HINT}}": _indent((scaffold.get("reproducer_hint") or "").rstrip(), 4),
        "{{REPRO_FQCN}}": repro_fqcn(fid),
        "{{REPRO_SOURCE}}": repro_block,
    }
    body = _load_fix_template()
    for k, v in tokens.items():
        body = body.replace(k, v)
    if feedback:
        body += (
            "\n\n# YOUR PREVIOUS PATCH DID NOT WORK\n"
            f"{feedback.strip()}\n\n"
            "Diagnose why (wrong location, didn't address the root cause, broke "
            "compilation, or the reproducer still fails) and return a CORRECTED "
            "unified-diff patch in a single ```diff block.\n"
        )
    return body


def extract_diff_block(text: str) -> str | None:
    """Return the last ```diff / ```patch fenced block, or a bare ``` block that
    looks like a unified diff. Fenced-only (mirrors extract_java_block)."""
    for lang in ("diff", "patch"):
        blocks = re.findall(rf"```{lang}\s*\n(.*?)\n```", text, re.DOTALL)
        if blocks:
            return blocks[-1].strip()
    for blk in reversed(re.findall(r"```\s*\n(.*?)\n```", text, re.DOTALL)):
        if re.search(r"^(diff --git |--- )", blk, re.MULTILINE):
            return blk.strip()
    return None


def _pending_fix_scaffolds() -> list[dict]:
    """Scaffolds that have a reproducer .java (needed to validate a fix) and whose
    fix gate is not yet 'pass'."""
    out = []
    for p in sorted(VALIDATION_DIR.glob("*.yaml")):
        s = yaml.safe_load(p.read_text()) or {}
        fid = s.get("finding_id")
        if not fid or not (REPROS_DIR / f"{fid}.java").exists():
            continue
        fix_status = (((s.get("gates") or {}).get("fix_passes_tests") or {}).get("status") or "").strip()
        if fix_status != "pass":
            out.append(s)
    return out


def cmd_fix_prompts() -> None:
    """Write a fix-builder prompt per finding that has a reproducer but no passing
    fix. The prompt embeds the reproducer source so the agent targets it.

    Make/session path: run each prompt via a fresh Agent, save the ```diff block
    to cell-1/hunt/patches/<id>.patch. Headless path: pipeline.run_fix_subagent."""
    if not VALIDATION_DIR.is_dir() or not any(VALIDATION_DIR.glob("*.yaml")):
        sys.exit("FAIL: no validation scaffolds. Run `validate` first.")
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    pending = _pending_fix_scaffolds()
    if not pending:
        print("No findings with a reproducer awaiting a fix. (Build reproducers first: repro-prompts.)")
        return
    for s in pending:
        fid = s["finding_id"]
        repro_src = (REPROS_DIR / f"{fid}.java").read_text()
        (PATCHES_DIR / f"{fid}.prompt.md").write_text(build_fix_prompt(s, repro_src))
        print(f"[fix-prompts] {PATCHES_DIR / (fid + '.prompt.md')}", file=sys.stderr)
    print()
    print(f"Wrote {len(pending)} fix-builder prompt(s) to {PATCHES_DIR}/")
    print("For each: run <id>.prompt.md via a fresh Agent, save the ```diff block to")
    print(f"  {PATCHES_DIR}/<id>.patch")
    print("Then validate:  python3 scripts/day3-hunt.py run-fixes")


def _safe_load_scaffold(path: Path) -> dict | None:
    """yaml.safe_load a scaffold, or warn + return None if it's malformed (so one
    bad file can't crash a whole batch)."""
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        print(f"[scaffold] WARN skipping malformed {path.name}: {e}", file=sys.stderr)
        return None


def _resolve_worktree(worktree: str | None) -> Path:
    return Path(worktree) if worktree else TARGET_DIR


def _env_with_network(network: str | None) -> dict:
    env = dict(os.environ)
    if network:
        env["REPRO_NETWORK"] = network
    return env


def validate_one_repro(finding_id: str, worktree: str | None = None,
                       network: str | None = None) -> dict:
    """Run run-repro.sh for ONE finding (non-AI), write its reproducer gate,
    return {finding_id, ok, status, exit_code, note}. status is pass|fail|
    not-attempted, or None when there's no reproducer .java to run."""
    java = REPROS_DIR / f"{finding_id}.java"
    if not java.exists():
        return {"finding_id": finding_id, "ok": False, "status": None, "note": "no reproducer .java"}
    wt = _resolve_worktree(worktree)
    if not wt.is_dir():
        return {"finding_id": finding_id, "ok": False, "status": "not-attempted", "note": f"worktree not found: {wt}"}
    proc = subprocess.run(
        ["bash", str(RUN_REPRO_SH), str(wt), repro_fqcn(finding_id), str(java)],
        capture_output=True, text=True, env=_env_with_network(network),
    )
    status, note = repro_status_from_exit(proc.returncode)
    tail = (proc.stderr or "").strip().splitlines()
    note_full = f"{note} [{tail[-1][:160]}]" if tail else note
    rel = java.relative_to(PROJECT_ROOT) if java.is_relative_to(PROJECT_ROOT) else java
    sp = VALIDATION_DIR / f"{finding_id}.yaml"
    if sp.exists():
        try:
            sp.write_text(set_reproducer_gate(sp.read_text(), status, str(rel), note_full))
        except ValueError:
            pass
    return {"finding_id": finding_id, "ok": True, "status": status,
            "exit_code": proc.returncode, "note": note_full}


def validate_one_fix(finding_id: str, worktree: str | None = None,
                     network: str | None = None) -> dict:
    """Apply ONE finding's patch + re-run its reproducer via run-fix.sh (non-AI),
    write its fix gate, return {finding_id, ok, status, exit_code, note}. A fix
    'passes' when the reproducer flips green."""
    patch = PATCHES_DIR / f"{finding_id}.patch"
    java = REPROS_DIR / f"{finding_id}.java"
    if not patch.exists():
        return {"finding_id": finding_id, "ok": False, "status": None, "note": "no patch"}
    if not java.exists():
        return {"finding_id": finding_id, "ok": False, "status": None, "note": "no reproducer .java to validate against"}
    wt = _resolve_worktree(worktree)
    if not wt.is_dir():
        return {"finding_id": finding_id, "ok": False, "status": "not-attempted", "note": f"worktree not found: {wt}"}
    proc = subprocess.run(
        ["bash", str(RUN_FIX_SH), str(wt), str(patch), repro_fqcn(finding_id), str(java)],
        capture_output=True, text=True, env=_env_with_network(network),
    )
    status, note = fix_status_from_exit(proc.returncode)
    tail = (proc.stderr or "").strip().splitlines()
    note_full = f"{note} [{tail[-1][:160]}]" if tail else note
    rel = patch.relative_to(PROJECT_ROOT) if patch.is_relative_to(PROJECT_ROOT) else patch
    sp = VALIDATION_DIR / f"{finding_id}.yaml"
    if sp.exists():
        try:
            sp.write_text(set_fix_gate(sp.read_text(), status, str(rel), note_full))
        except ValueError:
            pass
    return {"finding_id": finding_id, "ok": True, "status": status,
            "exit_code": proc.returncode, "note": note_full}


def cmd_run_fixes(worktree: str | None = None, network: str | None = None) -> None:
    """LEGACY (jackson-only Day-3 batch). Validate every finding's fix patch via
    run-fix.sh (loops validate_one_fix). The converged multi-language orchestrator
    uses tool/run_harness.py instead; this stays for the Cell #1 batch flow."""
    if not VALIDATION_DIR.is_dir():
        sys.exit("FAIL: no validation scaffolds. Run `validate` first.")
    ran = 0
    for sp in sorted(VALIDATION_DIR.glob("*.yaml")):
        s = _safe_load_scaffold(sp)
        fid = s.get("finding_id") if s else None
        if not fid:
            continue
        if not (PATCHES_DIR / f"{fid}.patch").exists():
            print(f"[run-fixes] {fid}: no patch — skip (run fix-prompts first)", file=sys.stderr)
            continue
        r = validate_one_fix(fid, worktree, network)
        if not r.get("ok") and r.get("status") is None:
            print(f"[run-fixes] {fid}: {r.get('note')} — skip", file=sys.stderr)
            continue
        ran += 1
        print(f"[run-fixes] {fid}: fix gate → {r['status']}", file=sys.stderr)
    print(f"\nRan {ran} fix validation(s). Review gates, then re-run `validate` to refresh the pass-1 report.")


def cmd_run_repros(worktree: str | None = None, network: str | None = None) -> None:
    """LEGACY (jackson-only Day-3 batch). Validate every finding's reproducer via
    run-repro.sh (loops validate_one_repro). The converged multi-language
    orchestrator uses tool/run_harness.py instead; this stays for Cell #1."""
    if not VALIDATION_DIR.is_dir():
        sys.exit("FAIL: no validation scaffolds. Run `validate` first.")
    ran = 0
    for sp in sorted(VALIDATION_DIR.glob("*.yaml")):
        s = _safe_load_scaffold(sp)
        fid = s.get("finding_id") if s else None
        if not fid:
            continue
        if not (REPROS_DIR / f"{fid}.java").exists():
            print(f"[run-repros] {fid}: no repro .java — skip (run repro-prompts first)", file=sys.stderr)
            continue
        r = validate_one_repro(fid, worktree, network)
        ran += 1
        print(f"[run-repros] {fid}: reproducer gate → {r['status']}", file=sys.stderr)
    print(f"\nRan {ran} reproducer(s). Review gates, then re-run `validate` to refresh the pass-1 report.")


# ---- main ----
def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="Substitute shortlist into prompt templates; write per-angle prompt.md + findings stubs.")
    d = sub.add_parser("dedup", help="One-off OSV+GitHub keyword lookup for a summary string.")
    d.add_argument("query", help="Finding summary or keyword string to dedup-check.")
    sub.add_parser("validate", help="State-aware: scaffold-then-aggregate. Writes cell-1-candidates-pass1.md once scaffolds are filled.")
    sub.add_parser("repro-prompts", help="Write a reproducer-builder prompt per finding still needing a reproducer.")
    rr = sub.add_parser("run-repros", help="[LEGACY/jackson-only] Execute each finding's reproducer .java via run-repro.sh (Docker); write the reproducer gate. Multi-language uses tool/run_harness.py.")
    rr.add_argument("--worktree", default=None, help="Worktree/clone to run against (default: targets/jackson-databind).")
    rr.add_argument("--network", default=None, help="REPRO_NETWORK value passed to run-repro.sh (e.g. 'host' for first-run dep resolution).")
    sub.add_parser("suggest-gates", help="Advisory: auto-fill blank dedup (references) + cwe gates from existing data (non-AI; never sets is_duplicate/final_status).")
    sub.add_parser("fix-prompts", help="Write a fix-builder prompt per finding that has a reproducer but no passing fix.")
    rf = sub.add_parser("run-fixes", help="[LEGACY/jackson-only] Apply each finding's patch + re-run its reproducer via run-fix.sh; write the fix gate. Multi-language uses tool/run_harness.py.")
    rf.add_argument("--worktree", default=None, help="Worktree/clone to run against (default: targets/jackson-databind).")
    rf.add_argument("--network", default=None, help="REPRO_NETWORK value passed to run-fix.sh.")
    args = p.parse_args()

    if args.cmd == "prepare":
        cmd_prepare()
    elif args.cmd == "dedup":
        cmd_dedup(args.query)
    elif args.cmd == "validate":
        cmd_validate()
    elif args.cmd == "suggest-gates":
        cmd_suggest_gates()
    elif args.cmd == "repro-prompts":
        cmd_repro_prompts()
    elif args.cmd == "run-repros":
        cmd_run_repros(worktree=args.worktree, network=args.network)
    elif args.cmd == "fix-prompts":
        cmd_fix_prompts()
    elif args.cmd == "run-fixes":
        cmd_run_fixes(worktree=args.worktree, network=args.network)


if __name__ == "__main__":
    main()
