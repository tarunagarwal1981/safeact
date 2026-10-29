"""The @safe decorator — the entire user-facing win in one wrapper."""

from __future__ import annotations

import asyncio
import functools
from typing import Callable, Optional

from .store import get_store


def safe(store, *, compensate: Optional[Callable] = None, guard=None):
    """
    Make one tool function idempotent and durable.

    The wrapped callable takes the idempotency KEY as its required first positional
    argument, and the underlying function receives it too (as its own first arg) —
    so a tool can forward the same key to a downstream's native idempotency
    mechanism (e.g. Stripe's Idempotency-Key header). Same key seen again -> the
    stored result is returned instead of re-executing.

        @safe(store="agent.db")
        def send_email(key, to, body):   # `key` is both the idempotency key AND
            email.send(to, body)         # available to forward downstream
            return {"sent": True}

        send_email("welcome-42", "a@b.com", "hi")   # runs once
        send_email("welcome-42", "a@b.com", "hi")   # replays stored result

    Works for `async def` tools too — `@safe` detects a coroutine function and
    returns an awaitable wrapper with the same exactly-once guarantee:

        @safe(store="agent.db")
        async def charge(key, customer, amount):
            await stripe.charge(customer, amount)
            return {"ok": True}

        await charge("pay-1", "alice", 50)   # runs once, awaited

    `compensate` registers an undo function used by Saga rollback. It is exposed as
    `.compensate` on the wrapped callable; the decorator itself does not call it.

    `guard` turns on the provenance gate (see safeact.guard). Pass a policy — or
    `guard=True` for the default `hold_if_tainted()` — and a side-effecting call
    whose arguments carry an untrusted origin is HELD before it runs, not executed:

        from safeact.guard import taint, WEB, hold_if_tainted

        @safe(store="agent.db", guard=hold_if_tainted())
        def run_shell(key, cmd):
            subprocess.run(cmd, shell=True)

        run_shell("job-1", taint(page_text, WEB))   # raises HeldForReview, never runs
    """
    store_obj = get_store(store)
    if guard is True:  # convenience: the default policy
        from .guard import hold_if_tainted
        guard = hold_if_tainted()

    def decorator(fn: Callable) -> Callable:
        # Async tool -> awaitable wrapper that awaits the side effect via arun.
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(key, *args, **kwargs):
                return await store_obj.arun(key, fn, (key,) + args, kwargs, fn_name=fn.__name__, guard=guard)

            awrapper.compensate = compensate  # type: ignore[attr-defined]
            awrapper._store = store_obj       # type: ignore[attr-defined]
            awrapper._fn = fn                 # type: ignore[attr-defined]
            return awrapper

        @functools.wraps(fn)
        def wrapper(key, *args, **kwargs):
            # A required positional key: forgetting it fails loudly (store.run checks
            # type), never silently runs unguarded. The key is prepended to the args
            # so the underlying fn receives it as its own first parameter.
            return store_obj.run(key, fn, (key,) + args, kwargs, fn_name=fn.__name__, guard=guard)

        wrapper.compensate = compensate  # type: ignore[attr-defined]
        wrapper._store = store_obj       # type: ignore[attr-defined]
        wrapper._fn = fn                 # type: ignore[attr-defined]
        return wrapper

    return decorator
