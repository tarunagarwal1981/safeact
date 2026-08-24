"""
A complete MCP server whose tools are exactly-once + auditable via safeact-mcp.

Run the built-in self-check (no MCP client needed):
    python examples/payments_server.py

Or serve it over stdio to a real MCP client:
    server.run()   # (uncomment in __main__)
"""

import asyncio
import os
import tempfile

from mcp.server.mcpserver import MCPServer
from safeact_mcp import safe_tool, audit_record

server = MCPServer("payments")

STORE = os.path.join(tempfile.mkdtemp(), "payments.db")  # fresh each run

# a stand-in for the real world (money actually moves)
_charges = []


@server.tool()
@safe_tool(store=STORE)
def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    """Charge a customer's card. Exactly-once per idempotency_key."""
    _charges.append((customer, amount))
    return {"charged": amount, "customer": customer, "charge_no": len(_charges)}


async def _self_check():
    args = {"idempotency_key": "order-42", "customer": "alice", "amount": 5000}
    await server.call_tool("charge_card", dict(args))     # first call
    await server.call_tool("charge_card", dict(args))     # crash + retry, same key
    print("charges actually made:", _charges)
    print("->", len(_charges), "charge, not 2 (retry replayed the stored result)")
    print("audit record:", audit_record(STORE, "order-42"))


if __name__ == "__main__":
    asyncio.run(_self_check())
    # To run as a real MCP server over stdio instead, use:
    #   server.run()
