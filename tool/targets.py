"""Target front-door (plan §3.7 / engine Phase C): add an OSS repo by URL, detect
its language, and track it.

Metadata lives in a SIDECAR `targets/_meta/<name>.yaml`, NOT inside the clone —
the validators run `git clean -fdq` in the worktree, which would wipe an in-repo
`target.yaml`. The sidecar survives that.

Trust is FAIL-CLOSED (G3): a freshly added target is `trusted: false`, so the
`local` execution backend refuses it until an operator explicitly flips the flag.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGETS_DIR = ROOT / "targets"
META_DIR = TARGETS_DIR / "_meta"

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
# Transport allowlist: https/ssh/git + file:// (the last is benign for a
# single-user local tool and handy for local repos). ext::/fd:: are REFUSED —
# those run arbitrary commands. Defense beyond git's own protocol.* defaults.
_URL_OK = re.compile(r"^(https?://|git@[\w.-]+:|ssh://|file://)", re.I)


def _safe_name(name: str) -> bool:
    """A target name must be a single safe path component — never a traversal."""
    return bool(name) and _SAFE.match(name) is not None \
        and name not in (".", "..") and "/" not in name and "\\" not in name


def _under_targets(name: str) -> bool:
    try:
        return (TARGETS_DIR / name).resolve().parent == TARGETS_DIR.resolve()
    except Exception:
        return False

_DETECTORS = [
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
    ("go", ("go.mod",)),
    ("rust", ("Cargo.toml",)),
    ("javascript", ("package.json",)),
]
_ADAPTER = {"java": "java-maven", "python": "python-pytest", "go": "go-test",
            "rust": "cargo-test", "javascript": "js-jest"}


def detect_language(repo_path) -> str:
    """ROOT-ONLY primary guess (first match by _DETECTORS order). For a polyglot
    monorepo this returns just one language and can pick the 'wrong' one (e.g. a
    repo with both root pyproject.toml and Cargo.toml → python). Use
    detect_components() for the full per-component picture."""
    p = Path(repo_path)
    for lang, markers in _DETECTORS:
        if any((p / m).exists() for m in markers):
            return lang
    return "unknown"


# Flattened (manifest filename → language), preserving _DETECTORS precedence so a
# directory with several manifests resolves to one language deterministically.
_MANIFEST_LANG = [(m, lang) for lang, markers in _DETECTORS for m in markers]
# Never descend into these when scanning a monorepo (vendored deps / build output).
_SKIP_DIRS = {".git", "node_modules", "target", "vendor", "dist", "build", ".venv",
              "venv", "__pycache__", ".next", ".cargo", ".tox", "site-packages"}


def detect_components(repo_path, max_depth: int = 4) -> list:
    """Monorepo-aware detection (M5/discovery): find EVERY language component by
    its manifest, not just the repo root. headroom (the pilot) is the motivating
    case — a Rust workspace + a root Python pkg + several TS package.json's, where
    detect_language() returns only 'python'. Returns [{language, dir, manifest}]
    sorted shallowest-first; one language per directory (first by _DETECTORS order).
    Bounded depth + skip-dirs keep it cheap on large repos."""
    root = Path(repo_path).resolve()
    out, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        fset = set(filenames)
        for marker, lang in _MANIFEST_LANG:
            if marker in fset:
                key = str(rel) or "."
                if key not in seen:
                    seen.add(key)
                    out.append({"language": lang, "dir": key, "manifest": marker})
                break                      # one language per directory
    out.sort(key=lambda c: (c["dir"].count(os.sep), c["dir"]))
    return out


def _git_sha(path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _meta_path(name: str) -> Path:
    return META_DIR / f"{name}.yaml"


def _read_meta(name: str) -> dict:
    p = _meta_path(name)
    if p.exists():
        try:
            return yaml.safe_load(p.read_text()) or {}
        except Exception:
            return {}
    return {}


def _describe(name: str, d: Path) -> dict:
    meta = _read_meta(name)
    lang = meta.get("language") or detect_language(d)
    return {
        "name": name,
        "language": lang,
        "adapter": meta.get("adapter") or _ADAPTER.get(lang),
        "sha": _git_sha(d) or meta.get("sha"),
        "repo": meta.get("repo"),
        "trusted": bool(meta.get("trusted", False)),
        "is_git": (d / ".git").exists(),
        "has_meta": _meta_path(name).exists(),
    }


def list_targets() -> list:
    out = []
    if not TARGETS_DIR.is_dir():
        return out
    for d in sorted(TARGETS_DIR.iterdir()):
        if not d.is_dir() or d.name == "_meta" or d.name.startswith("."):
            continue
        out.append(_describe(d.name, d))
    return out


def get_target(name: str) -> dict | None:
    if not _safe_name(name or "") or not _under_targets(name):
        return None
    d = TARGETS_DIR / name
    return _describe(name, d) if d.is_dir() else None


def _derive_name(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base or "target"


def _stream(argv, log, env=None) -> int:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def add_target(url: str, *, name: str | None = None, sha: str | None = None,
               trusted: bool = False, log=print) -> dict:
    if not url or url.startswith("-"):        # arg-injection guard
        raise ValueError(f"invalid repo URL: {url!r}")
    if not _URL_OK.match(url):                # transport allowlist (no ext::/file::)
        raise ValueError(f"unsupported URL scheme (use https/ssh): {url!r}")
    name = name or _derive_name(url)
    if not _safe_name(name) or not _under_targets(name):   # path-traversal guard
        raise ValueError(f"unsafe target name: {name!r}")
    dest = TARGETS_DIR / name
    if dest.exists():
        raise ValueError(f"target already exists: {name}")
    META_DIR.mkdir(parents=True, exist_ok=True)
    log(f"[target] cloning {url} -> targets/{name} …")
    try:
        # `--` stops the URL being parsed as a flag; restrict protocols hard.
        env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:ssh:git:file",
               "GIT_TERMINAL_PROMPT": "0"}
        if _stream(["git", "clone", "--progress", "--", url, str(dest)], log, env=env) != 0:
            raise RuntimeError("git clone failed")
        if sha:
            log(f"[target] checking out {sha}")
            if _stream(["git", "-C", str(dest), "checkout", "--detach", sha], log) != 0:
                raise RuntimeError(f"git checkout {sha} failed")
        lang = detect_language(dest)
        cur = _git_sha(dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)   # no half-written target dir
        raise
    meta = {"name": name, "repo": url, "sha": cur, "language": lang,
            "adapter": _ADAPTER.get(lang), "trusted": bool(trusted)}
    _meta_path(name).write_text(yaml.safe_dump(meta, sort_keys=False))
    log(f"[target] added: language={lang} sha={cur} trusted={trusted}")
    return {"name": name, "language": lang, "sha": cur, "trusted": bool(trusted)}
