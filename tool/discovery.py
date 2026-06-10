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
import time
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
    heavy/oversize repo (the headroom lesson). `native_heavy` (and `has_tests`) are populated
    for curated rows AND for GitHub candidates via enrichment (#59), so the heaviness gate now
    bites GitHub results too — a repo whose native build we can't resolve here is rejected, not
    hunted. (size + language gates always apply; native_heavy/archived stay unknown→False-safe.)"""
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


# --- GitHub enrichment (#59) -------------------------------------------------------------
# The search API gives stars/size/license but NOT the two biggest selection levers: does the
# repo have a test bed, and is it a heavy native build? We derive them from two cheap follow-up
# calls (repos/.../languages + the git tree). Heuristics are pure + unit-testable.

# A big share of these languages means a slow/heavy native build (the headroom lesson — ONNX/
# codecs). A Rust/Go crate that DOWNLOADS native deps at build time (ort-sys) won't show up in
# the language stats; a CMake/autoconf file in the tree is the secondary hint.
_NATIVE_LANGS = {"c", "c++", "cuda", "assembly", "fortran", "objective-c", "objective-c++"}
_NATIVE_TREE = re.compile(r"(^|/)(CMakeLists\.txt|configure\.ac|configure\.in|Makefile\.am)$"
                          r"|\.(cmake|cc|cxx)$")
NATIVE_FRACTION = 0.25

_TEST_PATTERNS = [
    re.compile(r"(^|/)tests?/"),                            # test/ tests/ (most ecosystems)
    re.compile(r"(^|/)src/test/"),                          # maven/gradle (java)
    re.compile(r"(^|/)spec/"),                              # rspec / jasmine
    re.compile(r"_test\.go$"),                              # go
    re.compile(r"(^|/)test_[^/]+\.py$|[^/]+_test\.py$"),    # pytest
    re.compile(r"\.(test|spec)\.(m?js|cjs|ts|tsx|jsx)$"),   # jest/node/vitest
]


def _native_heavy_from_languages(langs) -> bool:
    total = sum(_num(v) for v in (langs or {}).values())
    if total <= 0:
        return False
    native = sum(_num(v) for k, v in langs.items()
                 if str(k).strip().lower() in _NATIVE_LANGS)
    return (native / total) >= NATIVE_FRACTION


def _native_build_in_tree(paths) -> bool:
    return any(_NATIVE_TREE.search(str(p)) for p in (paths or []))


def _has_tests_in_tree(paths) -> bool:
    return any(pat.search(str(p)) for p in (paths or []) for pat in _TEST_PATTERNS)


def enrich_candidate(c: dict, detail: dict) -> dict:
    """Populate has_tests + native_heavy on a candidate from a detail dict
    {languages:{lang:bytes}, tree_paths:[...]} (GitHub repos/.../languages + git tree). Pure +
    idempotent, and it never overwrites a value a curated source already set (None = unknown)."""
    langs = (detail or {}).get("languages") or {}
    paths = (detail or {}).get("tree_paths") or []
    if c.get("has_tests") is None:
        c["has_tests"] = _has_tests_in_tree(paths)
    if c.get("native_heavy") is None:
        c["native_heavy"] = _native_heavy_from_languages(langs) or _native_build_in_tree(paths)
    return c


class RateLimitError(RuntimeError):
    """Raised by a fetcher when the API signals throttling; `retry_after` = seconds to wait."""

    def __init__(self, msg, *, retry_after: float = 2.0):
        super().__init__(msg)
        self.retry_after = retry_after


class RateLimiter:
    """Per-source pacing (#59): enforce a minimum interval between calls AND retry with backoff
    when a call raises RateLimitError (the API said 'slow down'). The backoff IS the real
    protection and is always on; `min_interval` is proactive politeness (default 0 = off, since
    a single discover run is ≤ 1 search + per_page detail calls, well under the budget).
    Injectable now()/sleep() keep tests instant + deterministic. State is per-instance → per
    source; the cross-RUN cadence is the scheduler's Budget (§12.5), not here."""

    def __init__(self, *, min_interval: float = 0.0, max_retries: int = 3,
                 now=time.monotonic, sleep=time.sleep):
        self.min_interval, self.max_retries = min_interval, max_retries
        self._now, self._sleep, self._last = now, sleep, None

    def _pace(self) -> None:
        if self._last is not None and self.min_interval > 0:
            wait = self.min_interval - (self._now() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._now()

    def call(self, fn):
        """Pace, then run fn; on RateLimitError back off `retry_after` and retry (≤ max_retries)."""
        attempt = 0
        while True:
            self._pace()
            try:
                return fn()
            except RateLimitError as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self._sleep(max(e.retry_after, 0.0))


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
    """GitHub repo search (NETWORK, read-only). Two injectable fetchers keep tests hermetic:
    `fetch(query) -> list[raw repo dict]` (the search) and `detail(candidate) -> {languages,
    tree_paths}` (the #59 enrichment). Defaults shell `gh api` with GH_TOKEN dropped + the public
    host forced (the enterprise EMU token can't see public repos). Every call goes through a
    per-source RateLimiter (#59: pacing + backoff-on-throttle). Never clones/pushes.

    search() maps each result, then — for plausibly-eligible repos ONLY (the cheap language/
    size/archived gate, so no API call is wasted on a repo discover would reject) — enriches
    has_tests + native_heavy. Enrichment is best-effort: a failed detail call leaves them
    unknown (None), which is False-safe in the gate."""
    name = "github"

    def __init__(self, query: str, *, fetch=None, detail=None, per_page: int = 30,
                 limiter=None, enrich: bool = True):
        self.query, self._fetch, self._detail = query, fetch, detail
        self.per_page, self.enrich = per_page, enrich
        self.limiter = limiter or RateLimiter()

    def search(self) -> list:
        raw = self.limiter.call(lambda: (self._fetch or self._gh_fetch)(self.query))
        cands = [self._map(r) for r in raw]
        if self.enrich:
            detail = self._detail or self._gh_detail
            for c in cands:
                if not self._enrichable(c):
                    continue            # don't spend an API call on a repo we'd reject anyway
                try:
                    enrich_candidate(c, self.limiter.call(lambda c=c: detail(c)))
                except Exception:
                    pass                # best-effort: unknown has_tests/native_heavy is fine
        return cands

    @staticmethod
    def _enrichable(c: dict) -> bool:
        # the gates that need no network — only spend a detail call on a plausibly-eligible repo.
        return (_norm_lang(c.get("language")) in SUPPORTED and not c.get("archived")
                and _num(c.get("size_kb")) <= OVERSIZE_KB)

    @staticmethod
    def _map(r: dict) -> dict:
        lic = r.get("license")
        lic = lic.get("spdx_id") if isinstance(lic, dict) else lic
        return {"repo": (r.get("full_name") or "").lower(),
                "url": r.get("html_url") or r.get("clone_url"),
                "language": r.get("language"), "license": lic,
                "stars": r.get("stargazers_count"), "pushed_at": r.get("pushed_at"),
                "size_kb": r.get("size"), "archived": r.get("archived"),
                "default_branch": r.get("default_branch"), "source": "github"}

    # ---- default network fetchers (gh CLI; #pragma: not exercised in tests — injected there) ----
    def _gh_api(self, path, *, params=None):  # pragma: no cover - network
        import os
        import subprocess
        # GH_TOKEN pins gh to the ENTERPRISE (EMU) account, which can't see public GitHub —
        # drop it so calls use the registered personal account + the public host.
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        argv = ["gh", "api", "--hostname", "github.com", "-X", "GET", path]
        for k, v in (params or {}).items():
            argv += ["-f", f"{k}={v}"]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)
        if r.returncode != 0:           # do NOT return [] silently (looks like "no results")
            msg, low = (r.stderr or "")[:300], (r.stderr or "").lower()
            if "rate limit" in low or "429" in low or "403" in low:
                raise RateLimitError(f"gh rate-limited on {path}: {msg}")
            raise RuntimeError(f"gh api {path} failed (rc={r.returncode}): {msg}")
        return json.loads(r.stdout) if (r.stdout or "").strip() else None

    def _gh_fetch(self, query):  # pragma: no cover - network
        data = self._gh_api("search/repositories",
                            params={"q": query, "per_page": str(self.per_page)})
        return (data or {}).get("items", [])

    def _gh_detail(self, c):  # pragma: no cover - network
        repo, branch = c["repo"], c.get("default_branch") or "HEAD"
        langs = self._gh_api(f"repos/{repo}/languages") or {}
        tree = self._gh_api(f"repos/{repo}/git/trees/{branch}", params={"recursive": "1"}) or {}
        return {"languages": langs,
                "tree_paths": [e.get("path", "") for e in tree.get("tree", [])]}


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
