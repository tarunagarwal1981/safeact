# DESIGN — safeact

A minimal "safe side-effects" layer for AI agent tools. One decorator (`@safe`),
one context manager (`Saga`), two error types, backed by a single SQLite file.

The whole point is correctness under **crash-and-retry**. This document defines
the exact ordering and state machine that makes duplicate side effects
impossible — and states honestly the one case we cannot solve (in-doubt).

---

## 1. The problem, precisely

An agent calls a tool that has a real side effect (send email, charge a card).
The agent process can die at *any* instruction boundary. On restart it retries.
A naive tool re-runs the side effect → the email is sent twice.

We want: **for a given idempotency key, the side effect executes at most once,
and its result is durably remembered**, even across process death.

The key insight is ordering relative to a durable commit. There are only two
places we can write a record: *before* the side effect and *after* it. We must
do **both**, and the "before" write is what makes duplicates detectable.

---

## 2. Single-call state machine

Every logical call is identified by a caller-supplied **idempotency key** (a
string). The key owns exactly one row in the `calls` table. That row moves
through this machine:

```
                      (no row yet)
                          │
        insert row  ┌─────▼─────┐   unique-constraint violation
        commit  ────┤ ATTEMPTING├───────────────┐
                    └─────┬─────┘                │ (another caller/attempt
                          │                       │  already owns this key)
          run the real    │                       ▼
          side effect      │                 read existing row,
                          │                 act on its state
             ┌────────────┼────────────┐         │
     raised  │            │ returned    │         │
             ▼            │             ▼         │
      record FAILED       │      record SUCCEEDED │
      commit              │      (result blob)    │
             │            │      commit           │
             ▼            │             │          │
        ┌────────┐        │         ┌───▼────┐     │
        │ FAILED │        │         │SUCCEEDED│    │
        └────────┘        │         └────────┘     │
                          │                         │
             ┌────────────▼─────────────┐           │
             │  process died in the gap  │           │
             │  (after side effect, before│          │
             │   SUCCEEDED/FAILED commit) │          │
             └────────────┬─────────────┘           │
                          │ next call sees a stale   │
                          │ ATTEMPTING row  ─────────┘
                          ▼
                     ┌──────────┐
                     │ IN_DOUBT │  → raise InDoubtError
                     └──────────┘
```

### States

| State        | Meaning                                                        |
|--------------|----------------------------------------------------------------|
| `ATTEMPTING` | We committed intent to run, and are (or were) running it now.   |
| `SUCCEEDED`  | Side effect ran, result is stored. Terminal, happy.            |
| `FAILED`     | Side effect raised a normal exception; recorded. Terminal.     |
| `IN_DOUBT`   | An `ATTEMPTING` row was found on a *later* call → the previous attempt died in the gap. We genuinely do not know if the side effect happened. Terminal until a human resolves it. |

### The ordering contract (record-before-execute)

For one `@safe` call with key `X`:

1. **BEGIN** a transaction. `INSERT` a row `(key=X, state=ATTEMPTING)`.
   - If the `INSERT` fails on the UNIQUE(key) constraint, someone already owns
     `X`. Go to step 5 (resolve existing).
   - **COMMIT** this insert. ← durable "I am about to do it" marker.
2. Execute the real side effect (call the wrapped function).
3. On return: `UPDATE` row to `SUCCEEDED`, store the serialized result. **COMMIT**.
   Return the result.
4. On exception: `UPDATE` row to `FAILED`, store the error text. **COMMIT**.
   Re-raise.
5. **Resolve existing row** (key already present):
   - `SUCCEEDED` → return the stored result. (No re-execution. This is the
     idempotency win.)
   - `FAILED` → re-raise a reconstructed error (or, per policy, allow a fresh
     attempt — see §6). Default: re-raise stored failure.
   - `ATTEMPTING` → this is the dangerous one. The row is either (a) a
     concurrent caller running *right now*, or (b) a corpse from a crashed
     attempt. We cannot tell them apart from a single snapshot. We transition
     it to `IN_DOUBT` and raise `InDoubtError`. We NEVER silently re-run.
   - `IN_DOUBT` → raise `InDoubtError` immediately.

**An `IN_DOUBT` record is actionable, not a tombstone.** The row carries enough to
go *ask* the downstream system what happened: the `key`, the tool name (`fn_name`),
the first-attempt timestamp, and an `args_hash`. `InDoubtError` surfaces all of it.
A human (or a later reconciliation pass — not built here, see §7) calls
`store.resolve(key, happened=True|False, result=...)` to close it out: `happened=True`
promotes it to `SUCCEEDED` with the known result, `happened=False` to `FAILED`. The
library's job is to *stop* at the unknown and hand you the receipt — "we don't know,
here's what you need to go check with Stripe" — never to guess.

**Why COMMIT after step 1 matters:** if we skipped it and crashed after the side
effect, the DB would have no memory the attempt ever started → retry re-runs it.
The committed `ATTEMPTING` marker is exactly what turns a silent duplicate into a
loud `IN_DOUBT`.

