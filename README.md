# SafeAct

**The safety checkpoint every AI action passes through — before it happens.**

🌐 **Live site:** https://tarunagarwal1981.github.io/safeact/

Your AI agent can send the email, issue the refund, update the record. SafeAct makes sure it
does that **safely, once, and on the record** — so your risk team says yes instead of no.

Every action an agent takes passes four guarantees:

| | |
|---|---|
| **✓ Checked** | Checked against your rules — in code, so the model can't prompt its way around them. |
| **✋ Held when unsure** | Low-confidence actions are held for a human to approve, not fired blindly. |
| **① Exactly once** | Crash, restart, retry — the action still happens once. If it's truly unknowable, it says so. |
| **🔒 Provable** | A tamper-evident receipt for every action: what happened, why, and who approved it. |

---

## Repository layout

This is the **public, open-source** repo. It holds only the free libraries and the
marketing site. **The paid platform lives in a separate private repo** (see below).

```
safeact/  (PUBLIC · Apache-2.0)
├── BRIEF.md              Product brief — problem, architecture, v1 scope, launch plan
├── docs/                 Marketing site (served via GitHub Pages)
└── packages/
    ├── core-py/          SafeAct Core (Python) — exactly-once · in-doubt · saga. BUILT + tested.
    ├── mcp-py/           SafeAct for MCP — make any MCP tool exactly-once + auditable. On PyPI: safeact-mcp.
    ├── langchain-py/     SafeAct for LangChain/LangGraph — exactly-once tools keyed on tool_call_id. On PyPI: safeact-langchain.
    └── core-ts/          SafeAct Core (TypeScript SDK) — thin client. Placeholder → in progress.
```

### Where the paid code goes

`SafeAct Control` — the commercial platform (policy engine, human-approval inbox,
tamper-evident audit, "prove it" regulator export, billing, Control console UI) — lives in a
**separate PRIVATE repo, `safeact-control`**. GitHub repos are all-or-nothing public/private,
so commercial IP is never committed here. **Open-core rule: this repo is free; anything
customers pay for stays private.**

The two-half architecture (free correctness core + paid governance layer) is described in
[BRIEF.md](BRIEF.md).

---

## Quickstart — SafeAct Core (Python)

```bash
pip install safeact
```

```python
from safeact import safe

@safe(store="agent.db")
def send_receipt(key, to, amount):   # key names the logical action
    email.send(to=to, subject="Your refund", body=f"Refunded ${amount}.")
    return {"sent": True}

send_receipt("refund-8842", "a@b.com", 50)   # runs once
send_receipt("refund-8842", "a@b.com", 50)   # returns stored result, no re-send
```

**Run the crash-simulation test suite from source:**

```bash
cd packages/core-py
python3.11 -m venv .venv && ./.venv/bin/pip install -e ".[test]"
./.venv/bin/pytest -q          # 15 tests: idempotency · in-doubt · concurrency · saga
```

See [packages/core-py/README.md](packages/core-py/README.md) for the full API and the four
mutation-tested guarantees, and [packages/core-py/DESIGN.md](packages/core-py/DESIGN.md) for
the state machine and rationale.

## SafeAct for MCP — `pip install safeact-mcp`

Building MCP tools? Two decorators make any tool exactly-once + auditable:

```python
from mcp.server.mcpserver import MCPServer
from safeact_mcp import safe_tool

server = MCPServer("payments")

@server.tool()
@safe_tool(store="agent.db")
def charge_card(idempotency_key: str, customer: str, amount: int) -> dict:
    stripe.charge(customer, amount)      # the real, irreversible action
    return {"charged": amount}
```

Now `charge_card` runs at most once per key — even across agent crashes and
retries — and every call is recorded to an audit trail. See
[packages/mcp-py/](packages/mcp-py/).

---

## Status

- **SafeAct Core (Python)** — built, mutation-tested, 15/15 green. **Live on PyPI:** `pip install safeact`.
- **SafeAct for MCP** — **live on PyPI:** `pip install safeact-mcp`. Wraps any MCP tool.
- **SafeAct for LangChain/LangGraph** — **live on PyPI:** `pip install safeact-langchain`. Exactly-once tools keyed on `tool_call_id`.
- **SafeAct Core (TypeScript)** — thin SDK, in progress (placeholder).
- **SafeAct Control** — designed (see BRIEF.md §5), not yet built.
- **Landing page** — live at **https://safeact.dev**
- **Domain** — safeact.dev live (GitHub Pages + HTTPS).

## Related

- [**wobbly**](https://github.com/tarunagarwal1981/wobbly) — metamorphic testing for AI outputs;
  finds wrong answers with no labels. The label-free verification signal that feeds SafeAct's
  "held when unsure" gate. A second free front door into the same trust thesis.
