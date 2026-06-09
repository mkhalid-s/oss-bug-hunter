"""Guard against the relocation regression: no source/script/tool file should
hardcode the old /workspaces/GW/AI path. Relocatable code derives paths from
__file__; prompts use the {{TARGET_DIR}} token or relative paths."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALE = "/workspaces/GW/AI"


def _source_files():
    for sub in ("scripts", "tool"):
        for p in (ROOT / sub).rglob("*"):
            if p.is_dir() or "__pycache__" in p.parts:
                continue
            if p.suffix in (".py", ".sh", ".md", ".js", ".html", ".css"):
                yield p


def test_no_hardcoded_ai_path_in_source():
    offenders = [str(p.relative_to(ROOT)) for p in _source_files()
                 if STALE in p.read_text(errors="ignore")]
    assert not offenders, f"stale {STALE} path in: {offenders}"


def test_day3_template_uses_target_dir_token():
    tmpl = (ROOT / "scripts" / "day3-novel-hunt-prompts.md").read_text()
    assert "{{TARGET_DIR}}" in tmpl
    assert STALE not in tmpl