**Why we can't collapse the gap (step 2→3):** between "side effect returned" and
"COMMIT SUCCEEDED" there is a real window. A crash there leaves the effect done
but unrecorded. No amount of ordering removes this window — it is inherent to
talking to an external system that isn't in our transaction. The honest answer
is `IN_DOUBT`, not a guess. This is the documented limitation.

---

## 3. Concurrency guard

Two threads/processes fire key `X` simultaneously:

- Both try `INSERT (key=X, ATTEMPTING)`.
- SQLite's `UNIQUE(key)` lets exactly **one** win the insert. The loser gets an
  `IntegrityError`.
- The winner executes. The loser goes to the "resolve existing" branch (§2.5).
  While the winner is still running, the loser sees `ATTEMPTING` → raises
  `InDoubtError` (it correctly refuses to run a second copy).

So the unique constraint — not a lock, not a mutex — is the primitive that
guarantees at-most-one execution. This holds across processes because it's the
database enforcing it.

> Trade-off (documented, not hidden): a *concurrent* loser and a *crashed*
> predecessor both present as `ATTEMPTING`, so the loser raises `InDoubtError`
> even though the winner may succeed moments later. For this minimal library we
> choose "refuse and surface" over "block and wait." A future version could add
> a lease/heartbeat so a live winner is distinguishable from a corpse (noted in
> §7).

---

## 4. Saga (compensation / undo)

`Saga` runs an ordered sequence of `@safe` steps. Each step may register a
**compensator** — an undo function. If any step raises, the Saga calls the
compensators of all **completed** steps in **reverse order**.

```
Saga.run:
  completed = []            # stack of (step, args, result)
  for step in steps:
      try:
          result = step(...)        # each step is itself @safe → idempotent
          completed.append((step, result))
      except Exception as e:
          rollback(completed)       # reverse order
          raise SagaFailed(e) from e
  return [r for _, r in completed]

rollback(completed):
  for (step, result) in reversed(completed):
      step.compensate(result)       # each compensator is ALSO @safe-guarded
```

### Compensation must itself be idempotent

Rollback can be interrupted and re-run (crash mid-rollback, or the caller retries
the whole Saga). So each compensator is executed through the *same* store
machinery, keyed by a **derived compensation key** (e.g. `comp:<step-key>`).
Running rollback twice therefore hits `SUCCEEDED` on the second pass and does
nothing — no double-refund.

**Crash *during* rollback (the nasty case).** If the process dies mid-rollback,
resume = re-run the same `with Saga(saga_id): ...` block. The forward steps replay
from the store as no-ops (each returns its stored result without re-executing),
which reconstructs the completed-step list *with results* for free; the failing
step fails again; rollback runs again with the same `comp:` keys, so already-done
compensators are no-ops. Net: each compensator runs **exactly once** across the
crash. If a compensator itself died in its own execute→record gap, its `comp:` key
is `IN_DOUBT` on resume and rollback **halts there and surfaces it** rather than
risking a double-undo — the same honesty rule as forward calls. This is an explicit
crash-test (§8), not just an assertion.

### Step identity

Each step in a Saga needs a stable idempotency key so re-running the whole Saga
(after a crash) re-attaches to prior progress instead of redoing it. The Saga is
given a `saga_id`; step keys are `f"{saga_id}:{step_index}:{step_name}"` and the
compensation key is `f"comp:{step_key}"`. Deterministic keys = safe replay.

---

## 5. Storage schema (SQLite, one file)

```sql
CREATE TABLE IF NOT EXISTS calls (
    key         TEXT PRIMARY KEY,      -- idempotency key (UNIQUE guard)
    state       TEXT NOT NULL,         -- ATTEMPTING|SUCCEEDED|FAILED|IN_DOUBT
    result      TEXT,                  -- JSON-serialized return value (nullable)
    error       TEXT,                  -- error repr for FAILED (nullable)
    fn_name     TEXT,                  -- tool name — part of the in-doubt receipt
    args_hash   TEXT,                  -- sha256(repr(args,kwargs))[:16] — receipt only,
                                       -- NEVER used for keying (see §6 anti-pattern)
    created_at  TEXT NOT NULL,         -- ISO timestamp of first attempt
    updated_at  TEXT NOT NULL          -- ISO timestamp of last state change
);
```

`fn_name` + `args_hash` + `created_at` are what make an `IN_DOUBT` row actionable
(§2). `args_hash` is a best-effort fingerprint for the receipt — it is emphatically
**not** the idempotency key (deriving keys from args is the trap called out in §6).

- `PRIMARY KEY (key)` gives us the UNIQUE guard for free.
- Connection opened with `PRAGMA journal_mode=WAL` and
  `isolation_level=None` (explicit `BEGIN`/`COMMIT`) so we control transaction
  boundaries precisely — the ordering contract depends on knowing exactly when a
  COMMIT lands.
