# safeact — SafeAct Core

**An agent sent the same email 40 times because it crashed and retried. Here's the 10-line fix.**

When an AI agent takes a real action — send an email, charge a card, write a row —
and the process dies mid-run, the orchestration retries it. The action runs *again*.
Developers report agents making dozens of duplicate API calls after a single
crash-and-retry. Durable-execution engines (Temporal, DBOS) persist an agent's
**state**; they don't make its **actions** safe. That gap is what this closes.

`safeact` is one decorator, one context manager, a SQLite file. No server.

---

## The fix

Before — a crash after `email.send()` re-sends on retry:

```python
def send_receipt(to, amount):
    email.send(to=to, subject="Your refund", body=f"Refunded ${amount}.")
    return {"sent": True}
```

After — at most once, forever, across restarts:

```python
from safeact import safe

@safe(store="agent.db")
def send_receipt(key, to, amount):     # key is the required first argument
    email.send(to=to, subject="Your refund", body=f"Refunded ${amount}.")
    return {"sent": True}

send_receipt("refund-8842", "a@b.com", 50)   # runs once
send_receipt("refund-8842", "a@b.com", 50)   # returns the stored result, no re-send
```

The `key` names the *logical* action. Same key seen again → the stored result is
returned instead of re-running. This holds **across process restarts**, because the
record lives in SQLite, not memory.

---

## Async tools

`@safe` auto-detects `async def` and awaits the side effect — same exactly-once
guarantee (agent and MCP tools are often async):

```python
@safe(store="agent.db")
async def charge(key, customer, amount):
    await stripe.charge(customer, amount)
    return {"ok": True}

await charge("pay-1", "alice", 50)   # runs once, awaited
```

## Guard: block actions driven by untrusted data (`safeact.guard`)

Exactly-once answers *"did it happen once?"*. Guard answers *"should it have happened
at all?"* — the question the 2026 agent breaches turned on, where untrusted content (a
planted `AGENTS.md`, a web page, a tool's output) became instructions that reached a
real action. Tag where a value came from, and a side-effecting call that receives an
**untrusted** argument is HELD before it runs — never executed, recorded durably:

```python
from safeact import safe, taint
from safeact.guard import WEB, hold_if_tainted

@safe(store="agent.db", guard=hold_if_tainted())
def run_shell(key, cmd):
    subprocess.run(cmd, shell=True)

run_shell("job-1", taint(page_text, WEB))   # HeldForReview — never runs
run_shell("job-2", "ls")                     # untagged/trusted — runs

store.list_held()                 # the review queue
store.release_hold("job-1", approve=False)   # resolve after a human looks
```

Provenance, not pattern-matching: it gates on *origin*, so it catches what
prompt-injection classifiers miss. See [GUARD.md](GUARD.md) for the threat model and
the honest v0 edges.

## Why not just Temporal / DBOS?

They persist and replay your agent's **state** — where it was in the workflow.
Priceless for resuming. But on replay they happily re-run the code that talks to the
outside world. `safeact` guards the **action itself** — it's the last inch before
`email.send()` or `stripe.charge()`, a layer you drop *underneath* any orchestrator.

---

## The four guarantees (each has a crash-test that fails if you remove it)

| Guarantee | What it means | Test |
|---|---|---|
| **Idempotency** | N retries of one key → exactly one execution, across restarts | `tests/test_idempotency.py` |
| **Honest in-doubt** | Crash in the gap → key is `IN_DOUBT`, next call raises `InDoubtError` — never a blind retry | `tests/test_in_doubt.py` |
| **Concurrency** | Same key from many threads → one execution (DB `UNIQUE`, not a lock) | `tests/test_concurrency.py` |
| **Compensation** | A failing `Saga` undoes completed steps in reverse, once each — even across a crash mid-rollback | `tests/test_saga.py` |

These aren't claims — they're mutation-tested. Break the ordering contract in the
store and 9 tests go red; strip the in-doubt refusal and 4 go red.

---

## The record-before-execute contract (the whole point)

For every call with key `X`:

```
1. COMMIT (X, ATTEMPTING)      <-- durable "I am about to do it" marker
2. run the real side effect
3. COMMIT (X, SUCCEEDED, result)
```

The committed `ATTEMPTING` marker in step 1 is what converts a *silent duplicate*
into a *detectable* event. Execute-first-record-second is the exact bug we eliminate.

### The honest limitation: `IN_DOUBT`

If the process dies **after** the side effect ran but **before** step 3 commits, we
genuinely cannot know whether it happened. We do **not** guess. The key is marked
`IN_DOUBT`, and the next call with it raises `InDoubtError` rather than blindly
retrying. The error is a **receipt, not a tombstone** — it carries the key, tool
name, timestamp, and args-hash so you (or a reconciliation pass) can go ask the
downstream system what happened, then close it out:

```python
from safeact import InDoubtError, get_store

try:
    charge_card("charge-9", ...)
except InDoubtError as e:
    print(e.key, e.tool, e.attempted_at, e.args_hash)   # go check Stripe

store = get_store("agent.db")
for rec in store.list_in_doubt():
    ...  # a human decides
store.resolve("charge-9", happened=True, result={...})   # or happened=False
```

An honest "we don't know, here's how to check" beats a confident duplicate.

### Known false-positive (documented, not solved)

A *live concurrent* loser and a *crashed* predecessor both look like `ATTEMPTING`
from a single snapshot, so a concurrent loser can also raise `InDoubtError`. A
lease/heartbeat would separate the two — it's an explicit extension point, **not
built**, because building it now is how the scope balloons. Knowing where the edge is
beats pretending it isn't there.

---

## Saga: compensation with automatic rollback

```python
from safeact import Saga

with Saga("refund-8842", store="agent.db") as s:
    s.step(charge_refund, 5000, compensate=reverse_charge)
    s.step(send_receipt, "a@b.com", compensate=send_correction)
    s.step(write_ledger, entry)          # if this raises...
# ...send_receipt and charge_refund are compensated, in reverse order, once each.
```

Step keys are deterministic (`{saga_id}:{index}:{name}`), so re-running a crashed
saga re-attaches to prior progress instead of redoing it, and rollback is idempotent
— run it twice, nothing double-undoes. See `examples/refund_agent.py`.

---

## The entire public API

```python
from safeact import safe, Saga, InDoubtError, SafeError
# also: FailedCallError, SagaFailed, Store/get_store (for reading the ledger)
```

- `@safe(store, compensate=None)` — make one tool idempotent + durable.
- `Saga(saga_id, store)` — context manager; `.step(fn, *args, compensate=fn, **kwargs)`.
- `InDoubtError` — the honest gap state (carries the receipt).
- `SafeError` — base of everything.

**Design note:** a `FAILED` key refuses to re-run by default (`FailedCallError`) —
the library can't know *why* it failed, and re-running either hammers a doomed action
or risks duplicating a partial success. Use a **new key** to genuinely retry.

---

## Run it

```bash
python3.11 -m venv .venv && ./.venv/bin/pip install -e ".[test]"
./.venv/bin/pytest -v          # the crash-simulation suite
./.venv/bin/python examples/refund_agent.py
```

Python 3.11+, standard library only (SQLite is stdlib). `pytest` for the tests.

See [`DESIGN.md`](DESIGN.md) for the full state machine and rationale.

## Deliberately not in this pass

Human-approval gates, a signed receipt format, multi-framework adapters, a web UI,
auth, any network service. Each has a marked extension point in the code. The value
here is doing four things *provably*, not ten things *plausibly*.
