"""Phase 3 §12.3 — candidate discovery + ranking. Hermetic: JsonSource fixtures +
an injected GitHub fetch (no network). Asserts the non-AI selection logic."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import discovery as d   # noqa: E402


def _src(tmp_path, rows):
    p = tmp_path / "cands.json"
    p.write_text(json.dumps(rows))
    return d.JsonSource(str(p))


def test_score_levers():
    base = {"language": "python"}
    assert d.score_candidate({**base, "has_tests": True}) > d.score_candidate(base)   # test bed
    assert d.score_candidate({"language": "c++"}) < 0                                  # unsupported
    assert d.score_candidate({**base, "native_heavy": True}) < d.score_candidate(base)  # headroom penalty
    assert d.score_candidate({**base, "archived": True}) < 0                            # archived sinks


def test_discover_ranks_filters_dedups(tmp_path):
    rows = [
        {"repo": "o/good", "language": "python", "has_tests": True, "license": "MIT"},
        {"repo": "o/cpp", "language": "C++", "has_tests": True},                    # unsupported -> dropped
        {"repo": "o/heavy", "language": "rust", "native_heavy": True, "size_kb": 900000},  # dropped (<0)
        {"repo": "o/good", "language": "python"},                                   # dup -> ignored
        {"repo": "o/ok", "language": "go"},
    ]
    out = d.discover([_src(tmp_path, rows)], existing=set())
    keys = [c["repo"] for c in out]
    assert keys[0] == "o/good"                              # highest score first
    assert "o/cpp" not in keys and "o/heavy" not in keys    # filtered by score
    assert keys.count("o/good") == 1 and "o/ok" in keys     # deduped + kept


def test_discover_existing_deny_allow(tmp_path):
    rows = [{"repo": "o/a", "language": "go"}, {"repo": "o/b", "language": "go"},
            {"repo": "x/c", "language": "go"}]
    src = _src(tmp_path, rows)
    assert "o/a" not in [c["repo"] for c in d.discover([src], existing={"o/a"})]
    assert "o/b" not in [c["repo"] for c in d.discover([src], existing=set(), denylist=["o/b"])]
    assert [c["repo"] for c in d.discover([src], existing=set(), allowlist=["x"])] == ["x/c"]  # by owner


def test_discover_cap(tmp_path):
    rows = [{"repo": f"o/r{i}", "language": "go"} for i in range(10)]
    assert len(d.discover([_src(tmp_path, rows)], existing=set(), limit=3)) == 3


def test_json_source_shapes(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([{"repo": "o/x", "language": "go"}]))
    (tmp_path / "b.json").write_text(json.dumps({"candidates": [{"repo": "o/y", "language": "go"}]}))
    assert d.JsonSource(str(tmp_path / "a.json")).search()[0]["repo"] == "o/x"
    assert d.JsonSource(str(tmp_path / "b.json")).search()[0]["repo"] == "o/y"


def test_github_source_maps_injected_fetch():
    raw = [{"full_name": "O/Repo", "html_url": "https://github.com/O/Repo", "language": "Go",
            "license": {"spdx_id": "MIT"}, "stargazers_count": 42,
            "pushed_at": "2026-05-01T00:00:00Z", "size": 100}]
    c = d.GitHubSearchSource("q", fetch=lambda q: raw).search()[0]
    assert c["repo"] == "o/repo" and c["language"] == "Go" and c["license"] == "MIT" and c["stars"] == 42


def test_enqueue_writes(tmp_path):
    out = tmp_path / "q.yaml"
    r = d.enqueue([{"repo": "o/x", "score": 5}], path=out)
    assert r["ok"] and r["count"] == 1 and out.exists()
    assert yaml.safe_load(out.read_text())["candidates"][0]["repo"] == "o/x"


def test_discover_fails_loud_on_bad_source(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):              # missing --json must NOT silently yield 0
        d.discover([d.JsonSource(str(tmp_path / "nope.json"))], existing=set())
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ValueError):                     # malformed JSON likewise (fail loud)
        d.discover([d.JsonSource(str(tmp_path / "bad.json"))], existing=set())


def test_hard_gate_beats_stars(tmp_path):
    # P0 review fix: language-support + heaviness are HARD GATES — a huge star count
    # must NOT rescue a repo we can't hunt/build (additive score is no longer enough).
    rows = [
        {"repo": "o/kotlin", "language": "Kotlin", "has_tests": True, "license": "MIT",
         "pushed_at": "2026-05-01T00:00:00Z", "stars": 50000},      # unsupported lang
        {"repo": "o/heavy", "language": "rust", "has_tests": True, "stars": 50000,
         "native_heavy": True},                                     # heavy native (headroom)
        {"repo": "o/huge", "language": "go", "has_tests": True, "stars": 50000,
         "size_kb": 900000},                                        # oversize
        {"repo": "o/good", "language": "go", "stars": 10},
    ]
    keys = [c["repo"] for c in d.discover([_src(tmp_path, rows)], existing=set())]
    assert keys == ["o/good"]      # only the eligible one, despite the others' 50k stars


def test_canon_dedups_across_transports(tmp_path):
    # P1 review fix: dedup + existing-exclusion must cover every transport, not just
    # github owner/name. A github URL and a bare repo are the same; a gitlab target
    # excludes a gitlab candidate.
    rows = [
        {"url": "https://github.com/O/Repo", "language": "go"},     # → o/repo
        {"repo": "o/repo", "language": "go"},                       # dup of the above
        {"url": "https://gitlab.com/o/lib", "language": "go"},       # non-github → kept
    ]
    out = d.discover([_src(tmp_path, rows)], existing={"git@gitlab.com:o/lib.git"})
    keys = sorted(c["repo"] for c in out)
    assert keys == ["o/repo"]      # github dup collapsed; gitlab one excluded by existing


def test_malformed_candidate_tolerated(tmp_path):
    rows = [
        {"repo": "o/ok", "language": "go", "size_kb": "huge", "stars": "lots"},  # bad numerics
        "not-a-dict",                                                            # ragged row
        {"repo": "o/two", "language": "python"},
    ]
    keys = {c["repo"] for c in d.discover([_src(tmp_path, rows)], existing=set())}
    assert keys == {"o/ok", "o/two"}   # string numerics coerced to 0, non-dict skipped — no crash


# ---- #59: GitHub enrichment (has_tests / native_heavy) + per-source rate-limiting ----

def test_native_heavy_from_languages():
    assert d._native_heavy_from_languages({"C++": 800, "Python": 200}) is True    # 80% native
    assert d._native_heavy_from_languages({"Python": 1000, "C": 50}) is False      # <25% native
    assert d._native_heavy_from_languages({"Go": 1000}) is False
    assert d._native_heavy_from_languages({}) is False                             # unknown → not heavy


def test_has_tests_in_tree():
    assert d._has_tests_in_tree(["src/main.go", "foo_test.go"])                    # go
    assert d._has_tests_in_tree(["src/test/java/AppTest.java"])                    # maven layout
    assert d._has_tests_in_tree(["lib/index.js", "index.test.ts"])                 # jest/vitest
    assert d._has_tests_in_tree(["tests/test_x.py"])                               # pytest dir
    assert not d._has_tests_in_tree(["src/main.go", "README.md"])                  # none


def test_enrich_candidate_merges_without_clobbering():
    c = {"repo": "o/x", "language": "go"}                                          # both unknown
    d.enrich_candidate(c, {"languages": {"Go": 1000}, "tree_paths": ["foo_test.go"]})
    assert c["has_tests"] is True and c["native_heavy"] is False
    # a value a curated source already set is preserved (only None = unknown gets filled)
    c2 = {"has_tests": False, "native_heavy": True}
    d.enrich_candidate(c2, {"languages": {"Go": 1000}, "tree_paths": ["foo_test.go"]})
    assert c2["has_tests"] is False and c2["native_heavy"] is True
    # CMake in the tree flags native_heavy even when languages look benign
    c3 = {"repo": "o/y", "language": "rust"}
    d.enrich_candidate(c3, {"languages": {"Rust": 1000}, "tree_paths": ["CMakeLists.txt"]})
    assert c3["native_heavy"] is True


def test_enrich_candidate_truncated_tree_leaves_unknown():
    # review P2: a TRUNCATED git tree is partial → don't derive a false has_tests/native=False.
    c = {"repo": "o/big", "language": "go"}
    d.enrich_candidate(c, {"languages": {"Go": 1000}, "tree_paths": [], "tree_truncated": True})
    assert c.get("has_tests") is None and c.get("native_heavy") is None   # unknown, not False
    # the languages API is complete → it can still rule native_heavy IN despite a truncated tree
    c2 = {"repo": "o/cpp", "language": "rust"}
    d.enrich_candidate(c2, {"languages": {"C++": 900, "Rust": 100}, "tree_paths": [],
                            "tree_truncated": True})
    assert c2["native_heavy"] is True


def test_github_source_enriches_only_eligible_repos():
    raw = [
        {"full_name": "o/go", "language": "Go", "size": 100, "default_branch": "main"},
        {"full_name": "o/cpp", "language": "C++", "size": 100},        # unsupported → don't enrich
        {"full_name": "o/huge", "language": "go", "size": 900000},     # oversize → don't enrich
    ]
    seen = []

    def detail(c):
        seen.append(c["repo"])
        return {"languages": {"Go": 1000}, "tree_paths": ["x_test.go"]}

    cands = d.GitHubSearchSource("q", fetch=lambda q: raw, detail=detail).search()
    by = {c["repo"]: c for c in cands}
    assert by["o/go"]["has_tests"] is True                             # eligible repo enriched
    assert seen == ["o/go"]                                            # ONLY it cost a detail call


def test_github_enrichment_best_effort_on_detail_failure():
    raw = [{"full_name": "o/go", "language": "Go", "size": 100}]

    def boom(c):
        raise RuntimeError("network down")

    c = d.GitHubSearchSource("q", fetch=lambda q: raw, detail=boom).search()[0]
    assert c.get("has_tests") is None and c.get("native_heavy") is None   # unknown, no crash


def test_github_enrichment_feeds_hard_gate(tmp_path):
    # an enriched native-heavy GitHub repo is REJECTED by discover's hard gate.
    raw = [{"full_name": "o/native", "language": "rust", "size": 100, "default_branch": "main"}]
    src = d.GitHubSearchSource(
        "q", fetch=lambda q: raw,
        detail=lambda c: {"languages": {"C++": 900, "Rust": 100}, "tree_paths": ["CMakeLists.txt"]})
    assert d.discover([src], existing=set()) == []                     # native_heavy → gated out


def test_rate_limiter_paces_with_fake_clock():
    clock, slept = {"t": 0.0}, []
    rl = d.RateLimiter(min_interval=10.0, now=lambda: clock["t"], sleep=lambda s: slept.append(s))
    assert rl.call(lambda: "a") == "a" and slept == []                 # first call: no pacing
    rl.call(lambda: "b")                                               # immediately after → sleep ~10
    assert len(slept) == 1 and abs(slept[0] - 10.0) < 1e-9


def test_rate_limiter_backoff_then_succeed_and_giveup():
    slept, calls = [], {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise d.RateLimitError("slow down", retry_after=5.0)
        return "ok"

    rl = d.RateLimiter(max_retries=2, now=lambda: 0.0, sleep=lambda s: slept.append(s))
    assert rl.call(flaky) == "ok" and calls["n"] == 3 and slept == [5.0, 5.0]   # 2 backoffs, then ok

    import pytest

    def always():
        raise d.RateLimitError("nope")
    rl2 = d.RateLimiter(max_retries=1, now=lambda: 0.0, sleep=lambda s: None)
    with pytest.raises(d.RateLimitError):                              # exceeds retries → re-raised
        rl2.call(always)
