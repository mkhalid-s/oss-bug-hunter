"""Synthetic demo targets — portability (#56).

The engine needs every target to BE a git repo (for `git apply` + `pristine`), but committing
a nested `.git` into our repo as content/gitlinks is broken (see .gitignore). So we track the
SOURCE under `targets/_src/<name>/` (plain files, no nested `.git`) and *materialize* a
gitignored working copy `targets/<name>/` on demand: copy → `git init` → one commit. That gives
the engine a real repo with the manifest committed — which #63's pristine guard now requires.

Idempotent: an already-materialized repo (one that has a HEAD) is left alone, since the engine's
`pristine()` keeps it clean between runs. Run `python tool/demo_targets.py` (or `make targets`)
to materialize all; tests call `materialize(name)` instead of skipping when the target is absent.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = _ROOT / "targets" / "_src"
OUT_DIR = _ROOT / "targets"

# These throwaway local repos are NEVER pushed, so identity is cosmetic — but it must not
# inherit the host's enterprise gpgsign (no GPG key in the devcontainer → commit would fail).
_GIT_ID = ["-c", "user.email=demo@oss-bug-hunter.local", "-c", "user.name=OSS Bug Hunter",
           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def available() -> list:
    """Names of synthetic targets with tracked source under targets/_src/."""
    return sorted(p.name for p in SRC_DIR.iterdir() if p.is_dir()) if SRC_DIR.is_dir() else []


def _git(wt: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(wt), *_GIT_ID, *args],
                          capture_output=True, text=True, check=check)


def is_materialized(name: str) -> bool:
    """True when targets/<name>/ is a git repo with at least one commit (HEAD resolves)."""
    wt = OUT_DIR / name
    return (wt / ".git").is_dir() and \
        _git(wt, "rev-parse", "--verify", "HEAD", check=False).returncode == 0


def materialize(name: str, *, force: bool = False) -> Path:
    """Ensure targets/<name>/ is a git repo with the tracked source committed; return its path.

    Idempotent: a repo that already has a HEAD is returned untouched (pristine() owns its
    cleanliness). A missing/partial/non-repo working copy is rebuilt from targets/_src/<name>/.
    """
    src = SRC_DIR / name
    if not src.is_dir():
        raise FileNotFoundError(
            f"no tracked source for synthetic target {name!r}: {src} "
            f"(known: {', '.join(available()) or 'none'})")
    wt = OUT_DIR / name
    if is_materialized(name) and not force:
        return wt
    if wt.exists():
        shutil.rmtree(wt)                       # partial / non-repo / --force → rebuild from source
    shutil.copytree(src, wt)
    _git(wt, "init", "-q")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"{name}: synthetic demo target (materialized from _src)")
    return wt


def materialize_all(*, force: bool = False) -> list:
    return [materialize(n, force=force) for n in available()]


if __name__ == "__main__":
    argv = sys.argv[1:]
    force = "--force" in argv
    names = [a for a in argv if not a.startswith("-")] or available()
    if not names:
        print("[demo-targets] no source found under targets/_src/", file=sys.stderr)
        sys.exit(1)
    for n in names:
        print(f"[demo-targets] {n} -> {materialize(n, force=force)}")
