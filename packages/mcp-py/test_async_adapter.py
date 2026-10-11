"""An async MCP tool gets exactly-once through the real call_tool path."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.mcpserver import MCPServer
from safeact_mcp import safe_tool, audit_record

DB = os.path.join(tempfile.mkdtemp(), "mcp_async.db")
charges = []

server = MCPServer("payments-async")


@server.tool()
@safe_tool(store=DB)
async def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    """An ASYNC tool — awaited, exactly-once."""
    await asyncio.sleep(0)  # a real await point (e.g. an async HTTP call)
    charges.append((customer, amount))
    return {"charged": amount, "customer": customer, "charge_no": len(charges)}


async def main():
    args = {"idempotency_key": "order-async-1", "customer": "alice", "amount": 5000}
    r1 = await server.call_tool("charge_card", dict(args))
    r2 = await server.call_tool("charge_card", dict(args))  # crash + retry, same key

    assert len(charges) == 1, f"expected exactly 1 charge, got {len(charges)}"
    rec = audit_record(DB, "order-async-1")
    assert rec is not None and rec.state == "SUCCEEDED", rec

    print("async tool via MCP call_tool:", charges, "->", len(charges), "charge, not 2  ✓")
    print("audit:", rec.state, rec.key)


if __name__ == "__main__":
    asyncio.run(main())
    print("OK")
