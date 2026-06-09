"""Candidate-repo DISCOVERY + ranking (plan §12.3, Phase 3 — the outer-loop INPUT).

Proposes + ranks OSS repos worth hunting and enqueues them for the scheduler (§12.5)
to clone (via targets.add_target) and run. Sources are pluggable; ranking is cheap and
NON-AI (adapter-supported language, has a test bed, permissive license, active, not
too heavy — the headroom lesson). Network sources are injectable so the SELECTION logic
stays hermetic + testable; nothing here clones, runs, or pushes.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

import yaml

import adapters as _ad
import targets as _tg

ROOT = Path(__file__).resolve().parents[1]
CELL = ROOT / "cell-1"
QUEUE = CELL / "hunt" / "discovery-queue.yaml"

# languages our engine can actually hunt (kept in sync with adapters + Java).
SUPPORTED = {"java"} | set(_ad._ADAPTERS.keys())
_LANG_ALIAS = {"typescript": "javascript", "ts": "javascript", "js": "javascript",
               "node": "javascript", "golang": "go", "rs": "rust", "py": "python"}
_PERMISSIVE = {"mit", "apache-2.0", "apache 2.0", "apache", "bsd-3-clause",
               "bsd-2-clause", "bsd", "isc", "mpl-2.0"}
MIN_SCORE = 0.0


def _norm_lang(lang) -> str:
    s = str(lang or "").strip().lower()
    return _LANG_ALIAS.get(s, s)


def _permissive(lic) -> bool:
    return str(lic or "").strip().lower() in _PERMISSIVE


def _recent(pushed_at, *, days: int = 365) -> bool:
    try:
        t = _dt.datetime.fromisoformat(str(pushed_at).replace("Z", "+00:00"))
    except Exception:
        return False
    return (_dt.datetime.now(_dt.timezone.utc) - t).days <= days


def _owner_repo(url) -> str | None:
    m = re.match(r"^(?:https?://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$",
                 url or "")
    return m.group(1) if m else None


def _canon(s) -> str:
    """Canonical repo key across transports (github/gitlab/ssh/file) so dedup +
    existing-target exclusion cover every URL form `targets.add_target` accepts. The
    github default host is stripped → bare owner/name (so a URL and a bare repo match)."""
    s = (s or "").strip().lower().rstrip("/")
    s = re.sub(r"^(?:https?://|git@|ssh://(?:git@)?|file://)", "", s)
    s = s.replace(":", "/")
    s = re.sub(r"\.git$", "", s)
    return re.sub(r"^github\.com/", "", s)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


OVERSIZE_KB = 500_000


def _eligible(c: dict) -> bool:
    """HARD GATE (not a score term): only enqueue repos the engine can actually hunt
    and build here. A high star count must NOT rescue an unsupported language or a
    heavy/oversize repo (the headroom lesson). NOTE: `native_heavy`/`has_tests` are only
    known for CURATED (JsonSource) rows today — GitHub search can't populate them, so
    enrichment is a follow-on; the language + size gates DO apply to GitHub candidates."""
    if _norm_lang(c.get("language")) not in SUPPORTED:
        return False
    if c.get("native_heavy") or c.get("archived"):
        return False
    return _num(c.get("size_kb")) <= OVERSIZE_KB


def _repo_key(c: dict) -> str:
    return _canon(c.get("repo") or c.get("url"))


def score_candidate(c: dict) -> float:
    """Transparent, non-AI score (higher = more worth hunting). The biggest levers are
    'can we even hunt it' (adapter-supported language) and 'is there a test bed' — the
    blog's #1 efficacy lever. Heavy native repos are penalized (the headroom lesson)."""
    lang = _norm_lang(c.get("language"))
    score = 3.0 if lang in SUPPORTED else -5.0     # must be a language we can run
    if c.get("has_tests"):
        score += 2.0                               # a test bed to validate against
    if _permissive(c.get("license")):
        score += 1.0
    if _recent(c.get("pushed_at")):
        score += 1.0
    if c.get("native_heavy"):                      # ONNX/codecs/build-time downloads
        score -= 3.0
    if _num(c.get("size_kb")) > OVERSIZE_KB:       # very large clone/build
        score -= 2.0
    if c.get("archived"):
        score -= 5.0
    score += min(_num(c.get("stars")) / 1000.0, 2.0)   # mild popularity signal (cap +2)
    return round(score, 2)


class Source:
    """A discovery source returning candidate dicts: {repo|url, language, license?,
    stars?, pushed_at?, has_tests?, size_kb?, native_heavy?, archived?, source?}."""
    name = "source"

    def search(self) -> list:
        raise NotImplementedError


