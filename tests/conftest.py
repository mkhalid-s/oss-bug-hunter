"""Test fixtures + module loaders for scripts with hyphens in filenames.

P0-10 fix: these tests pin the boundaries of load-bearing scoring functions.
Run with: .venv/bin/python -m pytest tests/
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tool"))


def _load(file_relpath: str, mod_name: str):
    full = ROOT / file_relpath
    spec = importlib.util.spec_from_file_location(mod_name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Modules with hyphenated filenames — load via importlib.
day2_build = _load("scripts/day2-build-dataset.py", "day2_build_dataset")
day2_backtest = _load("scripts/day2-backtest.py", "day2_backtest")
day3_hunt = _load("scripts/day3-hunt.py", "day3_hunt")
day4_finalize = _load("scripts/day4-finalize.py", "day4_finalize")

# Modules with valid Python identifiers — normal import.
import _check          # noqa: E402
import _lib            # noqa: E402
import claude_driver   # noqa: E402
