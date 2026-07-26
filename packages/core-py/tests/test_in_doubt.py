"""
Guarantee: a crash in the gap (after the side effect ran, before success was
recorded) lands the key in IN_DOUBT. The next call raises InDoubtError instead of
blindly retrying, the counter stays at 1, and the in-doubt record is ACTIONABLE —
it carries a receipt and can be resolved by a human.

This is the honest limitation, tested rather than hand-waved.
"""

import pytest

from safeact import FailedCallError, InDoubtError, safe
from safeact.store import get_store


class SimulatedCrash(Exception):
    """Stands in for the process dying at an instrumented point."""


def _tool_that_crashes_in_gap(tmp_db, calls):
    @safe(store=tmp_db)
    def send_email(key, to):
        calls["n"] += 1
        return {"to": to}

    store = get_store(tmp_db)

    def die_in_gap(key):
        raise SimulatedCrash("process died after send, before record")

    store._gap_hook = die_in_gap
    return send_email, store


def test_crash_in_gap_lands_in_doubt_and_next_call_raises(tmp_db):
    calls = {"n": 0}
    send_email, store = _tool_that_crashes_in_gap(tmp_db, calls)

    # First call: side effect fires, then the process "dies" in the gap.
    with pytest.raises(SimulatedCrash):
        send_email("welcome-42", "a@b.com")
    assert calls["n"] == 1, "the email really did go out once"

    # Process restarts; the gap hook is gone. Retry the SAME key.
    store._gap_hook = lambda key: None
    with pytest.raises(InDoubtError) as excinfo:
        send_email("welcome-42", "a@b.com")

    assert calls["n"] == 1, "the retry must NOT re-run the side effect"

    # The error is a receipt, not a tombstone.
    err = excinfo.value
    assert err.key == "welcome-42"
    assert err.tool == "send_email"
    assert err.args_hash
    assert err.attempted_at

    # And it is surfaced for reconciliation.
    doubts = store.list_in_doubt()
    assert [d.key for d in doubts] == ["welcome-42"]


def test_in_doubt_is_resolvable(tmp_db):
    """After checking the downstream, a human closes out the in-doubt key. Once
    resolved as 'happened', a future same-key call replays the resolved result and
    still does not re-execute."""
    calls = {"n": 0}
    send_email, store = _tool_that_crashes_in_gap(tmp_db, calls)

    with pytest.raises(SimulatedCrash):
        send_email("welcome-42", "a@b.com")
    store._gap_hook = lambda key: None
    with pytest.raises(InDoubtError):
        send_email("welcome-42", "a@b.com")  # flip ATTEMPTING -> IN_DOUBT

    # Human checked Stripe/ESP: yes, it happened. Close it out.
    store.resolve("welcome-42", happened=True, result={"to": "a@b.com", "reconciled": True})

    replayed = send_email("welcome-42", "a@b.com")
    assert calls["n"] == 1, "resolving must not cause a re-run"
    assert replayed == {"to": "a@b.com", "reconciled": True}
    assert store.list_in_doubt() == []


def test_resolve_as_not_happened_marks_failed(tmp_db):
    calls = {"n": 0}
    send_email, store = _tool_that_crashes_in_gap(tmp_db, calls)
    with pytest.raises(SimulatedCrash):
        send_email("welcome-42", "a@b.com")
    store._gap_hook = lambda key: None
    with pytest.raises(InDoubtError):
        send_email("welcome-42", "a@b.com")

    store.resolve("welcome-42", happened=False)

    # A FAILED key refuses re-run (refuse-by-default).
    with pytest.raises(FailedCallError):
        send_email("welcome-42", "a@b.com")
    assert calls["n"] == 1
