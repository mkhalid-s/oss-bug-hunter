"""Job/run store + worker + pub-sub broker — the seam (plan §4.2).

Turns fire-and-toast actions into runs with IDs, persisted status, and a live
log stream. Single-user laptop scope: in-process ThreadPoolExecutor (no broker),
SQLite (WAL) with a single serialized writer (G5), per-run pub/sub with the
SQLite log table doubling as the replay buffer.

Framework-agnostic: subscribers register a plain callable `sink(event)`. The web
layer adapts that to asyncio via loop.call_soon_threadsafe, so this module never
imports asyncio.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

TERMINAL = {"done", "error", "interrupted", "cancelled"}

_DB: Optional[sqlite3.Connection] = None
_LOCK = threading.RLock()            # G5: single serialized writer/reader
_POOL = ThreadPoolExecutor(max_workers=2)   # §4.2: small worker pool
_subs: dict = {}                     # run_id -> set[sink]
_subs_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, kind TEXT, params TEXT, status TEXT,
  step TEXT, exit_code INTEGER, backend TEXT, error TEXT,
  started TEXT, finished TEXT
);
CREATE TABLE IF NOT EXISTS run_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, stream TEXT, line TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_run ON run_logs(run_id, id);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def init_db(path: str) -> None:
    global _DB
    with _LOCK:
        if _DB is not None:                 # close-before-reopen (tests/hot-reload)
            try:
                _DB.close()
            except Exception:
                pass
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.execute("PRAGMA journal_mode=WAL")
        _DB.execute("PRAGMA synchronous=NORMAL")
        _DB.executescript(_SCHEMA)
        # startup reconciliation: no zombie "live" runs survive a restart
        _DB.execute("UPDATE runs SET status='interrupted', finished=? "
                    "WHERE status IN ('running','queued')", (_now(),))
        _DB.commit()


def _exec(sql: str, args=()):
    with _LOCK:
        cur = _DB.execute(sql, args)
        _DB.commit()
        return cur


def create_run(kind: str, params: dict) -> str:
    rid = uuid.uuid4().hex[:12]
    _exec("INSERT INTO runs(id,kind,params,status,started) VALUES(?,?,?,?,?)",
          (rid, kind, json.dumps(params), "queued", _now()))
    return rid


def set_status(run_id: str, status: str, *, step=None, exit_code=None,
               backend=None, error=None, finished=False) -> None:
    fin = _now() if finished else None
    _exec("UPDATE runs SET status=?, step=COALESCE(?,step), "
          "exit_code=COALESCE(?,exit_code), backend=COALESCE(?,backend), "
          "error=COALESCE(?,error), finished=COALESCE(?,finished) WHERE id=?",
          (status, step, exit_code, backend, error, fin, run_id))


def append_log(run_id: str, line: str, stream: str = "stdout") -> int:
    with _LOCK:
        cur = _DB.execute(
            "INSERT INTO run_logs(run_id,stream,line,ts) VALUES(?,?,?,?)",
            (run_id, stream, line, _now()))
        seq = cur.lastrowid
        _DB.commit()
    _publish(run_id, {"type": "log", "seq": seq, "stream": stream, "line": line})
    return seq


def get_run(run_id: str) -> Optional[dict]:
    with _LOCK:
        cur = _DB.execute(
            "SELECT id,kind,params,status,step,exit_code,backend,error,started,finished "
            "FROM runs WHERE id=?", (run_id,))
        row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "kind", "params", "status", "step", "exit_code", "backend",
            "error", "started", "finished"]
    d = dict(zip(keys, row))
    d["params"] = json.loads(d["params"] or "{}")
    return d


def list_runs(limit: int = 50) -> list:
    with _LOCK:
        cur = _DB.execute(
            "SELECT id,kind,status,exit_code,backend,started,finished "
            "FROM runs ORDER BY started DESC, id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    keys = ["id", "kind", "status", "exit_code", "backend", "started", "finished"]
    return [dict(zip(keys, r)) for r in rows]


def get_logs(run_id: str, after: int = 0) -> list:
    """Replay buffer: persisted logs with id > after (Last-Event-ID)."""
    with _LOCK:
        cur = _DB.execute(
            "SELECT id,stream,line FROM run_logs WHERE run_id=? AND id>? ORDER BY id",
            (run_id, after))
        return cur.fetchall()


# ---- pub/sub broker ----
def subscribe(run_id: str, sink: Callable) -> None:
    with _subs_lock:
        _subs.setdefault(run_id, set()).add(sink)


def unsubscribe(run_id: str, sink: Callable) -> None:
    with _subs_lock:
        s = _subs.get(run_id)
        if s:
            s.discard(sink)
            if not s:
                _subs.pop(run_id, None)


def _publish(run_id: str, event: dict) -> None:
    with _subs_lock:
        sinks = list(_subs.get(run_id, ()))
    for s in sinks:
        try:
            s(event)
        except Exception:
            pass


# ---- worker ----
def submit_job(kind: str, params: dict, fn: Callable) -> str:
    """fn(run_id, emit) -> dict (may include exit/backend/outcome). emit(line[,stream])."""
    run_id = create_run(kind, params)
    _POOL.submit(_run, run_id, fn)
    return run_id


def _run(run_id: str, fn: Callable) -> None:
    set_status(run_id, "running")
    _publish(run_id, {"type": "status", "status": "running"})

    def emit(line: str, stream: str = "stdout") -> None:
        append_log(run_id, line, stream)

    try:
        result = fn(run_id, emit) or {}
        set_status(run_id, "done", exit_code=result.get("exit", 0),
                   backend=result.get("backend"), finished=True)
        _publish(run_id, {"type": "done", "status": "done", **result})
    except Exception as e:  # noqa: BLE001
        append_log(run_id, f"[run] ERROR {e!r}", "stderr")
        set_status(run_id, "error", error=str(e), finished=True)
        _publish(run_id, {"type": "done", "status": "error", "error": str(e)})
