"""Gated-PR DRAFT queue (plan §12.6, Phase 3 — the outer-loop OUTPUT seam).

Promotes pr.py's read-only PREVIEW into a persisted, reviewable DRAFT: a validated
keeper finding is parked in an approval queue (cell-1/hunt/pr-drafts/<id>.yaml) with
a review status; a human approves/rejects, then runs the identity-gated push
(`pr_preview.manual_steps`). Like pr.py, this NEVER pushes / never runs `gh pr
create` — the push stays an explicit, personal-identity, human action (hard gate).
This is the queue the §12.7 control-plane renders and the autonomous loop feeds.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import yaml

import pr as _pr

ROOT = Path(__file__).resolve().parents[1]
CELL = ROOT / "cell-1"
DRAFTS = CELL / "hunt" / "pr-drafts"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")          # path-traversal guard


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _path(finding_id: str) -> Path | None:
    return DRAFTS / f"{finding_id}.yaml" if _SAFE_ID.match(finding_id or "") else None


def queue_draft(finding_id: str, target: str = "jackson-databind", *, force: bool = False) -> dict:
    """Build the PR preview and, if it's a READY keeper (or force=True), persist a
    draft record (status `pending-review`, preserving any prior decision). Returns
    {ok, draft} or {ok:False, blockers|error}. NEVER pushes."""
    p = _path(finding_id)
    if p is None:
        return {"ok": False, "error": f"unsafe finding id {finding_id!r}"}
    pv = _pr.pr_preview(finding_id, target)
    if pv is None:
        return {"ok": False, "error": f"no finding {finding_id}"}
    if not pv.get("ready") and not force:
        return {"ok": False, "finding_id": finding_id, "blockers": pv.get("blockers", [])}
    prior = get_draft(finding_id) or {}
    draft = {
        "finding_id": finding_id, "target": target,
        "status": prior.get("status", "pending-review"),
        "created": prior.get("created", _now()), "updated": _now(),
        "branch": pv["branch"], "title": pv["title"], "upstream": pv["upstream"],
        "fork": pv["fork"], "commit_message": pv["commit_message"], "body": pv["body"],
        "manual_steps": pv["manual_steps"], "ready": pv["ready"],
        "blockers": pv["blockers"], "decision_note": prior.get("decision_note"),
    }
    DRAFTS.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(draft, sort_keys=False))
    return {"ok": True, "draft": draft}


def list_drafts() -> list:
    if not DRAFTS.is_dir():
        return []
    out = []
    for p in sorted(DRAFTS.glob("*.yaml")):
        try:
            out.append(yaml.safe_load(p.read_text()) or {})
        except Exception:
            pass
    return out


def get_draft(finding_id: str) -> dict | None:
    p = _path(finding_id)
    if p is None or not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:
        return None


def decide_draft(finding_id: str, decision: str, *, note: str | None = None) -> dict:
    """Record a human review decision (`approved` | `rejected`). Does NOT push — an
    approved draft is pushed by a human via the draft's `manual_steps` (identity gate).
    Rejections feed back as negative signal for the loop."""
    if decision not in ("approved", "rejected"):
        return {"ok": False, "error": "decision must be 'approved' or 'rejected'"}
    d = get_draft(finding_id)
    if d is None:
        return {"ok": False, "error": f"no draft {finding_id}"}
    d.update({"status": decision, "decision_note": note, "updated": _now()})
    _path(finding_id).write_text(yaml.safe_dump(d, sort_keys=False))
    return {"ok": True, "draft": d}
