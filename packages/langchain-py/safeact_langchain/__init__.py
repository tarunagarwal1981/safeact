"""
safeact-langchain — make a LangChain/LangGraph tool exactly-once and auditable.

    from safeact_langchain import safe_tool

    @safe_tool(store="agent.db")
    def charge_card(customer: str, amount: int) -> dict:
        stripe.charge(customer, amount)
        return {"charged": amount}

The tool dedupes on LangChain's injected tool_call_id (hidden from the LLM). Under
LangGraph, tool_call_ids are persisted in the checkpoint, so a crash-and-resume
re-invokes with the same id and the side effect does not repeat.
"""

from .adapter import safe_tool, audit_record, in_doubt
from safeact import InDoubtError

__all__ = ["safe_tool", "audit_record", "in_doubt", "InDoubtError"]
__version__ = "0.1.0"
