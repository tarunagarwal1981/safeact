# safeact-langchain

**Make a LangChain/LangGraph tool exactly-once and auditable — one decorator.**

Your agent's tools do real things: charge a card, send an email, book a slot.
When the graph crashes and resumes — or the agent retries — a naive tool fires the
action **twice**. `safeact-langchain` stops that, keyed on LangChain's own
`tool_call_id`.

```bash
pip install safeact-langchain
```

## Quickstart

```python
from safeact_langchain import safe_tool

@safe_tool(store="agent.db")
def charge_card(customer: str, amount: int) -> dict:
    stripe.charge(customer, amount)          # the real, irreversible action
    return {"charged": amount}
```

`charge_card` is now a LangChain `StructuredTool` — bind it to your agent or
LangGraph `ToolNode` exactly as usual. It runs **at most once per tool-call** and
records every call to a durable audit store.

## Why it "just works" under LangGraph

The idempotency key is LangChain's **injected `tool_call_id`** — captured via
`InjectedToolCallId`, so it's **hidden from the LLM schema** (the model never sees
or sets it). And LangGraph **persists tool_call_ids in its checkpoint**, so when a
run crashes and resumes, the tool is re-invoked with the **same** id — and the side
effect does not repeat. Crash-safety for free, no key to manage.

- **Async tools** are supported (`async def` → awaited, same guarantee).
- If the process dies *after* the action but *before* it's recorded, the id is
  marked `IN_DOUBT` and the next call raises `InDoubtError` instead of blindly
  re-running — an honest "we don't know, go check" beats a silent double-charge.

## Reading the audit trail

```python
from safeact_langchain import audit_record, in_doubt

audit_record("agent.db", "<tool_call_id>")   # the durable record for one call
in_doubt("agent.db")                          # ids stuck in the gap — your review queue
```

## How it works

A thin adapter over [**safeact**](https://github.com/tarunagarwal1981/safeact)
(exactly-once · honest in-doubt · saga), wrapping your function as a LangChain tool
whose key is the injected tool-call id.

## License

Apache-2.0. Part of [SafeAct](https://safeact.dev) — the trust layer for AI agents.
