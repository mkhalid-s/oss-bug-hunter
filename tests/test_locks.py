"""#25 — G1 per-key sharded locks (pipeline.keyed_lock). Concurrency is proven with threads +
events; no network/cargo. The point: SAME key serializes (race-free), DIFFERENT keys run in
parallel, nested same-key is reentrant, and _set_gate's read-modify-write is race-free."""
import sys
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import pipeline as pl  # noqa: E402


def test_keyed_lock_reentrant_same_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "_LOCKS_DIR", tmp_path / ".locks")
    reached = False
    with pl.keyed_lock("k"):
        with pl.keyed_lock("k"):           # nested SAME key, same thread → no self-deadlock
            reached = True
    assert reached


def test_keyed_lock_different_keys_do_not_block(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "_LOCKS_DIR", tmp_path / ".locks")
    a_holds, release, b_done = threading.Event(), threading.Event(), threading.Event()

    def hold_a():
        with pl.keyed_lock("a"):
            a_holds.set()
            release.wait(3)

    t = threading.Thread(target=hold_a); t.start()
    assert a_holds.wait(3)
    with pl.keyed_lock("b"):               # DIFFERENT key while A holds → must NOT block
        b_done.set()
    assert b_done.is_set()
    release.set(); t.join(3)


def test_keyed_lock_same_key_serializes(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "_LOCKS_DIR", tmp_path / ".locks")
    a_holds, release = threading.Event(), threading.Event()
    got = []

    def hold_a():
        with pl.keyed_lock("k"):
            a_holds.set()
            release.wait(3)

    def grab_k():
        with pl.keyed_lock("k"):
            got.append("b")

    ta = threading.Thread(target=hold_a); ta.start()
    assert a_holds.wait(3)
    tb = threading.Thread(target=grab_k); tb.start()
    time.sleep(0.4)
    assert got == []                       # B blocked while A holds the SAME key
    release.set(); ta.join(3); tb.join(3)
    assert got == ["b"]                    # B proceeds once A releases


def test_set_gate_concurrent_same_finding_no_lost_update(monkeypatch, tmp_path):
    # the real race fix: two threads hammering _set_gate on the SAME scaffold (different gates).
    # Without keyed_lock the read-modify-write loses updates / tears the YAML; with it, both
    # gates always reach their final value and the file never corrupts.
    monkeypatch.setattr(pl, "_LOCKS_DIR", tmp_path / ".locks")
    sc = tmp_path / "ec-9.yaml"
    sc.write_text(yaml.safe_dump({"finding_id": "ec-9", "gates": {}}))
    N = 50

    def setter(gate):
        for i in range(N):
            pl._set_gate(sc, gate, f"s{i}", "n")

    ts = [threading.Thread(target=setter, args=(g,)) for g in ("reproducer", "fix")]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    gates = yaml.safe_load(sc.read_text())["gates"]
    assert gates["reproducer"]["status"] == f"s{N - 1}"   # both gates survived — no lost update
    assert gates["fix"]["status"] == f"s{N - 1}"


def test_pipeline_lock_still_acquires(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "CELL", tmp_path)
    monkeypatch.setattr(pl, "_LOCK_PATH", tmp_path / ".pipeline.lock")
    held = False
    with pl.pipeline_lock():
        held = True
    assert held
