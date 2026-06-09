"""Per-target env-bootstrap runner (M5 #47). Runs an adapter's `bootstrap_steps`
(deps resolution: uv venv + install / go mod download / cargo fetch / npm ci) ONCE,
IDEMPOTENTLY — a `.oss-bootstrap.json` marker keyed on the manifest hash means an
unchanged target is skipped on re-run. Steps run via an injectable `run_step`
(default: local subprocess) so the logic is hermetic + testable; the network policy
is bridge (first-run dependency resolution). Never modifies tracked source.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from pathlib import Path

MARKER = ".oss-bootstrap.json"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _write_marker(path, data) -> None:
    """Atomic write (tmp + os.replace) so a crash/concurrent run can't leave a torn marker."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _manifest_files(worktree, adapter) -> list:
    wt = Path(worktree)
    out = []
    for m in getattr(adapter, "MANIFESTS", ()):
        out += sorted(wt.glob(m)) if "*" in m else ([wt / m] if (wt / m).exists() else [])
    # python: also hash sibling requirements*.txt (requirements-dev.txt etc.)
    if getattr(adapter, "language", None) == "python":
        out += [p for p in sorted(wt.glob("requirements*.txt")) if p not in out]
    return [p for p in out if p.is_file()]


def _manifest_hash(worktree, adapter) -> str | None:
    files = _manifest_files(worktree, adapter)
    if not files:
        return None
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def needs_bootstrap(worktree, adapter) -> bool:
    """True when the target has a manifest the adapter knows how to resolve."""
    return bool(adapter.bootstrap_steps(str(worktree)))


def _local_run(argv, *, cwd, network=None):
    """Default runner: local subprocess (the trust gate already chose local for a
    trusted target). `network` is advisory locally (full net); honored by container runners."""
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=1800,
                       env=dict(os.environ))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def bootstrap(worktree, adapter, *, run_step=None, network: str = "bridge", log=print,
              force: bool = False) -> dict:
    """Resolve the target's deps once, idempotently. Returns {ok, status, ...} where
    status ∈ skipped (no manifest) | cached (marker matches) | bootstrapped | failed."""
    steps = adapter.bootstrap_steps(str(worktree))
    if not steps:
        return {"ok": True, "status": "skipped", "steps_run": 0}   # single-file target — nothing to do
    marker = Path(worktree) / MARKER
    key = _manifest_hash(worktree, adapter)
    if not force and marker.exists():
        try:
            prev = json.loads(marker.read_text())
            if prev.get("status") == "ok" and prev.get("hash") == key:
                log("[bootstrap] cached (manifests unchanged)")
                return {"ok": True, "status": "cached", "steps_run": 0}
        except Exception:
            pass
    runner = run_step or _local_run
    for i, argv in enumerate(steps, 1):
        log(f"[bootstrap] step {i}/{len(steps)}: {' '.join(argv)}")
        rc, out = runner(argv, cwd=str(worktree), network=network)
        if rc != 0:
            _write_marker(marker, {"status": "failed", "hash": key, "step": argv, "ts": _now()})
            log(f"[bootstrap] FAILED rc={rc}: {(out or '')[-200:]}")
            return {"ok": False, "status": "failed", "step": argv, "rc": rc}
    _write_marker(marker, {"status": "ok", "hash": key, "steps": len(steps), "ts": _now()})
    log(f"[bootstrap] ok ({len(steps)} step(s))")
    return {"ok": True, "status": "bootstrapped", "steps_run": len(steps)}
