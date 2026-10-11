"""Exactly-once for a LangChain tool, keyed on the injected tool_call_id —
sync and async, invoked the way a LangGraph ToolNode does."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from safeact_langchain import safe_tool, audit_record

STORE = os.path.join(tempfile.mkdtemp(), "lc.db")
charges = []


@safe_tool(store=STORE)
def charge_card(customer: str, amount: int) -> dict:
    """Charge a customer's card."""
    charges.append((customer, amount))
    return {"charged": amount, "charge_no": len(charges)}


acalls = {"n": 0}


@safe_tool(store=STORE)
async def notify(customer: str) -> dict:
    """Async tool."""
    await asyncio.sleep(0)
    acalls["n"] += 1
    return {"notified": customer, "n": acalls["n"]}


def _call(tool, args, call_id):
    return tool.invoke({"name": tool.name, "args": args, "id": call_id, "type": "tool_call"})


def main():
    # schema must hide the injected id
    assert list(charge_card.args.keys()) == ["customer", "amount"], charge_card.args
    print("schema (id hidden):", list(charge_card.args.keys()))

    # sync: same tool_call_id twice (crash + resume) -> one charge
    _call(charge_card, {"customer": "alice", "amount": 50}, "call-1")
    _call(charge_card, {"customer": "alice", "amount": 50}, "call-1")
    assert len(charges) == 1, charges
    print("sync charges:", charges, "->", len(charges), "charge, not 2  ✓")

    # async: same id twice -> one execution
    asyncio.run(_acall())
    assert acalls["n"] == 1, acalls
    print("async notify executions:", acalls["n"], "-> 1, not 2  ✓")

    rec = audit_record(STORE, "call-1")
    assert rec is not None and rec.state == "SUCCEEDED", rec
    print("audit:", rec.state, rec.key)


async def _acall():
    await notify.ainvoke({"name": "notify", "args": {"customer": "bob"}, "id": "call-2", "type": "tool_call"})
    await notify.ainvoke({"name": "notify", "args": {"customer": "bob"}, "id": "call-2", "type": "tool_call"})


if __name__ == "__main__":
    main()
    print("OK")
