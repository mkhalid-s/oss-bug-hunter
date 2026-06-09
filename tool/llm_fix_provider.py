"""LLM fix-builder wired as an orchestrate `fix_provider` (plan §11.4 follow-up).

`run_harness.orchestrate` calls `fix_provider(feedback, attempt)` when a fix
attempt fails. This adapts that hook to the existing LLM fix-builder:

    build_fix_prompt(scaffold, repro_src, feedback=<failure>)   # day3-hunt.py
      -> claude -p (opus/high)                                   # claude_driver
      -> extract_diff_block(response)                            # day3-hunt.py
      -> write a .patch -> return its path

The non-AI validator (validate_fix) still DECIDES pass/fail; the LLM only
PROPOSES the revised patch — the load-bearing invariant of the whole project.
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
    """day3-hunt.py has a hyphen (not importable normally); load it by path."""
    spec = importlib.util.spec_from_file_location(
        "day3_hunt", str(_ROOT / "scripts" / "day3-hunt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_d3 = _load_day3()


def make_llm_fix_provider(scaffold: dict, repro_src, out_dir: str, *,
                          model: str = "opus", effort: str = "high",
                          timeout_s: int = 1800, log=print):
    """Return a `fix_provider(feedback, attempt) -> patch_path | None` closure
    that asks the LLM for a corrected patch using the prior failure feedback."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fid = str(scaffold.get("finding_id") or "fix")

    def provider(feedback, attempt):
        log(f"[fix-builder] attempt {attempt} failed — asking {model}/{effort} "
            f"for a corrected patch…")
        prompt = _d3.build_fix_prompt(scaffold, repro_src=repro_src, feedback=feedback)
        res = claude_driver.run_claude_with_retry(
            prompt, model=model, effort=effort, timeout_s=timeout_s)
        if res.get("returncode") != 0:
            log(f"[fix-builder] claude failed rc={res.get('returncode')}: "
                f"{(res.get('stderr') or '')[:160]}")
            return None
        diff = _d3.extract_diff_block(res.get("stdout") or "")
        if not diff:
            log("[fix-builder] no unified-diff block in the response — giving up")
            return None
        patch_path = out / f"{fid}-retry{attempt}.patch"
        patch_path.write_text(diff.rstrip() + "\n")
        log(f"[fix-builder] wrote revised patch -> {patch_path}")
        return str(patch_path)

    return provider
