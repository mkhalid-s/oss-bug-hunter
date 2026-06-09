#!/usr/bin/env python3
"""Ingest Anthropic defending-code-reference-harness artifacts into OSS Bug Hunter
finding scaffolds (Phase 2 of docs/ADOPTION.md).

Their `/vuln-scan` skill is READ-ONLY/static ("expect more false positives"); their
`/triage` dedups + ranks. Neither EXECUTES the code. This bridges their output into
our cell-1/hunt/validation/<id>.yaml scaffolds, feeding our converged orchestrator
(run_harness) so it can act as the multi-language **Verify** stage they lack — AI
(their skills) proposes, our non-AI gates dispose.

Reads VULN-FINDINGS.json (/vuln-scan) or TRIAGE.json (/triage). Ingested findings
land in the 'proposed' column (gates not-attempted) and carry `source` provenance,
a first-class `severity`, and a `triage` block (verdict/confidence/reachability),
preserving Anthropic's severity discipline.

CLOSURE STATUS — be honest: ingest OPENS the funnel; it does NOT by itself verify
non-Java findings. `orchestrate_finding` auto-builds a reproducer only for Java; an
ingested Python/Go/Rust/JS finding has no PoC, so it stays in 'proposed' until a
reproducer is supplied. A per-language reproducer-builder is the missing piece (NOT
yet built) — tracked in docs/ADOPTION.md. We carry a `reproducer_hint` to seed it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CELL = ROOT / "cell-1"

_SEV = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
# Anthropic triage/verdict fields worth preserving (severity discipline + provenance).
_TRIAGE_FIELDS = ("severity", "verdict", "confidence", "rationale", "reachability",
                  "attacker_control", "preconditions", "blast_radius", "verify_verdict")


def _findings_array(doc):
    """Tolerant extraction mirroring triage's own input handling: a top-level list,
    or an object with a findings-like key."""
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("findings", "triaged", "results", "bugs"):
            v = doc.get(k)
            if isinstance(v, list):
                return v
    return []


def _norm_id(raw, i):
    # sanitize → clamp (avoid OSError on absurd filenames) → re-strip any trailing dash
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw or "")).strip("-").lower()[:64].strip("-")
    return f"vs-{s}" if s else f"vs-f{i + 1:03d}"


def _norm_sev(raw):
    """Normalize a scanner severity to our enum, else None. Kept first-class so the
    board can surface/sort by it (adopting Anthropic's severity discipline)."""
    s = str(raw or "").strip().upper()
    return s if s in _SEV else None


def is_rejected(f) -> str | None:
    """A reason to SKIP a triage-rejected finding (false positive / duplicate), else None."""
    v = str(f.get("verdict", "")).lower()
    if v in ("false_positive", "duplicate", "not_a_bug"):
        return f"verdict={v}"
    if f.get("is_duplicate") is True or f.get("duplicate_of"):
        return "duplicate"
    return None


def map_finding(f: dict, *, language: str, target: str, source: str, index: int = 0) -> dict:
    """Anthropic finding {id,file,line,category,severity,title,description,...} ->
    our finding scaffold (cell-1/hunt/validation schema; see tool/findings.py)."""
    fid = _norm_id(f.get("id"), index)
    file_, line = f.get("file"), f.get("line")
    loc = f"{file_}:{line}" if file_ and line is not None else (file_ or "unknown")
    summary = (f.get("title") or (f.get("description") or "")[:120] or fid).strip()
    evidence = (f.get("description") or f.get("rationale") or "").strip() \
        or "(no description from scanner)"
    triage = {k: f[k] for k in _TRIAGE_FIELDS if k in f and f[k] not in (None, "")}
    return {
        "finding_id": fid,
        "angle": "security",
        "type": f.get("category") or "unknown",
        "location": loc,
        "summary": summary,
        "severity": _norm_sev(f.get("severity")),   # first-class (CRITICAL..LOW|None)
        "language": language,
        "target": target,
        "source": source,                       # provenance: which Anthropic skill
        "evidence": evidence,
        # seed for the (not-yet-built) per-language reproducer-builder; surfaced by
        # findings.get_finding so a human/agent can hand-author a PoC to close Verify.
        "reproducer_hint": f"Write a {language} test exercising {loc} that FAILS on "
                           f"HEAD, demonstrating: {summary}",
        "triage": triage,                        # fuller severity discipline (kept verbatim)
        "dedup_auto": {"osv_matches": [], "github_matches": []},
        "gates": {
            "reproducer": {"status": "not-attempted", "path": None,
                           "notes": "ingested static finding — needs a reproducer (PoC) to execution-verify."},
            "dedup": {"is_duplicate": False, "references": [], "notes": f"source={source}"},
            "cwe": {"cwe": "", "cvss": "N/A", "notes": f.get("category") or ""},
            "fix_passes_tests": {"status": "not-attempted", "patch_path": None,
                                 "notes": "awaiting reproducer + fix"},
        },
        "final_status": "pending",
        "self_consistency": {},
    }


def _source_for(path: Path, override: str | None) -> str:
    if override:
        return override
    n = path.name.lower()
    if "triage" in n:
        return "anthropic:triage"
    if "vuln" in n or "finding" in n:
        return "anthropic:vuln-scan"
    return "anthropic:import"


def ingest(json_path, *, language: str, target: str, cell: Path | None = None,
           source: str | None = None, write: bool = True) -> dict:
    """Parse an Anthropic artifact and (optionally) write finding scaffolds.
    Returns {source, language, target, written:[ids], skipped:[{id,reason}], total}."""
    path = Path(json_path)
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read/parse {path}: {e}") from e
    src = _source_for(path, source)
    vdir = (cell or CELL) / "hunt" / "validation"
    arr = _findings_array(doc)
    written, skipped, seen = [], [], set()
    for i, f in enumerate(arr):
        if not isinstance(f, dict):
            skipped.append({"index": i, "reason": "not-an-object"})
            continue
        reason = is_rejected(f)
        if reason:
            skipped.append({"id": f.get("id"), "reason": reason})
            continue
        rec = map_finding(f, language=language, target=target, source=src, index=i)
        fid = rec["finding_id"]
        if fid in seen:                          # collision after id-normalization
            skipped.append({"id": f.get("id"), "reason": f"duplicate-id {fid}"})
            continue
        seen.add(fid)
        if write:
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / f"{fid}.yaml").write_text(
                yaml.safe_dump(rec, sort_keys=False, default_flow_style=False))
        written.append(fid)
    return {"source": src, "language": language, "target": target,
            "written": written, "skipped": skipped, "total": len(arr)}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Ingest Anthropic VULN-FINDINGS.json / TRIAGE.json into finding scaffolds.")
    ap.add_argument("json_path")
    ap.add_argument("--language", required=True, help="java|python|go|rust|javascript")
    ap.add_argument("--target", required=True, help="target name (under targets/)")
    ap.add_argument("--source", default=None, help="override provenance label")
    ap.add_argument("--dry-run", action="store_true", help="parse + map, but do not write")
    a = ap.parse_args(argv)
    r = ingest(a.json_path, language=a.language, target=a.target,
               source=a.source, write=not a.dry_run)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
