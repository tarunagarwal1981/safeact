# safeact-mcp

**Make any MCP tool exactly-once and auditable — one decorator.**

Your MCP tools *do real things*: charge a card, send an email, book a slot. When
the agent crashes and retries — and agents crash and retry a lot — a naive tool
fires the action **twice**. And there's no record of what actually happened, so
you can't prove it or check it.

`safeact-mcp` fixes both, in one line on top of the MCP SDK.

```bash
pip install safeact-mcp
```

## Quickstart

```python
from mcp.server.mcpserver import MCPServer
from safeact_mcp import safe_tool

server = MCPServer("payments")

@server.tool()
@safe_tool(store="agent.db")
def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    stripe.charge(customer, amount)          # the real, irreversible action
    return {"charged": amount}
```

That's it. Now `charge_card`:

- **Runs at most once per idempotency key** — even across crashes and retries.
- **Records every attempt** to a durable store — that's your audit trail.
- **Refuses to guess.** If the process dies *after* the charge but *before* it's
  recorded, the key is marked `IN_DOUBT` and the next call raises `InDoubtError`
  instead of blindly charging again. (An honest "we don't know, go check" beats a
  silent double-charge.)

### Async tools

`safe_tool` auto-detects `async def` tools and awaits them — same exactly-once
guarantee (MCP servers commonly use async tools):

```python
@server.tool()
@safe_tool(store="agent.db")
async def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    await stripe.charge(customer, amount)
    return {"charged": amount}
```

## Reading the audit trail

```python
from safeact_mcp import audit_record, in_doubt

audit_record("agent.db", "order-42")   # the durable record for one call
in_doubt("agent.db")                   # keys stuck in the gap — your review queue
```

## The one honest limitation

MCP has no crash-safe idempotency key of its own. So the **caller supplies one**
via the `idempotency_key` argument — pass a *stable* value (e.g. your agent's
tool-call id) so retries deduplicate. No key → you still get the audit trail, but
not the exactly-once guarantee. We're upfront about that.

## How it works

`safeact-mcp` is a thin adapter over [**safeact**](https://github.com/tarunagarwal1981/safeact),
the correctness core (exactly-once · honest in-doubt · saga compensation),
mutation-tested. `safe_tool` composes *under* `MCPServer.tool()` and preserves
your tool's signature, so the MCP server still generates the correct input schema.

See [`examples/payments_server.py`](examples/payments_server.py) for a complete,
runnable server.

## License

Apache-2.0. Part of [SafeAct](https://safeact.dev) — the trust layer for AI agents.
