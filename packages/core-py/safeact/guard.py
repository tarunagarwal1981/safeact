"""
safeact.guard — a provenance-aware gate for side-effecting actions.

The exactly-once core answers "did this action happen once, on the record?".
Guard answers the question the 2026 agent breaches turned on: "should this action
have been allowed to happen *at all*, given where its arguments came from?".

The failure these attacks exploit is structural, not a model bug: an agent reads
untrusted content (a planted ``AGENTS.md``, a web page, a tool's output, a form
field) and that content becomes *instructions* that reach a real, irreversible
call — ``run_shell``, ``charge_card``, ``send_email`` — with nothing checking the
*origin* of the data in between. Prompt-injection classifiers guess at wording;
the best open ones catch ~half. Provenance does not guess: it tracks *where each
value came from* and gates on that.

How it works, in three moving parts:

  * ``taint(value, origin)`` — tag a value with where it came from at the point you
    ingest it (``taint(page_text, WEB)``, ``taint(user.amount, USER)``).
  * a *policy* — a callable that, given a call's arguments, returns a ``Decision``
    to allow or HOLD. The default, ``hold_if_tainted()``, holds any side-effecting
    call that received an argument from an untrusted origin.
  * the gate — ``@safe(..., guard=policy)`` evaluates the policy right after the
    idempotency key is claimed and *before* the side effect runs. A held action is
    never executed; the decision (and why) is written to the same SQLite ledger as
    a durable ``HELD`` record, and ``HeldForReview`` is raised.

This module has no dependencies on the rest of safeact (``store`` imports *from*
here, never the other way), so the taint primitives are usable on their own.

v0 scope — honest about the edges:
  * You tag untrusted data at its source; taint rides through to the call arguments
    (including values nested in dicts / lists / tuples / sets). Automatic tagging at
    the MCP / LangChain tool boundary is on the roadmap.
  * A HELD action is *blocked and recorded*. Approve-then-execute (a human approval
    inbox) is the paid "Control" layer, not this.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Iterable

# ---- origins ---------------------------------------------------------------
# Where a value came from. Trusted origins are the ones you control; everything
# else is attacker-reachable and must not silently drive a side effect.

USER = "user"        # typed by the human in the loop
SYSTEM = "system"    # your own code / config / trusted constants
WEB = "web"          # fetched page, search result, scraped content
FILE = "file"        # a file the agent read (incl. planted AGENTS.md / notes)
TOOL = "tool"        # another tool's / agent's output
ENV = "env"          # environment variables, external config at runtime
NETWORK = "network"  # arbitrary network / API response
UNKNOWN = "unknown"  # explicitly-unknown provenance

TRUSTED: frozenset = frozenset({USER, SYSTEM})
#: Origins the default policy treats as attacker-reachable.
UNTRUSTED_DEFAULT: frozenset = frozenset({WEB, FILE, TOOL, ENV, NETWORK, UNKNOWN})

_MAX_DEPTH = 8  # cap on how deep we walk nested containers


class Tainted:
    """
    A value wrapped with its provenance. Transparent to safeact: the store
    unwraps it (``untaint``) before the real side effect runs, so your function
    receives the raw value — never the wrapper.

    Prefer the ``taint()`` helper over constructing this directly.
    """

    __slots__ = ("_safeact_value", "_safeact_origins")

    def __init__(self, value: Any, origins: Iterable[str]):
        self._safeact_value = value
        self._safeact_origins = frozenset(origins)

    @property
    def origins(self) -> frozenset:
        return self._safeact_origins

    @property
    def value(self) -> Any:
        return self._safeact_value

    def __repr__(self) -> str:
        return f"Tainted({self._safeact_value!r}, origins={sorted(self._safeact_origins)})"


def taint(value: Any, origin: str) -> Tainted:
    """
    Tag ``value`` with ``origin``. Tagging an already-tainted value *adds* the
    origin (taint only ever accumulates — it never gets washed off by re-tagging).

        notes  = taint(open("AGENTS.md").read(), FILE)   # untrusted
        amount = taint(user.amount, USER)                # trusted
    """
    if isinstance(value, Tainted):
        return Tainted(value._safeact_value, value._safeact_origins | {origin})
    return Tainted(value, {origin})


def untaint(x: Any, _depth: int = 0) -> Any:
    """
    Strip taint wrappers, returning the raw value with the same shape. Walks into
    dict / list / tuple / set so a tainted value nested inside a container is
    unwrapped too. This is what the store calls right before executing the side
    effect, so your function never sees a ``Tainted``.
    """
    if isinstance(x, Tainted):
        return untaint(x._safeact_value, _depth)
    if _depth >= _MAX_DEPTH:
        return x
    if isinstance(x, dict):
        return {k: untaint(v, _depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [untaint(v, _depth + 1) for v in x]
    if isinstance(x, tuple):
        return tuple(untaint(v, _depth + 1) for v in x)
    if isinstance(x, set):
        return {untaint(v, _depth + 1) for v in x}
    return x


def taint_params(fn: Callable, args: tuple, kwargs: dict, names: Iterable[str], origin: str = UNKNOWN):
    """
    Return ``(args, kwargs)`` with the named parameters of ``fn`` tagged ``origin``,
    resolving positional/keyword binding from ``fn``'s signature. Unknown names are
    ignored. This is how an adapter auto-taints the parameters a tool author declared
    untrusted, without the caller having to wrap each value by hand.
    """
    names = set(names or ())
    if not names:
        return args, kwargs
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        # Signature mismatch (e.g. *args tool) — best effort: tag matching keywords.
        kwargs = {k: (taint(v, origin) if k in names else v) for k, v in kwargs.items()}
        return args, kwargs
    for name in names:
        if name in bound.arguments:
            bound.arguments[name] = taint(bound.arguments[name], origin)
    return tuple(bound.args), dict(bound.kwargs)


def origins_of(x: Any, _depth: int = 0) -> frozenset:
    """All origins carried anywhere within ``x`` (nested containers included)."""
    acc: set = set()
    if isinstance(x, Tainted):
        acc |= set(x._safeact_origins)
        acc |= origins_of(x._safeact_value, _depth)
        return frozenset(acc)
    if _depth >= _MAX_DEPTH:
        return frozenset(acc)
    if isinstance(x, dict):
        for v in x.values():
            acc |= origins_of(v, _depth + 1)
    elif isinstance(x, (list, tuple, set)):
        for v in x:
            acc |= origins_of(v, _depth + 1)
    return frozenset(acc)


# ---- decisions & policies --------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """A policy's verdict on one call. ``hold=True`` blocks the side effect."""

    hold: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> "Decision":
        return cls(False, "")

    @classmethod
    def held(cls, reason: str) -> "Decision":
        return cls(True, reason)


