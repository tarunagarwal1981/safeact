"""
SafeAct for MCP — make any MCP tool exactly-once and auditable.

    from mcp.server.mcpserver import MCPServer
    from safeact_mcp import safe_tool

    server = MCPServer("payments")

    @server.tool()
    @safe_tool(store="agent.db")
    def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
        stripe.charge(customer, amount)          # the real, irreversible action
        return {"charged": amount}

The tool now runs at most once per idempotency key (even across crashes and
retries), and every call is recorded to a durable audit store. Honest limit:
MCP has no crash-safe idempotency key of its own, so the CALLER supplies one via
the `idempotency_key` argument — pass a stable value (e.g. your agent's
tool-call id) so retries deduplicate.
"""

from .adapter import safe_tool, audit_record, in_doubt, held
from safeact import InDoubtError, HeldForReview, taint

__all__ = ["safe_tool", "audit_record", "in_doubt", "held", "InDoubtError", "HeldForReview", "taint"]
__version__ = "0.1.0"
