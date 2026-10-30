"""Make a LangChain tool exactly-once, keyed on its injected tool_call_id.

The idempotency key is LangChain's own `tool_call_id`, captured via
`InjectedToolCallId` — so it's hidden from the LLM schema, and under LangGraph
(which persists tool_call_ids in its checkpoint) a crash-and-resume re-invokes the
tool with the SAME id, so the side effect does not repeat.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Annotated, Callable, Optional

from langchain_core.tools import InjectedToolCallId, StructuredTool

from safeact import get_store, HeldForReview, InDoubtError  # re-exported for callers
from safeact.guard import UNKNOWN, hold_if_tainted, taint_params

_KEY_PARAM = "safeact_tool_call_id"


def safe_tool(
    store,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    guard=None,
    untrusted=None,
    untrusted_origin: str = UNKNOWN,
) -> Callable:
    """Turn a function into a LangChain `StructuredTool` that runs **at most once
    per tool-call**, keyed on the injected `tool_call_id`.

        from safeact_langchain import safe_tool

        @safe_tool(store="agent.db")
        def charge_card(customer: str, amount: int) -> dict:
            stripe.charge(customer, amount)        # the real, irreversible action
            return {"charged": amount}

    The LLM never sees an idempotency key — it's LangChain's own tool-call id. Bind
    the returned tool to your agent / LangGraph `ToolNode` as usual. Works for
    `async def` tools too. Every call is recorded to `store` (the audit trail).

    Provenance gate (safeact.guard): pass ``untrusted=["url", "document"]`` to name
    the parameters an attacker can drive; a value arriving in them is auto-tainted
    and HELD before the side effect runs (``HeldForReview``, recorded durably).
    ``guard`` may instead/also be a policy (or ``True`` for the default); naming
    ``untrusted`` params turns the default policy on for you.
    """
    store_obj = get_store(store)
    if guard is True:
        guard = hold_if_tainted()
    untrusted = list(untrusted or [])
    if untrusted and guard is None:
        guard = hold_if_tainted()

    def decorator(fn: Callable) -> StructuredTool:
        tool_name = name or fn.__name__
        desc = description or (fn.__doc__ or tool_name)

        # new signature = fn's params + an injected (schema-hidden) tool_call_id
        sig = inspect.signature(fn)
        injected = inspect.Parameter(
            _KEY_PARAM,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[str, InjectedToolCallId],
        )
        new_sig = sig.replace(parameters=list(sig.parameters.values()) + [injected])
        new_ann = {**getattr(fn, "__annotations__", {}),
                   _KEY_PARAM: Annotated[str, InjectedToolCallId]}

        def _key(kwargs: dict) -> str:
            key = kwargs.pop(_KEY_PARAM, None)
            if not key:
                raise ValueError(
                    f"{tool_name}: no tool_call_id was injected. Invoke this tool through an "
                    f"agent / LangGraph ToolNode (which supplies the tool-call id) so the call "
                    f"can be deduplicated."
                )
            return key

        def _taint(args, kwargs):
            if not untrusted:
                return args, kwargs
            return taint_params(fn, args, kwargs, untrusted, untrusted_origin)

        if asyncio.iscoroutinefunction(fn):
            async def awrapper(*args, **kwargs):
                key = _key(kwargs)
                targs, tkwargs = _taint(args, kwargs)
                return await store_obj.arun(key, fn, targs, tkwargs, fn_name=tool_name, guard=guard)

            functools.update_wrapper(awrapper, fn)
            awrapper.__signature__ = new_sig          # type: ignore[attr-defined]
            awrapper.__annotations__ = new_ann
            return StructuredTool.from_function(coroutine=awrapper, name=tool_name, description=desc)

        def wrapper(*args, **kwargs):
            key = _key(kwargs)
            targs, tkwargs = _taint(args, kwargs)
            return store_obj.run(key, fn, targs, tkwargs, fn_name=tool_name, guard=guard)

        functools.update_wrapper(wrapper, fn)
        wrapper.__signature__ = new_sig               # type: ignore[attr-defined]
        wrapper.__annotations__ = new_ann
        return StructuredTool.from_function(func=wrapper, name=tool_name, description=desc)

    return decorator


def audit_record(store, key: str):
    """The durable audit entry for one tool-call — for a dashboard / 'prove it'."""
    return get_store(store).get(key)


def in_doubt(store):
    """Tool-call ids stuck in the unrecoverable gap — the human-review queue."""
    return get_store(store).list_in_doubt()


def held(store):
    """Tool calls a guard policy blocked before execution — the review queue."""
    return get_store(store).list_held()
