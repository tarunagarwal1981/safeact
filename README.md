# SafeAct

**The safety checkpoint every AI action passes through — before it happens.**

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

This is a monorepo. It grows package-by-package as the product does.

```
safeact/
├── BRIEF.md              Product brief — problem, architecture, v1 scope, launch plan
├── packages/
│   └── core-py/          SafeAct Core (Python) — exactly-once · in-doubt · saga rollback
│                         The free, open-source correctness engine. BUILT + tested.
└── (planned)
    ├── packages/core-ts/         SafeAct Core (TypeScript) — thin SDK
    ├── services/control/         SafeAct Control — policy engine, approval inbox, audit, export
    └── apps/web/                 Marketing site + Control console
```

The two-half architecture (free correctness core + paid governance layer) is described in
[BRIEF.md](BRIEF.md).

---

## Quickstart — SafeAct Core (Python)

```bash
cd packages/core-py
python3.11 -m venv .venv && ./.venv/bin/pip install -e ".[test]"
./.venv/bin/pytest -q          # the crash-simulation suite (15 tests)
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

See [packages/core-py/README.md](packages/core-py/README.md) for the full API and the four
mutation-tested guarantees, and [packages/core-py/DESIGN.md](packages/core-py/DESIGN.md) for
the state machine and rationale.

---

## Status

- **SafeAct Core (Python)** — built, mutation-tested, 15/15 green.
- **SafeAct Control** — designed (see BRIEF.md §5), not yet built.
- **Name / domain** — safeact.ai; package `safeact` on PyPI + npm.
