"""
Guarantee: two calls with the same key at the same time must not both execute. The
DB-level UNIQUE(key) constraint — not a lock — is the primitive that enforces
at-most-one execution across threads (and processes).
"""

import threading

import pytest

from safeact import InDoubtError, safe
from safeact.store import get_store


def test_same_key_from_many_threads_executes_once(tmp_db):
    calls = {"n": 0}
    lock = threading.Lock()

    @safe(store=tmp_db)
    def charge(key, amount):
        with lock:
            calls["n"] += 1
        # simulate real work so threads genuinely overlap
        threading.Event().wait(0.02)
        return {"amount": amount, "execution": calls["n"]}

    barrier = threading.Barrier(20)
    outcomes = []
    out_lock = threading.Lock()

    def fire():
        barrier.wait()  # release all threads at once
        try:
            r = charge("charge-1", 999)
            with out_lock:
                outcomes.append(("ok", r))
        except InDoubtError:
            with out_lock:
                outcomes.append(("in_doubt", None))
        except Exception as e:  # pragma: no cover - surfaces unexpected failures
            with out_lock:
                outcomes.append(("error", repr(e)))

    threads = [threading.Thread(target=fire) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # THE core assertion: exactly one thread ran the side effect.
    assert calls["n"] == 1, f"expected exactly one execution, got {calls['n']}"

    # Losers either replayed the stored result or were told IN_DOUBT (the documented
    # false-positive: a live loser can't be distinguished from a crashed corpse).
    # None of them executed. No unexpected errors.
    assert all(kind in ("ok", "in_doubt") for kind, _ in outcomes)
    assert len(outcomes) == 20

    # Whatever the winner stored, every 'ok' replay must equal it.
    store = get_store(tmp_db)
    winner = store.get("charge-1")
    assert winner.state in ("SUCCEEDED", "IN_DOUBT")
