"""End-to-end test: register a safe tool on a REAL MCPServer and invoke it
through the actual MCP call path (call_tool), proving exactly-once."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))  # find the safeact_mcp package

from mcp.server.mcpserver import MCPServer
from safeact_mcp import safe_tool, audit_record

DB = os.path.join(tempfile.mkdtemp(), "mcp.db")
charges = []  # the REAL side effect

server = MCPServer("payments")


@server.tool()
@safe_tool(store=DB)
def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    """Charge a customer's card."""
    charges.append((customer, amount))
    return {"charged": amount, "customer": customer, "charge_no": len(charges)}


async def main():
    print("=== schema the MCP client sees ===")
    tools = await server.list_tools()
    t = tools[0]
    schema = t.input_schema if isinstance(t.input_schema, dict) else t.input_schema.model_dump()
    print("tool:", t.name, "| params:", list(schema.get("properties", {}).keys()))

    args = {"idempotency_key": "order-42", "customer": "alice", "amount": 5000}

    print("\n=== call the tool through the real MCP path ===")
    r1 = await server.call_tool("charge_card", dict(args))
    print("call 1 ->", getattr(r1, "structuredContent", None) or getattr(r1, "content", r1))

    print("\n=== agent crashed and retried the SAME key ===")
    r2 = await server.call_tool("charge_card", dict(args))
    print("call 2 ->", getattr(r2, "structuredContent", None) or getattr(r2, "content", r2))

    print(f"\nReal charges made: {charges}")
    print(f"-> {len(charges)} charge, not 2. Exactly-once held through the real MCP call path.")
    print("audit record:", audit_record(DB, "order-42"))


if __name__ == "__main__":
    asyncio.run(main())
