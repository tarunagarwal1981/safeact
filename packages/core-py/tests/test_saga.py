"""
Guarantees for Saga:
  - a failing step compensates completed steps in REVERSE order, once each;
  - rollback is idempotent — replaying the whole saga after a crash does not
    double-undo (nor double-do);
  - a crash DURING rollback resumes safely: each compensator still ran exactly once,
    and a compensator that died in its own gap halts rollback as IN_DOUBT rather than
    risking a double-undo.
"""

from collections import defaultdict

import pytest

from safeact import Saga, SagaFailed
from safeact.store import get_store


class SimulatedCrash(Exception):
    """Stands in for the process dying at an instrumented point."""


def _refund_tools(events, counters):
    """A realistic charge -> email -> ledger flow where the ledger step fails."""

    def charge(amount):
        counters["charge"] += 1
        events.append("charge")
        return {"charge_id": "ch_1", "amount": amount}

    def reverse_charge(step_result):
        counters["reverse_charge"] += 1
        events.append("reverse_charge")
        return {"reversed": step_result["charge_id"]}

    def send_receipt(to):
        counters["send_receipt"] += 1
        events.append("send_receipt")
        return {"to": to}

    def send_correction(step_result):
        counters["send_correction"] += 1
        events.append("send_correction")
        return {"corrected": step_result["to"]}

    def write_ledger(entry):
        counters["write_ledger"] += 1
        events.append("write_ledger")
        raise RuntimeError("ledger DB unreachable")

    return charge, reverse_charge, send_receipt, send_correction, write_ledger


def test_failing_step_compensates_in_reverse_once_each(tmp_db):
    events, counters = [], defaultdict(int)
    charge, reverse_charge, send_receipt, send_correction, write_ledger = _refund_tools(events, counters)

    with pytest.raises(SagaFailed) as excinfo:
        with Saga("refund-8842", store=tmp_db) as s:
            s.step(charge, 5000, compensate=reverse_charge)
            s.step(send_receipt, "a@b.com", compensate=send_correction)
            s.step(write_ledger, {"amount": 5000})

    # Forward to the failure, then compensators in reverse order.
    assert events == [
        "charge",
        "send_receipt",
        "write_ledger",
        "send_correction",  # step 2 undone first
        "reverse_charge",   # then step 1
    ]
    assert counters["send_correction"] == 1
    assert counters["reverse_charge"] == 1

    sf = excinfo.value
    assert sf.compensated == ["send_receipt", "charge"]  # reverse-order step names
    assert sf.in_doubt == []
    assert isinstance(sf.original, RuntimeError)


def test_replaying_a_failed_saga_does_not_double_undo(tmp_db):
    """Compensation idempotency: run the same saga block twice. Deterministic keys
    mean the second run replays everything as no-ops — no step re-runs, no
    compensator fires a second time."""
    events, counters = [], defaultdict(int)
    charge, reverse_charge, send_receipt, send_correction, write_ledger = _refund_tools(events, counters)

    def run_once():
        with Saga("refund-8842", store=tmp_db) as s:
            s.step(charge, 5000, compensate=reverse_charge)
            s.step(send_receipt, "a@b.com", compensate=send_correction)
            s.step(write_ledger, {"amount": 5000})

    with pytest.raises(SagaFailed):
        run_once()
    with pytest.raises(SagaFailed):
        run_once()  # "resume" after a crash: re-run the identical block

    # Every side effect fired exactly once across BOTH runs.
    assert counters["charge"] == 1
    assert counters["send_receipt"] == 1
    assert counters["write_ledger"] == 1
    assert counters["send_correction"] == 1
    assert counters["reverse_charge"] == 1


def test_crash_during_rollback_resumes_without_double_undo(tmp_db):
    """The nasty case: crash mid-rollback, then resume. The compensator that died in
    its own gap must NOT run again (no double-undo); it lands IN_DOUBT and halts the
    rollback, which is surfaced honestly."""
    counters = defaultdict(int)

    def charge(amount):
        counters["charge"] += 1
        return {"charge_id": "ch_1"}

    def reverse_charge(r):
        counters["reverse_charge"] += 1
        return {"ok": True}

    def send_receipt(to):
        counters["send_receipt"] += 1
        return {"to": to}

    def send_correction(r):
        counters["send_correction"] += 1
        return {"ok": True}

    def write_ledger(entry):
        counters["write_ledger"] += 1
        raise RuntimeError("ledger unreachable")

    store = get_store(tmp_db)
    comp_step2_key = "comp:crash-saga:1:send_receipt"

    def die_compensating_step2(key):
        if key == comp_step2_key:
            raise SimulatedCrash("died compensating step 2")

    store._gap_hook = die_compensating_step2

    def run_saga():
        with Saga("crash-saga", store=tmp_db) as s:
            s.step(charge, 1, compensate=reverse_charge)
            s.step(send_receipt, "a@b.com", compensate=send_correction)
            s.step(write_ledger, {})

    # First run: steps 1,2 ok; step 3 fails; rollback starts; send_correction runs its
    # side effect then the process dies in the gap.
    with pytest.raises(SimulatedCrash):
        run_saga()
    assert counters["send_correction"] == 1  # compensator side effect ran once
    assert counters["reverse_charge"] == 0   # never reached step 1's compensator

    # Resume: the process restarts (gap hook gone) and re-runs the same saga.
    store._gap_hook = lambda key: None
    with pytest.raises(SagaFailed) as excinfo:
        run_saga()

    sf = excinfo.value
    # comp:...:send_receipt was left ATTEMPTING -> IN_DOUBT; rollback halts there.
    assert sf.in_doubt == [comp_step2_key]
    assert sf.compensated == []  # halted before compensating anything on resume

    # THE point: no double-undo, and nothing re-executed.
    assert counters["send_correction"] == 1
    assert counters["reverse_charge"] == 0
    assert counters["charge"] == 1
    assert counters["send_receipt"] == 1
    assert counters["write_ledger"] == 1


def test_successful_saga_runs_no_compensation(tmp_db):
    counters = defaultdict(int)

    def step_a(x):
        counters["a"] += 1
        return {"x": x}

    def undo_a(r):
        counters["undo_a"] += 1

    with Saga("ok-saga", store=tmp_db) as s:
        r1 = s.step(step_a, 1, compensate=undo_a)
        r2 = s.step(step_a, 2)

    assert r1 == {"x": 1}
    assert r2 == {"x": 2}
    assert counters["a"] == 2
    assert counters["undo_a"] == 0
