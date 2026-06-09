"""LLM reproducer-builder for NON-JAVA findings (task #54 / docs/ADOPTION.md step 3).

Closes the ingest→Verify loop the Phase-2 review flagged: an ingested Python/Go/
Rust/JS finding has no PoC, so it could never reach run_harness. This asks the LLM
to PROPOSE a FAILING test (a reproducer) from the finding's hint/location/evidence;
the non-AI validator (run_harness.validate_repro) DISPOSES — a reproducer only
counts when the test actually FAILS on HEAD. Java keeps its bespoke jackson-aware
builder (pipeline.run_repro_subagent); this serves the four adapter languages.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOL))
import claude_driver  # noqa: E402

# the test SHAPE each adapter expects (mirrors tool/adapters.py placement/run).
_SHAPE = {
    "python": "a pytest test function `def test_repro(): ...` that imports the target "
              "package and asserts the buggy behavior",
    "go": "a `func TestRepro(t *testing.T) { ... }` test (package-level) that calls the "
          "target and fails via t.Fatal/t.Error on the bug",
    "rust": "a `#[test] fn repro() { ... }` integration test using the crate's public API "
            "that asserts/panics on the bug",
    "javascript": "a Node built-in test — `import { test } from 'node:test'; import "
                  "assert from 'node:assert'; test('repro', () => { ... })` — asserting the bug",
}


def build_repro_prompt(scaffold: dict) -> str:
    lang = scaffold.get("language", "python")
    return (
        f"You are writing a REPRODUCER: a single {lang} test that demonstrates a bug by "
        f"FAILING on the current (buggy) code. Failing IS the point — that is how the "
        f"reproducer proves the bug is real.\n\n"
        f"Finding: {scaffold.get('summary', '')}\n"
        f"Type: {scaffold.get('type', '')}\n"
        f"Location: {scaffold.get('location', '')}\n"
        f"Evidence: {scaffold.get('evidence', '')}\n"
        f"Hint: {scaffold.get('reproducer_hint', '')}\n\n"
        f"Write {_SHAPE.get(lang, 'a failing test')}.\n"
        f"Rules: it MUST FAIL on the buggy code; use ONLY the target's public API + the "
        f"standard test framework; no network, no new dependencies. Output ONLY the test "
        f"file content inside ONE fenced ``` code block."
    )


def extract_code_block(text: str, lang: str) -> str | None:
    """Prefer a fenced block tagged with the language; else the first fenced block."""
    # NB: keep these alternations group-free (non-capturing) so group(1) stays the code body.
    tags = {"python": r"py(?:thon)?", "go": r"go", "rust": r"rust|rs",
            "javascript": r"javascript|js|mjs|node"}.get(lang, re.escape(lang))
    m = re.search(rf"```(?:{tags})[^\n]*\n(.*?)```", text, re.S | re.I)
    if not m:
        m = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.S)
    return (m.group(1).strip() + "\n") if m else None


def build_repro(scaffold: dict, repro_path, *, model: str = "opus", effort: str = "high",
                timeout_s: int = 1800, log=print, _runner=None):
    """AI PROPOSES a reproducer written to `repro_path`; returns the path or None.
    The engine (run_harness.validate_repro) DISPOSES. `_runner` is injectable for
    tests (defaults to claude_driver.run_claude_with_retry)."""
    lang = scaffold.get("language", "python")
    if lang == "java":
        return None                       # Java uses pipeline.run_repro_subagent
    fid = str(scaffold.get("finding_id") or "repro")
    runner = _runner or claude_driver.run_claude_with_retry
    log(f"[repro-builder] asking {model}/{effort} for a {lang} reproducer for {fid}…")
    res = runner(build_repro_prompt(scaffold), model=model, effort=effort, timeout_s=timeout_s)
    if res.get("returncode") != 0:
        log(f"[repro-builder] claude failed rc={res.get('returncode')}: "
            f"{(res.get('stderr') or '')[:160]}")
        return None
    code = extract_code_block(res.get("stdout") or "", lang)
    if not code:
        log("[repro-builder] no fenced code block in the response — giving up")
        return None
    p = Path(repro_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)
    log(f"[repro-builder] wrote reproducer -> {p}")
    return str(p)