- Result is JSON. Non-JSON-serializable returns raise at record time (loudly),
  rather than silently corrupting state.

---

## 6. Public API (tiny — grasp in 60s)

```python
from safeact import safe, Saga, InDoubtError, SafeError

@safe(store="agent.db")
def send_email(key, to, body):         # KEY IS THE REQUIRED FIRST POSITIONAL ARG
    ...                                 # the real side effect (never sees `key`)

# idempotent:
send_email("welcome-user-42", "a@b.com", "hi")   # runs once
send_email("welcome-user-42", "a@b.com", "hi")   # returns stored result

# saga (keys are generated deterministically from saga_id — you don't pass them):
with Saga("refund-99", store="agent.db") as s:
    s.step(charge_refund, 5000, compensate=reverse_charge)
    s.step(send_receipt, "a@b.com")
    s.step(write_ledger, entry)
# if write_ledger raises → send_receipt, charge_refund compensated in reverse
```

- `@safe` — decorator; params: `store` (db path), optional `compensate=<fn>`.
  The wrapped callable takes the **idempotency key as a required positional first
  argument**; the underlying tool function does not receive it.
- `Saga` — context manager; `.step(fn, *args, compensate=fn, **kwargs)`. Steps are
  plain callables; the Saga owns key generation (§4), so there is no key to forget.
- `InDoubtError` — raised when a key is in the unrecoverable gap state; carries the
  receipt (`.key`, `.tool`, `.args_hash`, `.attempted_at`).
- `SafeError` / `FailedCallError` / `SagaFailed` — base error, refused-retry-of-a-
  FAILED-key, and saga-rollback wrapper.

**Why the key is a mandatory positional, not a `_key` kwarg.** A keyword like
`_key` is easy to omit, visually collides with the tool's real arguments, and a
*forgotten* key silently disables the entire guarantee — the worst failure mode,
because it looks like it's working. A required positional can't run without being
supplied. It's a little ugly at the call site; the ugliness is the point.

**Anti-pattern we deliberately reject: deriving the key from the args.** Two
legitimately-distinct calls with identical args (two real refunds of the same
amount to the same person) would collapse into one execution. The key is the
*caller's explicit statement* that "this is the same logical action" — only they
know that. So the key is loud and mandatory, never inferred.

**FAILED re-attempt policy (default): refuse.** A `FAILED` terminal state re-raises
(as `FailedCallError`) on subsequent same-key calls; a genuine retry needs a *new*
key. Rationale — the library cannot know *why* it failed. If it was "card declined,"
silently re-running the same key just hammers a doomed action. Worse, if the
"failure" was actually a partial success the downstream already recorded, re-running
risks the exact duplicate we exist to prevent. Refuse-by-default hands the decision
back to the human, which is the only correct move when intent is unknowable. (Easy
to flip to "allow retry from FAILED" later, but that is not the default.)

---

## 7. Explicitly out of scope (where they'd plug in later)

Marked with `# EXTENSION POINT:` comments in code where relevant.

- **Human-approval gate** — would sit between §2 step 1 and step 2: mark
  `AWAITING_APPROVAL`, block execution until approved. Not built.
- **Receipt / signature format** — signed proof of a `SUCCEEDED` record for
  cross-service trust. Not built.
- **Lease / heartbeat** — to distinguish a live `ATTEMPTING` winner from a crash
  corpse (§3 trade-off), turning some `IN_DOUBT` cases back into safe waits.
  Not built.
- **Multi-framework adapters, web UI, auth, network service** — none. SQLite
  file only.

---

## 8. What each guarantee's test will look like (preview of tests/)

Every "MUST be correct" guarantee gets a crash-test that fails if the guarantee
is removed:

| Guarantee                     | Test (simulated crash)                                   |
|-------------------------------|----------------------------------------------------------|
| Idempotency across retries    | Counter-backed email; N calls same key → counter == 1.   |
| Record-before-execute         | Reopen DB after a crash *before* execute → no side effect ran, key retryable; after execute → detected. |
| Crash-in-the-gap → in_doubt   | Inject crash between execute and record → key is `IN_DOUBT`, next call raises `InDoubtError`, counter == 1. |
| Concurrent duplicate keys     | Threads fire same key → exactly one execution (counter==1), losers raise. |
| Compensation reverse order    | 3-step saga, step 3 fails → steps 2,1 compensated in that order, once each. |
| Compensation idempotency      | Run rollback twice → compensators fire once each (counter==1). |
| Crash *during* rollback       | Crash mid-rollback, resume by re-running the saga → each compensator ran exactly once; a compensator that died in its own gap lands `IN_DOUBT` and halts rollback (no double-undo). |
| Forgotten key fails loud      | Call the wrapped fn without a key → `TypeError`, never a silent unguarded run. |

Crashes are simulated by raising at the instrumented point, or by dropping and
reopening the SQLite connection to mimic process death — never by mocking the
happy path.
```
