"""LLM fix-builder for NON-JAVA findings (task #55, the #54 sibling).

Symmetric to llm_repro_provider: closes the reproduce→fix→retry loop for Python/Go/
Rust/JS. AI PROPOSES a minimal unified-diff patch (from the finding + the reproducer
+ the prior failure feedback); the non-AI validator (run_harness.validate_fix)
DISPOSES — a patch counts only when the reproducer flips PASS and stays contained
(adapter patch_allowed/denied globs). Java keeps its jackson-aware builders
(pipeline.run_fix_subagent + llm_fix_provider).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent
_ROOT = _TOOL.parent
sys.path.insert(0, str(_TOOL))
import claude_driver  # noqa: E402


def _load_day3():
    """Reuse day3-hunt.py's battle-tested unified-diff extractor (hyphenated name)."""
    spec = importlib.util.spec_from_file_location(
        "day3_hunt", str(_ROOT / "scripts" / "day3-hunt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_d3 = _load_day3()

# what a fix may touch per language (mirrors adapters.<Adapter>.patch_allowed/denied).
_PATCH_RULE = {
    "python": "Edit only `.py` source files; NEVER pyproject.toml/setup.py/requirements/conftest.py.",
    "go": "Edit only `.go` source files; NEVER go.mod/go.sum.",
    "rust": "Edit only `.rs` source files; NEVER Cargo.toml/Cargo.lock.",
    "javascript": "Edit only `.js/.cjs/.mjs/.jsx/.ts/.tsx` source; NEVER package.json/lockfiles.",
}


def build_fix_prompt(scaffold: dict, repro_src: str, *, feedback: str | None = None) -> str:
    lang = scaffold.get("language", "python")
    fb = (f"\n\nThe PREVIOUS patch did NOT make the test pass. Failure feedback:\n"
          f"{feedback}\nFix the root cause this time.\n") if feedback else ""
    return (
        f"Propose a fix for this {lang} bug as a UNIFIED DIFF (git apply format).\n\n"
        f"Finding: {scaffold.get('summary', '')}\n"
        f"Location: {scaffold.get('location', '')}\n"
        f"Evidence: {scaffold.get('evidence', '')}\n\n"
        f"The reproducer test (it MUST PASS after your fix):\n```\n{repro_src}\n```{fb}\n"
        f"Rules: the SMALLEST change that fixes the ROOT CAUSE — no refactoring, no "
        f"drive-by cleanups, no symptom-masking. {_PATCH_RULE.get(lang, 'Edit only source files.')} "
        f"Do NOT edit the reproducer/test. Output ONLY the unified diff in ONE fenced "
        f"``` block, paths relative to the repo root (e.g. `--- a/src/x.py`)."
    )


def _propose(prompt, *, model, effort, timeout_s, runner, log):
    res = (runner or claude_driver.run_claude_with_retry)(
        prompt, model=model, effort=effort, timeout_s=timeout_s)
    if res.get("returncode") != 0:
        log(f"[fix-builder] claude failed rc={res.get('returncode')}: "
            f"{(res.get('stderr') or '')[:160]}")
        return None
    diff = _d3.extract_diff_block(res.get("stdout") or "")
    if not diff:
        log("[fix-builder] no unified-diff block in the response — giving up")
        return None
    return diff.rstrip() + "\n"


def build_fix(scaffold: dict, repro_src: str, patch_path, *, model: str = "opus",
              effort: str = "high", timeout_s: int = 1800, log=print, _runner=None):
    """Initial non-Java fix: AI proposes a patch written to `patch_path`. Returns
    the path or None. The engine (validate_fix) disposes. `_runner` injectable for tests."""
    if scaffold.get("language") == "java":
        return None                       # Java uses pipeline.run_fix_subagent
    diff = _propose(build_fix_prompt(scaffold, repro_src), model=model, effort=effort,
                    timeout_s=timeout_s, runner=_runner, log=log)
    if not diff:
        return None
    p = Path(patch_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(diff)
    log(f"[fix-builder] wrote patch -> {p}")
    return str(p)


def make_provider(scaffold: dict, repro_src: str, out_dir, *, model: str = "opus",
                  effort: str = "high", timeout_s: int = 1800, log=print, _runner=None):
    """Return a `fix_provider(feedback, attempt) -> patch_path | None` closure for
    run_harness.orchestrate's retry loop (asks for a corrected patch from feedback)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fid = str(scaffold.get("finding_id") or "fix")

    def provider(feedback, attempt):
        log(f"[fix-builder] attempt {attempt} failed — asking {model}/{effort} for a corrected patch…")
        diff = _propose(build_fix_prompt(scaffold, repro_src, feedback=feedback),
                        model=model, effort=effort, timeout_s=timeout_s, runner=_runner, log=log)
        if not diff:
            return None
        p = out / f"{fid}-retry{attempt}.patch"
        p.write_text(diff)
        log(f"[fix-builder] wrote revised patch -> {p}")
        return str(p)

    return provider
