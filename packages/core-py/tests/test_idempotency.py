"""
Guarantee: a logical call runs AT MOST ONCE per key — across retries and across
process restarts. Proven with a counter-backed tool, never a mock.

Remove record-before-execute or on-disk durability and these tests fail.
"""

from safeact import safe
from safeact.store import _STORES, get_store


def test_forty_retries_execute_exactly_once(tmp_db):
    calls = {"n": 0}

    @safe(store=tmp_db)
    def send_email(key, to):
        calls["n"] += 1
        return {"to": to, "execution": calls["n"]}

    results = [send_email("welcome-42", "a@b.com") for _ in range(40)]

    assert calls["n"] == 1, "the side effect must fire exactly once for one key"
    # every retry replayed the identical stored result
    assert all(r == {"to": "a@b.com", "execution": 1} for r in results)


def test_record_before_execute_ordering(tmp_db):
    """The ATTEMPTING row must be committed to disk BEFORE the side effect runs.
    If you reordered to execute-first, `store.get(key)` inside the body would be
    None and this test would fail."""
    store = get_store(tmp_db)
    seen = {}

    @safe(store=tmp_db)
    def act(key):
        rec = store.get(key)  # what does the DB know at the instant we execute?
        seen["state"] = rec.state if rec else None
        return "ok"

    act("k1")
    assert seen["state"] == "ATTEMPTING"


def test_idempotency_survives_restart(tmp_db):
    """Simulate a new process: drop the in-memory Store cache and rebuild the
    decorated tool against the same file. The stored result must come from disk,
    so the side effect is NOT repeated."""
    calls = {"n": 0}

    def build_tool():
        @safe(store=tmp_db)
        def charge(key, amount):
            calls["n"] += 1
            return {"amount": amount}

        return charge

    charge_v1 = build_tool()
    first = charge_v1("pay-1", 100)
    assert calls["n"] == 1

    # --- "process dies and restarts" ---
    _STORES.clear()  # nothing survives in memory
    charge_v2 = build_tool()  # fresh Store, same SQLite file

    second = charge_v2("pay-1", 100)  # same key, new process
    assert calls["n"] == 1, "restart must not re-execute a key already SUCCEEDED on disk"
    assert second == first == {"amount": 100}


def test_distinct_keys_run_independently(tmp_db):
    """Sanity: idempotency is per-key, not global. Different keys each run once —
    this is why keys must be explicit and never derived from args."""
    calls = {"n": 0}

    @safe(store=tmp_db)
    def refund(key, amount):
        calls["n"] += 1
        return amount

    refund("refund-1", 50)
    refund("refund-2", 50)  # identical amount, DIFFERENT logical action
    assert calls["n"] == 2
