"""The @safe decorator — the entire user-facing win in one wrapper."""

from __future__ import annotations

import functools
from typing import Callable, Optional

from .store import get_store


def safe(store, *, compensate: Optional[Callable] = None):
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

    `compensate` registers an undo function used by Saga rollback. It is exposed as
    `.compensate` on the wrapped callable; the decorator itself does not call it.

    # EXTENSION POINT: a human-approval gate would slot in here, before the store
    # claim — mark AWAITING_APPROVAL and block until approved. Not built (DESIGN §7).
    """
    store_obj = get_store(store)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(key, *args, **kwargs):
            # A required positional key: forgetting it fails loudly (store.run checks
            # type), never silently runs unguarded. The key is prepended to the args
            # so the underlying fn receives it as its own first parameter.
            return store_obj.run(key, fn, (key,) + args, kwargs, fn_name=fn.__name__)

        wrapper.compensate = compensate  # type: ignore[attr-defined]
        wrapper._store = store_obj       # type: ignore[attr-defined]
        wrapper._fn = fn                 # type: ignore[attr-defined]
        return wrapper

    return decorator
