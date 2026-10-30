"""The adapter: a decorator that makes an MCP tool exactly-once + audited.

It composes UNDER `MCPServer.tool()` and preserves the tool's signature (via
`functools.wraps`) so the MCP server still generates the correct input schema.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Optional

from safeact import get_store, HeldForReview, InDoubtError  # re-exported for callers
from safeact.guard import UNKNOWN, hold_if_tainted, taint_params


def safe_tool(
    store,
    name: Optional[str] = None,
    key_arg: str = "idempotency_key",
    *,
    guard=None,
    untrusted=None,
    untrusted_origin: str = UNKNOWN,
) -> Callable:
    """Wrap an MCP tool so it runs at most once per idempotency key.

    The wrapped tool must accept `key_arg` (default ``idempotency_key``) as a
    parameter; the MCP client supplies a stable value. On a repeat key the stored
    result is replayed instead of re-running the side effect. A crash in the
    execute->record gap marks the key in-doubt and the next call raises
    ``InDoubtError`` rather than blindly retrying.

    Every attempt is persisted to `store` — that durable record IS the audit
    trail (read it with ``audit_record`` / list the review queue with ``in_doubt``).

    Provenance gate (safeact.guard): pass ``untrusted=["url", "document"]`` to name
    the parameters an attacker can drive (free text, fetched content, a filename),
    and any value arriving in them is auto-tainted and HELD before the side effect
    runs — raising ``HeldForReview``, recorded durably. ``guard`` may instead/also be
    a full policy (or ``True`` for the default); naming ``untrusted`` params turns the
    default policy on for you.

    # EXTENSION POINT: derive the key from MCP request `_meta` via a Context param,
    # so well-behaved clients don't have to expose it in the tool schema. Not built.
    """
    if guard is True:
        guard = hold_if_tainted()
    untrusted = list(untrusted or [])
    if untrusted and guard is None:
        guard = hold_if_tainted()

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        store_obj = get_store(store)

        def _taint(args, kwargs):
            if not untrusted:
                return args, kwargs
            return taint_params(fn, args, kwargs, untrusted, untrusted_origin)

        def _key(args, kwargs):
            key = kwargs[key_arg] if key_arg in kwargs else (args[0] if args else None)
            if not isinstance(key, str) or not key:
                raise TypeError(
                    f"{tool_name}: '{key_arg}' (the idempotency key) is required and must "
                    f"be a non-empty string. The MCP client should pass a stable value "
                    f"(e.g. the agent's tool-call id) so retries are deduplicated."
                )
            return key

        # Async MCP tool -> await the side effect via the store's async path.
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)  # keep fn's signature so MCP builds the right schema
            async def awrapper(*args, **kwargs):
                key = _key(args, kwargs)
                targs, tkwargs = _taint(args, kwargs)
                return await store_obj.arun(key, fn, targs, tkwargs, fn_name=tool_name, guard=guard)

            return awrapper

        @functools.wraps(fn)  # keep fn's signature so MCP builds the right schema
        def wrapper(*args, **kwargs):
            # store.run executes fn(*args, **kwargs) at most once for this key.
            key = _key(args, kwargs)
            targs, tkwargs = _taint(args, kwargs)
            return store_obj.run(key, fn, targs, tkwargs, fn_name=tool_name, guard=guard)

        return wrapper

    return decorator


def audit_record(store, key: str):
    """The durable audit entry for one call — for a dashboard or a 'prove it' export."""
    return get_store(store).get(key)


def in_doubt(store):
    """Keys stuck in the unrecoverable gap — the human-review queue."""
    return get_store(store).list_in_doubt()


def held(store):
    """Tool calls a guard policy blocked before execution — the review queue."""
    return get_store(store).list_held()
