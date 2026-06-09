"""Findings reader for the U2 board + detail (plan §4.3).

Findings are the validation scaffolds in cell-1/hunt/validation/*.yaml, paired
with the reproducer (cell-1/hunt/repros/<id>.java) and the fix patch
(cell-1/hunt/patches/<id>.patch). The kanban COLUMN is derived from the gates —
the non-AI validators' results decide where a finding sits, never an LLM label.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CELL = Path(__file__).resolve().parents[1] / "cell-1"
VALIDATION = CELL / "hunt" / "validation"
REPROS = CELL / "hunt" / "repros"
PATCHES = CELL / "hunt" / "patches"

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_REJECTED = {"failed-self-consistency", "dupe", "false-positive", "unreproducible"}


def _column(gates: dict, final: str) -> str:
    g = gates or {}
    repro = ((g.get("reproducer") or {}).get("status")) == "pass"
    fixed = ((g.get("fix_passes_tests") or {}).get("status")) == "pass"
    dupe = ((g.get("dedup") or {}).get("is_duplicate")) is True
    if fixed and not dupe and final not in _REJECTED:
        return "pr-ready"
    if fixed:
        return "fixed"
    if repro:
        return "reproduced"
    return "proposed"


def _summary(s: dict) -> dict:
    g = s.get("gates") or {}
    return {
        "id": s.get("finding_id"),
        "angle": s.get("angle"),
        "type": s.get("type"),
        "location": s.get("location"),
        "summary": s.get("summary"),
        "severity": s.get("severity"),     # CRITICAL..LOW for ingested findings (Anthropic severity discipline); None for native
        "source": s.get("source"),         # provenance e.g. "anthropic:vuln-scan"; None for native findings
        # language + target let the UI launch the RIGHT run per finding (default
        # to the original Java cell for back-compat with pre-multi-language scaffolds).
        "language": s.get("language", "java"),
        "target": s.get("target", "jackson-databind"),
        "final_status": s.get("final_status"),
        "gates": {
            "reproducer": (g.get("reproducer") or {}).get("status"),
            "fix": (g.get("fix_passes_tests") or {}).get("status"),
            "dedup": (g.get("dedup") or {}).get("is_duplicate"),
            "cwe": (g.get("cwe") or {}).get("cwe"),
        },
        "column": _column(g, s.get("final_status")),
    }


def list_findings() -> list:
    out = []
    if not VALIDATION.is_dir():
        return out
    for p in sorted(VALIDATION.glob("*.yaml")):
        try:
            s = yaml.safe_load(p.read_text()) or {}
        except Exception:
            continue
        if s.get("finding_id"):
            out.append(_summary(s))
    return out


_REPRO_EXT = {"java": ".java", "python": ".py", "go": ".go",
              "rust": ".rs", "javascript": ".js"}


def get_finding(fid: str):
    if not _SAFE_ID.match(fid or ""):
        return None
    p = VALIDATION / f"{fid}.yaml"
    if not p.exists():
        return None
    try:
        s = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return None
    ext = _REPRO_EXT.get(s.get("language", "java"), ".java")
    repro = REPROS / f"{fid}{ext}"
    patch = PATCHES / f"{fid}.patch"
    d = _summary(s)
    d.update({
        "evidence": s.get("evidence"),
        "reproducer_hint": s.get("reproducer_hint"),
        "gates_full": s.get("gates") or {},
        "self_consistency": s.get("self_consistency") or {},
        "reproducer_src": repro.read_text() if repro.exists() else None,
        "reproducer_path": str(repro.relative_to(CELL.parent)) if repro.exists() else None,
        "patch_text": patch.read_text() if patch.exists() else None,
        "patch_path": str(patch.relative_to(CELL.parent)) if patch.exists() else None,
    })
    return d
