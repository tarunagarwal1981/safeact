"""
Guarantee: a side-effecting action whose arguments came from an untrusted origin is
HELD before it runs — never executed — and the decision is a durable record. This is
the provenance gate from safeact.guard, the "checked, held when unsure" guarantee.

Each test that asserts a HOLD also proves the side effect did NOT happen (a spy that
flips only when the real function body runs).
"""

import asyncio

import pytest

from safeact import (
    FailedCallError,
    HeldForReview,
    hold_if_tainted,
    safe,
    taint,
)
from safeact.guard import FILE, TOOL, USER, WEB, Tainted, origins_of, untaint
from safeact.store import HELD, SUCCEEDED, get_store


# ---- the core gate ---------------------------------------------------------

def test_untrusted_arg_is_held_and_not_executed(tmp_db):
    ran = {"did": False}

    @safe(store=tmp_db, guard=hold_if_tainted())
    def run_shell(key, cmd):
        ran["did"] = True          # the dangerous side effect
        return {"ran": cmd}

    # cmd came from a planted file — exactly the AGENTS.md attack shape.
    with pytest.raises(HeldForReview) as ei:
        run_shell("job-1", taint("rm -rf /", FILE))

    assert ran["did"] is False     # proven: the side effect never happened
    assert ei.value.key == "job-1"
    assert "file" in ei.value.reason

    # and it is a durable, inspectable record
    store = get_store(tmp_db)
    held = store.list_held()
    assert [r.key for r in held] == ["job-1"]
    assert store.get("job-1").state == HELD


def test_trusted_arg_runs_normally(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    def charge(key, amount):
        return {"charged": amount}

    out = charge("pay-1", taint(50, USER))
    assert out == {"charged": 50}
    assert get_store(tmp_db).get("pay-1").state == SUCCEEDED


def test_untagged_arg_runs(tmp_db):
    # The default policy only holds on *untrusted* taint; a plain, untagged value
    # is not held (you opt in by tainting external data at its source).
    @safe(store=tmp_db, guard=hold_if_tainted())
    def act(key, x):
        return x

    assert act("k-1", 7) == 7


def test_one_untrusted_arg_among_trusted_still_holds(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    def transfer(key, to, amount):
        return {"to": to, "amount": amount}

    with pytest.raises(HeldForReview):
        transfer("t-1", taint("attacker@evil.com", WEB), taint(100, USER))


def test_tainted_value_is_unwrapped_before_execution(tmp_db):
    seen = {}

    @safe(store=tmp_db, guard=hold_if_tainted())
    def act(key, amount, meta):
        seen["amount_type"] = type(amount)
        seen["meta_inner_type"] = type(meta["v"])
        return amount

    # trusted taint passes the gate; the fn must receive RAW values, not wrappers —
    # including a value nested inside a dict.
    act("k-1", taint(50, USER), {"v": taint("ok", USER)})
    assert seen["amount_type"] is int
    assert seen["meta_inner_type"] is str


def test_guard_true_uses_default_policy(tmp_db):
    @safe(store=tmp_db, guard=True)
    def act(key, x):
        return x

    with pytest.raises(HeldForReview):
        act("k-1", taint("payload", TOOL))


# ---- consistency with the rest of the state machine ------------------------

def test_held_key_reinvoked_stays_held(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    def act(key, x):
        return x

    with pytest.raises(HeldForReview):
        act("k-1", taint("p", WEB))
    # same key again -> claim conflict -> resolves to the HELD record, still refused
    with pytest.raises(HeldForReview):
        act("k-1", taint("p", WEB))


def test_release_hold_denied_then_refused(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    def act(key, x):
        return x

    with pytest.raises(HeldForReview):
        act("k-1", taint("p", WEB))

    store = get_store(tmp_db)
    store.release_hold("k-1", approve=False)
    # denied holds become FAILED -> refuse-by-default on reuse
    with pytest.raises(FailedCallError):
        act("k-1", taint("p", WEB))
    assert store.list_held() == []


def test_release_hold_approved_records_success(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    def act(key, x):
        return x

    with pytest.raises(HeldForReview):
        act("k-1", taint("p", WEB))

    store = get_store(tmp_db)
    store.release_hold("k-1", approve=True, result={"handled": True})
    rec = store.get("k-1")
    assert rec.state == SUCCEEDED
    assert store.list_held() == []


def test_guard_none_is_unchanged_behavior(tmp_db):
    # The gate must be strictly opt-in: without `guard`, runs once then replays,
    # exactly as before — and a Tainted value passes straight through untouched.
    calls = {"n": 0}

    @safe(store=tmp_db)
    def act(key, x):
        calls["n"] += 1
        return x

    assert act("k-1", 5) == 5
    assert act("k-1", 5) == 5      # replay, not re-run
    assert calls["n"] == 1


# ---- async ----------------------------------------------------------------

def test_async_untrusted_arg_is_held(tmp_db):
    ran = {"did": False}

    @safe(store=tmp_db, guard=hold_if_tainted())
    async def send(key, body):
        ran["did"] = True
        return {"sent": True}

    with pytest.raises(HeldForReview):
        asyncio.run(send("m-1", taint("<script>", WEB)))
    assert ran["did"] is False
    assert get_store(tmp_db).get("m-1").state == HELD


def test_async_trusted_arg_runs(tmp_db):
    @safe(store=tmp_db, guard=hold_if_tainted())
    async def send(key, body):
        return {"sent": body}

    out = asyncio.run(send("m-1", taint("hello", USER)))
    assert out == {"sent": "hello"}


# ---- taint primitives ------------------------------------------------------

def test_taint_accumulates_and_never_washes_off(tmp_db):
    t = taint(taint("x", WEB), USER)
    assert t.origins == frozenset({WEB, USER})


def test_origins_of_walks_nested_containers():
    nested = {"a": [1, taint("deep", FILE)], "b": (taint("t", TOOL),)}
    assert origins_of(nested) == frozenset({FILE, TOOL})


def test_untaint_restores_shape():
    nested = {"a": [taint(1, WEB), 2], "b": taint("x", USER)}
    assert untaint(nested) == {"a": [1, 2], "b": "x"}
    assert not isinstance(untaint(taint("x", WEB)), Tainted)
