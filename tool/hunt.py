"""Headless HUNT step (§12.5 last mile / task #61) — the scheduler's `hunt`.

Runs a static vuln/bug scan over a cloned target via `claude -p` (the headless,
automatable analogue of Anthropic's vendored `/vuln-scan` skill — see
vendor/anthropic-skills/), emits the `VULN-FINDINGS.json` schema, and bridges it
through `ingest.py` into our finding scaffolds. AI proposes candidates; the engine's
verify/fix gates dispose. Read-only: scans source, never builds/runs/pushes.

The LLM call is injectable (`runner=`) so this is hermetic in tests; a real scan is a
live demo (needs the model + a host), like the repro/fix builders. For richer,
multi-agent results, run the vendored interactive `/vuln-scan` skill in Claude Code and
ingest its VULN-FINDINGS.json with `ingest.ingest(...)` directly.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOL))
import claude_driver  # noqa: E402
import ingest as _ingest  # noqa: E402


def build_scan_prompt(target_dir: str, *, language: str = "") -> str:
    lang = f" (primary language: {language})" if language else ""
    return (
        f"You are the static review (vuln-scan) stage of Anthropic's defending-code "
        f"pipeline. Review the source tree at `{target_dir}`{lang} and report REAL, "
        f"specific bugs or security vulnerabilities. READ files (Read/Grep/Glob); do "
        f"NOT build, run, install, or modify anything.\n\n"
        f"Focus on reachable, attacker-controllable, or correctness-breaking issues "
        f"(injection, memory/bounds, auth, unsafe deserialization, race conditions, "
        f"wrong results on edge cases). Avoid style nits and speculative findings — "
        f"prefer a few high-confidence issues you can point to a specific line for.\n\n"
        f"Output ONLY a JSON object (one ```json block), matching the VULN-FINDINGS "
        f"schema:\n"
        f'{{"findings": [{{"id": "f001", "file": "<path relative to the tree>", '
        f'"line": <int>, "category": "<short-kebab>", "severity": '
        f'"CRITICAL|HIGH|MEDIUM|LOW", "title": "<one line>", '
        f'"description": "<why it is a bug + how it is reached>"}}]}}\n'
        f"If you find nothing solid, output {{\"findings\": []}}."
    )


def _extract_json(text: str):
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    blob = m.group(1) if m else (text or "")
    try:
        return json.loads(blob)
    except Exception:
        s, e = blob.find("{"), blob.rfind("}")          # fall back to the outermost object
        if 0 <= s < e:
            try:
                return json.loads(blob[s:e + 1])
            except Exception:
                return None
        return None


def vuln_scan(target_dir, *, language: str, target_name: str, cell=None,
              model: str = "opus", effort: str = "high", timeout_s: int = 1800,
              log=print, runner=None) -> dict:
    """Scan `target_dir` → VULN-FINDINGS.json → ingest → finding scaffolds. Returns
    {ok, finding_ids, raw?, error?}. AI proposes; verify/fix dispose. `runner` is
    injectable for tests (defaults to claude_driver.run_claude_with_retry)."""
    runner = runner or claude_driver.run_claude_with_retry
    log(f"[hunt] scanning {target_dir} ({language or '?'}) with {model}/{effort}…")
    res = runner(build_scan_prompt(str(target_dir), language=language),
                 model=model, effort=effort, timeout_s=timeout_s)
    if res.get("returncode") != 0:
        return {"ok": False, "error": f"scan failed rc={res.get('returncode')}", "finding_ids": []}
    doc = _extract_json(res.get("stdout") or "")
    if doc is None:
        return {"ok": False, "error": "no JSON findings in scan output", "finding_ids": []}
    cell = Path(cell) if cell else _ingest.CELL
    vf = cell / "hunt" / f"VULN-FINDINGS-{target_name}.json"
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps(doc))
    r = _ingest.ingest(vf, language=language, target=target_name, cell=cell,
                       source="anthropic:vuln-scan")
    log(f"[hunt] {len(r['written'])} finding(s) ingested from {target_name}")
    return {"ok": True, "finding_ids": r["written"], "raw": str(vf),
            "skipped": r.get("skipped", [])}
