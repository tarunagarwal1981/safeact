# GUARD — provenance gate for safeact

The exactly-once core answers *"did this action happen once, on the record?"*
Guard answers the question the 2026 agent breaches turned on: *"should this
action have been allowed to happen **at all**, given where its arguments came
from?"*

This document states the threat, the design, the guarantee, and — honestly —
the edges of the v0.

---

## 1. The problem, precisely

Through October 2026 the pattern repeated: an agent reads **untrusted content**
— a planted `AGENTS.md` in a downloads folder, a web page, a form field,
another tool's output — and that content becomes **instructions** that reach a
real, irreversible call (`run_shell`, `charge_card`, `send_email`). Nothing
between the untrusted input and the side effect checks *where the data came
from*.

- The malicious-`AGENTS.md` campaign hijacked coding agents into installing
  malware and stealing credentials.
- Salesforce Agentforce was driven into zero-click exfiltration via a web form.

Prompt-injection **classifiers** try to guess from wording; the best open ones
catch roughly half. That is a filter, not a gate. **Provenance** does not guess:
it tracks where each value came from and decides on that.

We want: **a side-effecting call whose arguments carry an untrusted origin is
stopped before it runs, and the block is a durable, reviewable record.**

---

## 2. The three parts

1. **`taint(value, origin)`** — tag a value with its origin at the point you
   ingest it: `taint(page_text, WEB)`, `taint(user.amount, USER)`. Taint only
   accumulates; re-tagging never washes it off. It rides through nested
   dict / list / tuple / set.

2. **A policy** — a callable `(key, fn_name, args, kwargs) -> Decision`. The
   default, `hold_if_tainted()`, holds any call that received an argument from
   an untrusted origin (`web`, `file`, `tool`, `env`, `network`, `unknown`).
   Trusted (`user`, `system`) and untagged values pass.

3. **The gate** — `@safe(..., guard=policy)`. The policy runs **after** the
   idempotency key is claimed and **before** the side effect executes. Held →
   the action never runs, the row becomes `HELD`, and `HeldForReview` is raised.
   Allowed → taint is stripped (`untaint`) so the function receives raw values.

```python
from safeact import safe, taint
from safeact.guard import WEB, hold_if_tainted

@safe(store="agent.db", guard=hold_if_tainted())
def run_shell(key, cmd):
    subprocess.run(cmd, shell=True)

run_shell("job-1", taint(page_text, WEB))   # HeldForReview — never runs
```

---

## 3. Where it sits in the state machine

Guard rides the existing ledger — it adds one state, no new storage:

```
CLAIM(key, ATTEMPTING)        <-- the exactly-once "about to do it" marker
   │
   ├── guard(args) == HOLD ──> COMMIT(key, HELD, reason)   raise HeldForReview
   │                           (side effect NEVER runs)
   │
   └── allow ──> untaint(args) ──> execute ──> COMMIT(key, SUCCEEDED, result)
```

Because the gate sits *after* `CLAIM`, it reuses the concurrency guard and the
durable row for free: a held action owns exactly one row, and a re-invocation of
the same key hits the claim conflict and resolves to the standing `HELD` record
(still refused) — identical in shape to how `IN_DOUBT` and `FAILED` behave.

`guard=None` is the default and changes nothing: the path is byte-for-byte the
unguarded library.

---

## 4. The guarantee

For a call made through `@safe(..., guard=policy)`:

- If any argument carries an untrusted origin (per the policy), the side effect
  **does not execute**, and a durable `HELD` record is written with the reason.
- The decision is **tamper-evident and reviewable**: `store.list_held()` is the
  queue; `store.release_hold(key, approve=...)` closes one out.
- An allowed call runs **exactly once** with the raw values — every existing
  safeact guarantee still holds on top.

---

## 5. Review & resolution

`release_hold` mirrors `resolve` for in-doubt: it records a human's decision, it
does **not** re-execute (the side effect never ran).

- `release_hold(key, approve=False)` → `FAILED` ("denied by review"); reuse of
  the key is refused.
- `release_hold(key, approve=True, result=...)` → `SUCCEEDED`; you assert the
  action was allowed / handled out of band.

---

## 6. Honest edges (v0) and roadmap

- **You tag at the source.** Taint is explicit: you call `taint()` where
  untrusted data enters. Automatic tagging at the **MCP / LangChain tool
  boundary** (so tool outputs and fetched content arrive pre-tainted) is the
  next step — it makes the gate zero-effort for adapter users.
- **Block + record, not approve-and-run.** v0 stops the action and records it.
  A live **human-approval inbox** (approve → the action then executes) is the
  "Control" layer, not this.
- **Policy is origin-based, not value-based.** A value-level exfiltration guard
  (block a `secret`-tainted value flowing to an untrusted destination — the
  Railway AI-key lesson) is a natural second policy on the same primitives.
- **Taint propagation is through containers, not through arbitrary string ops.**
  `taint(a, WEB) + "x"` does not stay tainted. Carrying taint across string
  operations is a known hard problem; v0 deliberately keeps it explicit and
  predictable rather than partially-correct.