# A policy is: (key, fn_name, args, kwargs) -> Decision
Policy = Callable[[str, "str | None", tuple, dict], Decision]


def _scan(args: tuple, kwargs: dict) -> frozenset:
    found: set = set()
    for a in args:
        found |= origins_of(a)
    for v in kwargs.values():
        found |= origins_of(v)
    return frozenset(found)


def hold_if_tainted(untrusted: Iterable[str] = UNTRUSTED_DEFAULT) -> Policy:
    """
    The default policy: HOLD a side-effecting call if any argument carries an
    untrusted origin. Trusted and untagged arguments pass.

        @safe(store="agent.db", guard=hold_if_tainted())
        def run_shell(key, cmd): ...
    """
    untrusted = frozenset(untrusted)

    def policy(key: str, fn_name: "str | None", args: tuple, kwargs: dict) -> Decision:
        bad = _scan(args, kwargs) & untrusted
        if bad:
            return Decision.held(
                f"{fn_name or 'action'}: argument from untrusted origin "
                f"{sorted(bad)} reached a side-effecting call"
            )
        return Decision.allow()

    return policy


def hold_if(predicate: Callable[[str, "str | None", tuple, dict], bool], reason: str) -> Policy:
    """Build a custom policy from a predicate over the call."""

    def policy(key: str, fn_name: "str | None", args: tuple, kwargs: dict) -> Decision:
        return Decision.held(reason) if predicate(key, fn_name, args, kwargs) else Decision.allow()

    return policy


def allow_all() -> Policy:
    """A no-op policy (useful as an explicit, auditable override)."""

    def policy(key: str, fn_name: "str | None", args: tuple, kwargs: dict) -> Decision:
        return Decision.allow()

    return policy