class JsonSource(Source):
    """Hermetic source: a JSON file holding a list (or {"candidates": [...]}). Doubles
    as the manual-curation path and the test fixture."""

    def __init__(self, path, name: str = "json"):
        self.path, self.name = Path(path), name

    def search(self) -> list:
        doc = json.loads(self.path.read_text())
        rows = doc.get("candidates", []) if isinstance(doc, dict) else doc
        return [{**r, "source": r.get("source", self.name)}
                for r in (rows or []) if isinstance(r, dict)]


class GitHubSearchSource(Source):
    """GitHub repo search (NETWORK, read-only). `fetch(query) -> list[raw repo dict]`
    is injectable so tests stay hermetic; the default shells `gh api
    search/repositories` with GH_TOKEN dropped + the public host forced (the
    enterprise EMU token can't see public repos). Never clones/pushes. NOTE:
    per-source RATE-LIMITING is the scheduler's job (§12.5) and is NOT enforced
    here yet — single-query use only until then."""
    name = "github"

    def __init__(self, query: str, *, fetch=None, per_page: int = 30):
        self.query, self._fetch, self.per_page = query, fetch, per_page

    def search(self) -> list:
        return [self._map(r) for r in (self._fetch or self._gh_fetch)(self.query)]

    @staticmethod
    def _map(r: dict) -> dict:
        lic = r.get("license")
        lic = lic.get("spdx_id") if isinstance(lic, dict) else lic
        return {"repo": (r.get("full_name") or "").lower(),
                "url": r.get("html_url") or r.get("clone_url"),
                "language": r.get("language"), "license": lic,
                "stars": r.get("stargazers_count"), "pushed_at": r.get("pushed_at"),
                "size_kb": r.get("size"), "archived": r.get("archived"), "source": "github"}

    def _gh_fetch(self, query):  # pragma: no cover - network path, not run in tests
        import os
        import subprocess
        # GH_TOKEN pins gh to the ENTERPRISE (EMU) account, which can't see public
        # GitHub — drop it so search uses the registered personal account + public host.
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        r = subprocess.run(["gh", "api", "--hostname", "github.com", "-X", "GET",
                            "search/repositories", "-f", f"q={query}",
                            "-f", f"per_page={self.per_page}"],
                           capture_output=True, text=True, timeout=30, env=env)
        if r.returncode != 0:           # do NOT return [] silently (looks like "no results")
            raise RuntimeError(f"gh search failed (rc={r.returncode}): {(r.stderr or '')[:200]}")
        return (json.loads(r.stdout) or {}).get("items", [])


def discover(sources, *, limit: int = 20, denylist=(), allowlist=None, existing=None,
             min_score: float = MIN_SCORE, log=None) -> list:
    """Gather → dedup → filter (allow/deny + already-a-target) → HARD-GATE (_eligible)
    → score → rank → cap. `existing` defaults to current targets' repos (don't
    re-propose them). Raises on a config/data source error (bad JSON); tolerates a
    flaky network source (logged via `log` if given). Returns ranked candidate dicts."""
    deny = {_canon(d) for d in denylist}
    allow = {_canon(a) for a in allowlist} if allowlist is not None else None
    if existing is None:
        existing = {_canon(t.get("repo")) for t in _tg.list_targets() if t.get("repo")}
    else:
        existing = {_canon(e) for e in existing}
    seen, out = set(), []
    for src in sources:
        try:
            rows = src.search()
        except (FileNotFoundError, json.JSONDecodeError):
            raise                       # config/data error (e.g. bad --json) — fail LOUD
        except Exception as e:
            if log:                     # surface tolerated source errors (e.g. GH auth) — not silent
                log(f"[discover] source {getattr(src, 'name', '?')} failed: {e}")
            continue                    # tolerate a flaky source (e.g. network) — don't kill the batch
        for c in rows:
            if not isinstance(c, dict):
                continue
            key = _repo_key(c)
            if not key or key in seen:
                continue
            seen.add(key)
            if key in deny or key in existing:
                continue
            if allow is not None and key not in allow and key.split("/")[0] not in allow:
                continue
            if not _eligible(c):        # HARD GATE: skip what we can't hunt/build here (P0)
                continue
            scored = {**c, "repo": key, "score": score_candidate(c)}
            if scored["score"] >= min_score:
                out.append(scored)
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:limit]


def enqueue(candidates, path=QUEUE) -> dict:
    """Persist the ranked queue for the scheduler (§12.5) to consume. Does NOT clone."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(
        {"generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
         "count": len(candidates), "candidates": candidates}, sort_keys=False))
    return {"ok": True, "count": len(candidates), "queue": str(path)}
