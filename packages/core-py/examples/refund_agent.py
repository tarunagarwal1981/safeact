"""
A realistic refund agent: charge -> email -> ledger, wrapped in a Saga.

The ledger step fails (simulating a downstream outage), which triggers automatic
compensation in reverse order: the receipt correction is sent, then the charge is
reversed. Run it:

    python examples/refund_agent.py

You'll see the three steps run, step 3 fail, and the two completed steps roll back
exactly once — no double-refund.
"""

import os
import tempfile

from safeact import Saga, SagaFailed

# --- fake downstream systems, each backed by a counter so you can SEE at-most-once ---

class World:
    def __init__(self):
        self.charges = []
        self.emails = []
        self.ledger = []
        self.reversals = []
        self.corrections = []


world = World()


def charge_customer(amount):
    world.charges.append(amount)
    print(f"  [stripe]  charged ${amount}  -> charge_id ch_{len(world.charges)}")
    return {"charge_id": f"ch_{len(world.charges)}", "amount": amount}


def reverse_charge(step_result):
    world.reversals.append(step_result["charge_id"])
    print(f"  [stripe]  REVERSED {step_result['charge_id']}")
    return {"reversed": step_result["charge_id"]}


def email_customer(to):
    world.emails.append(to)
    print(f"  [email]   sent refund receipt to {to}")
    return {"to": to}


def send_correction(step_result):
    world.corrections.append(step_result["to"])
    print(f"  [email]   sent CORRECTION notice to {step_result['to']}")
    return {"corrected": step_result["to"]}


def write_ledger(entry):
    # Simulate the failure that triggers rollback.
    print(f"  [ledger]  writing {entry} ... FAILED (ledger DB unreachable)")
    raise RuntimeError("ledger DB unreachable")


def main():
    db = os.path.join(tempfile.mkdtemp(), "refund_agent.db")
    print(f"store: {db}\n")

    print("Running refund saga (charge -> email -> ledger):")
    try:
        with Saga("refund-8842", store=db) as s:
            s.step(charge_customer, 5000, compensate=reverse_charge)
            s.step(email_customer, "customer@example.com", compensate=send_correction)
            s.step(write_ledger, {"refund": "refund-8842", "amount": 5000})
    except SagaFailed as e:
        print(f"\nSaga failed as expected: {e.original!r}")
        print(f"Compensated in reverse order: {e.compensated}")

    print("\nFinal world state (each list should have exactly one entry):")
    print(f"  charges     = {world.charges}")
    print(f"  reversals   = {world.reversals}")
    print(f"  emails      = {world.emails}")
    print(f"  corrections = {world.corrections}")

    assert world.charges == [5000]
    assert world.reversals == ["ch_1"]      # charge undone exactly once
    assert world.emails == ["customer@example.com"]
    assert world.corrections == ["customer@example.com"]  # receipt corrected exactly once
    print("\nOK — the failed refund was fully unwound, each step compensated once.")


if __name__ == "__main__":
    main()
