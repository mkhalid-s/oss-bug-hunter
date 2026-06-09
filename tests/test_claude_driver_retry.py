"""Pins the WS2 resilience layer in claude_driver: is_retriable classification,
run_claude_with_retry backoff/exhaustion, and run_claude_batch ordering.

All tests monkeypatch claude_driver.run_claude (the module global that the retry
+ batch helpers call) so nothing actually spawns `claude -p`.
"""
from __future__ import annotations

import time

import claude_driver as cd


# --- is_retriable -----------------------------------------------------------

def test_success_is_not_retriable():
    assert cd.is_retriable({"returncode": 0}) is False


def test_timeout_is_retriable():
    assert cd.is_retriable({"returncode": -1, "timed_out": True}) is True


def test_transient_stderr_is_retriable():
    for marker in ("API Error 429", "rate limit exceeded", "503 Service",
                   "overloaded_error", "connection reset", "timed out"):
        assert cd.is_retriable({"returncode": 1, "stderr": marker}) is True, marker


def test_terminal_stderr_is_not_retriable():
    for marker in ("invalid api key", "authentication failed",
                   "validation error: unknown model", "permission denied"):
        assert cd.is_retriable({"returncode": 1, "stderr": marker}) is False, marker


def test_user_interrupt_is_not_retriable():
    # A Ctrl-C mid-run must not trigger auto-retry even though it failed.
    assert cd.is_retriable(
        {"returncode": -1, "timed_out": True, "interrupted": True}
    ) is False


# --- run_claude_with_retry --------------------------------------------------

def _patch(monkeypatch, fn):
    monkeypatch.setattr(cd, "run_claude", fn)


def test_retry_then_succeed(monkeypatch):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"returncode": 1, "stderr": "503 service unavailable"}
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    _patch(monkeypatch, fake)
    r = cd.run_claude_with_retry("p", max_retries=2, _sleep=lambda s: None)
    assert r["returncode"] == 0
    assert r["attempts"] == 3
    assert "retries_exhausted" not in r


def test_retry_exhausted_preserves_last_failure(monkeypatch):
    _patch(monkeypatch, lambda prompt, **kw: {"returncode": 1, "stderr": "throttled"})
    r = cd.run_claude_with_retry("p", max_retries=2, _sleep=lambda s: None)
    assert r["returncode"] == 1
    assert r["attempts"] == 3          # 1 initial + 2 retries
    assert r["retries_exhausted"] is True


def test_terminal_error_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return {"returncode": 1, "stderr": "authentication failed"}

    _patch(monkeypatch, fake)
    r = cd.run_claude_with_retry("p", max_retries=5, _sleep=lambda s: None)
    assert calls["n"] == 1
    assert r["attempts"] == 1


def test_backoff_schedule_is_exponential(monkeypatch):
    delays = []
    _patch(monkeypatch, lambda prompt, **kw: {"returncode": 1, "stderr": "timeout"})
    cd.run_claude_with_retry(
        "p", max_retries=3, backoff_s=10, backoff_factor=2.0,
        _sleep=delays.append,
    )
    # 3 retries -> 3 sleeps at 10, 20, 40
    assert delays == [10.0, 20.0, 40.0]


# --- run_claude_batch -------------------------------------------------------

def test_batch_preserves_order(monkeypatch):
    _patch(monkeypatch, lambda prompt, **kw: {"returncode": 0, "stdout": prompt})
    jobs = [{"prompt": f"p{i}", "key": f"k{i}"} for i in range(5)]
    res = cd.run_claude_batch(jobs, max_parallel=5, with_retry=False)
    assert [r["key"] for r in res] == [f"k{i}" for i in range(5)]
    assert [r["stdout"] for r in res] == [f"p{i}" for i in range(5)]
    assert [r["index"] for r in res] == list(range(5))


def test_batch_runs_concurrently(monkeypatch):
    def slow(prompt, **kw):
        time.sleep(0.05)
        return {"returncode": 0, "stdout": prompt}

    _patch(monkeypatch, slow)
    jobs = [{"prompt": f"p{i}"} for i in range(6)]
    t0 = time.time()
    cd.run_claude_batch(jobs, max_parallel=6, with_retry=False)
    # 6 x 50ms serial would be ~300ms; concurrent should be well under 150ms.
    assert time.time() - t0 < 0.15


def test_batch_per_job_kwargs_override_common(monkeypatch):
    seen = []
    _patch(monkeypatch, lambda prompt, **kw: seen.append((prompt, kw.get("model"))) or {"returncode": 0})
    jobs = [{"prompt": "a", "model": "opus"}, {"prompt": "b"}]
    cd.run_claude_batch(jobs, max_parallel=2, with_retry=False, model="haiku")
    seen_by_prompt = dict(seen)
    assert seen_by_prompt["a"] == "opus"   # per-job wins
    assert seen_by_prompt["b"] == "haiku"  # falls back to common
